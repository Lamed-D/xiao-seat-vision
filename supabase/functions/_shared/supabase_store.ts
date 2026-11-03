// Store implementation backed by Supabase (service role key; bypasses RLS).
// SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are injected by the Edge Runtime.

import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2.45.4";
import { CONSTRAINT_CODES, ConstraintError } from "./http.ts";
import type {
  CurrentStateRow,
  DashboardData,
  DeviceRow,
  InsertOutcome,
  LayoutRow,
  SnapshotRow,
  Store,
} from "./store.ts";
import type { DeviceStatusInput, LayoutSeat, SeatEventRow, SnapshotInput } from "./validation.ts";

export const SNAPSHOT_BUCKET = "snapshots";
const SIGNED_URL_SECONDS = 120;

type PgError = { code?: string; message: string } | null;

function check(error: PgError): void {
  if (!error) return;
  if (error.code && CONSTRAINT_CODES.has(error.code)) {
    throw new ConstraintError(error.message);
  }
  throw new Error(error.message);
}

export function createSupabaseStoreFromEnv(): Store | null {
  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) return null; // names only are logged by callers, never values
  return new SupabaseStore(
    createClient(url, key, {
      auth: { persistSession: false, autoRefreshToken: false },
    }),
  );
}

export class SupabaseStore implements Store {
  constructor(private readonly db: SupabaseClient) {}

  async insertSeatEvent(row: SeatEventRow): Promise<InsertOutcome> {
    // INSERT ... ON CONFLICT (event_id) DO NOTHING RETURNING id
    const { data, error } = await this.db
      .from("seat_events")
      .upsert(row, { onConflict: "event_id", ignoreDuplicates: true })
      .select("id");
    check(error);
    return data && data.length > 0 ? "created" : "duplicate";
  }

  async upsertDeviceStatus(input: DeviceStatusInput): Promise<void> {
    const now = new Date().toISOString();
    const row: Record<string, unknown> = {
      device_id: input.device_id,
      last_seen_at: now,
    };
    if (input.kind === "status") {
      row.status = input.status;
      row.status_changed_at = now;
    } else {
      row.telemetry = input.telemetry;
      row.telemetry_at = input.device_ts ?? now;
      row.status = "online"; // telemetry only arrives from a connected device
    }
    const { error } = await this.db
      .from("device_status")
      .upsert(row, { onConflict: "device_id" });
    check(error);
  }

  async saveSnapshot(input: SnapshotInput): Promise<InsertOutcome> {
    const existing = await this.db
      .from("seat_snapshots")
      .select("id")
      .eq("snapshot_id", input.snapshot_id)
      .limit(1);
    check(existing.error);
    if (existing.data && existing.data.length > 0) return "duplicate";

    const day = (input.device_ts ?? new Date().toISOString()).slice(0, 10);
    const path = `${input.device_id}/${day}/${input.snapshot_id}.jpg`;
    const upload = await this.db.storage
      .from(SNAPSHOT_BUCKET)
      .upload(path, input.jpeg, { contentType: "image/jpeg", upsert: false });
    // A previous attempt may have uploaded the object before failing to insert the row.
    if (upload.error && !/exist|duplicate/i.test(upload.error.message)) {
      throw new Error(`storage upload failed: ${upload.error.message}`);
    }

    const { data, error } = await this.db
      .from("seat_snapshots")
      .upsert({
        snapshot_id: input.snapshot_id,
        device_id: input.device_id,
        storage_path: path,
        reason: input.reason,
        is_test: input.is_test,
        source: input.source,
        width: input.width,
        height: input.height,
        seats: input.seats,
        detections: input.detections,
        device_ts: input.device_ts,
      }, { onConflict: "snapshot_id", ignoreDuplicates: true })
      .select("id");
    check(error);
    return data && data.length > 0 ? "created" : "duplicate";
  }

  async loadDashboard(isTest: boolean): Promise<DashboardData> {
    const [current, devices, layouts, snaps] = await Promise.all([
      this.db.from("seat_current_state").select("*").eq("is_test", isTest),
      this.db.from("device_status").select("*"),
      this.db.from("seat_layouts").select("*"),
      this.db
        .from("seat_snapshots")
        .select(
          "snapshot_id,device_id,storage_path,reason,width,height,seats,detections,device_ts,created_at",
        )
        .eq("is_test", isTest)
        .order("created_at", { ascending: false })
        .limit(1),
    ]);
    for (const r of [current, devices, layouts, snaps]) check(r.error);

    const latest = (snaps.data?.[0] ?? null) as SnapshotRow | null;
    let snapshotUrl: string | null = null;
    if (latest) {
      const signed = await this.db.storage
        .from(SNAPSHOT_BUCKET)
        .createSignedUrl(latest.storage_path, SIGNED_URL_SECONDS);
      snapshotUrl = signed.data?.signedUrl ?? null;
    }
    return {
      currentStates: (current.data ?? []) as CurrentStateRow[],
      devices: (devices.data ?? []) as DeviceRow[],
      layouts: (layouts.data ?? []) as LayoutRow[],
      latestSnapshot: latest,
      snapshotUrl,
    };
  }

  async recentEvents(isTest: boolean, limit: number): Promise<unknown[]> {
    const { data, error } = await this.db
      .from("seat_events")
      .select("*")
      .eq("is_test", isTest)
      .order("created_at", { ascending: false })
      .limit(limit);
    check(error);
    return data ?? [];
  }

  async replaceLayout(deviceId: string, seats: LayoutSeat[]): Promise<void> {
    const rows = seats.map((s, i) => ({
      device_id: deviceId,
      seat_id: s.seat_id,
      label: s.label,
      roi_x: s.roi.x,
      roi_y: s.roi.y,
      roi_width: s.roi.width,
      roi_height: s.roi.height,
      sort_order: i,
      updated_at: new Date().toISOString(),
    }));
    const up = await this.db
      .from("seat_layouts")
      .upsert(rows, { onConflict: "device_id,seat_id" });
    check(up.error);
    const keep = seats.map((s) => s.seat_id);
    const del = await this.db
      .from("seat_layouts")
      .delete()
      .eq("device_id", deviceId)
      .not("seat_id", "in", `(${keep.join(",")})`);
    check(del.error);
  }
}
