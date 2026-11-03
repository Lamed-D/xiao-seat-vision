// Builds the dashboard views from raw DB rows (pure; unit-tested).
//
// summary : what a normal user needs (counts, seat states, snapshot URL, ROI only
//           for placing badges). No confidence, timing, memory or raw JSON.
// debug   : everything for developer mode (admin token only).

import type { DashboardData } from "./store.ts";

export const DEVICE_STALE_MS = 3 * 60 * 1000;

export interface Roi {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface SummarySeat {
  device_id: string;
  seat_id: string;
  label: string;
  state: "empty" | "occupied" | "unknown";
  stale: boolean;
  updated_at: string | null;
  roi: Roi | null;
}

export interface Summary {
  generated_at: string;
  counts: { total: number; occupied: number; empty: number; unknown: number };
  seats: SummarySeat[];
  devices: { device_id: string; online: boolean; last_seen_at: string }[];
  snapshot:
    | { url: string; device_id: string; width: number; height: number; created_at: string }
    | null;
}

type Obj = Record<string, unknown>;

function telemetrySeats(t: Obj | null): Obj[] {
  const seats = t?.seats;
  return Array.isArray(seats)
    ? seats.filter((s): s is Obj => typeof s === "object" && s !== null)
    : [];
}

function asRoi(v: unknown): Roi | null {
  if (typeof v !== "object" || v === null) return null;
  const r = v as Obj;
  const nums = [r.x, r.y, r.width, r.height];
  if (!nums.every((n) => typeof n === "number" && Number.isFinite(n))) return null;
  return {
    x: r.x as number,
    y: r.y as number,
    width: r.width as number,
    height: r.height as number,
  };
}

export function buildSummary(data: DashboardData, now: Date = new Date()): Summary {
  const seats = new Map<string, SummarySeat>();
  const key = (d: string, s: string) => `${d}/${s}`;
  const deviceOnline = new Map<string, boolean>();

  for (const d of data.devices) {
    const fresh = now.getTime() - Date.parse(d.last_seen_at) < DEVICE_STALE_MS;
    deviceOnline.set(d.device_id, d.status !== "offline" && fresh);
    // Device-reported ROIs are what the firmware really uses.
    for (const s of telemetrySeats(d.telemetry)) {
      if (typeof s.seat_id !== "string") continue;
      seats.set(key(d.device_id, s.seat_id), {
        device_id: d.device_id,
        seat_id: s.seat_id,
        label: typeof s.label === "string" ? s.label : s.seat_id,
        state: "unknown",
        stale: true,
        updated_at: null,
        roi: asRoi(s.roi),
      });
    }
  }

  // Dashboard-edited layout overrides labels/ROIs for display.
  const sorted = [...data.layouts].sort((a, b) => a.sort_order - b.sort_order);
  for (const l of sorted) {
    const k = key(l.device_id, l.seat_id);
    const existing = seats.get(k);
    seats.set(k, {
      device_id: l.device_id,
      seat_id: l.seat_id,
      label: l.label,
      state: existing?.state ?? "unknown",
      stale: existing?.stale ?? true,
      updated_at: existing?.updated_at ?? null,
      roi: { x: l.roi_x, y: l.roi_y, width: l.roi_width, height: l.roi_height },
    });
  }

  for (const c of data.currentStates) {
    const k = key(c.device_id, c.seat_id);
    const online = deviceOnline.get(c.device_id) ?? false;
    const existing = seats.get(k);
    const state = c.state === "empty" || c.state === "occupied" ? c.state : "unknown";
    seats.set(k, {
      device_id: c.device_id,
      seat_id: c.seat_id,
      label: existing?.label ?? c.seat_id,
      // Old data from an offline/silent device is not trusted.
      state: online ? state : "unknown",
      stale: !online,
      updated_at: c.created_at,
      roi: existing?.roi ?? null,
    });
  }

  const list = [...seats.values()].sort((a, b) =>
    a.device_id === b.device_id
      ? a.seat_id.localeCompare(b.seat_id)
      : a.device_id.localeCompare(b.device_id)
  );
  const counts = { total: list.length, occupied: 0, empty: 0, unknown: 0 };
  for (const s of list) counts[s.state]++;

  const snap = data.latestSnapshot;
  return {
    generated_at: now.toISOString(),
    counts,
    seats: list,
    devices: data.devices.map((d) => ({
      device_id: d.device_id,
      online: deviceOnline.get(d.device_id) ?? false,
      last_seen_at: d.last_seen_at,
    })),
    snapshot: snap && data.snapshotUrl
      ? {
        url: data.snapshotUrl,
        device_id: snap.device_id,
        width: snap.width,
        height: snap.height,
        created_at: snap.created_at,
      }
      : null,
  };
}

export function buildDebug(data: DashboardData, recentEvents: unknown[]) {
  return {
    current_states: data.currentStates,
    devices: data.devices.map((d) => ({
      device_id: d.device_id,
      status: d.status,
      status_changed_at: d.status_changed_at,
      last_seen_at: d.last_seen_at,
      telemetry_at: d.telemetry_at,
      telemetry: d.telemetry,
    })),
    latest_snapshot: data.latestSnapshot
      ? {
        snapshot_id: data.latestSnapshot.snapshot_id,
        reason: data.latestSnapshot.reason,
        seats: data.latestSnapshot.seats,
        detections: data.latestSnapshot.detections,
        device_ts: data.latestSnapshot.device_ts,
        created_at: data.latestSnapshot.created_at,
      }
      : null,
    recent_events: recentEvents,
  };
}
