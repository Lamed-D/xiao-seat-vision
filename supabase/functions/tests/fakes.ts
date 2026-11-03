// In-memory Store fake: no Supabase project or secrets needed for tests.
import { ConstraintError } from "../_shared/http.ts";
import type { DashboardData, InsertOutcome, Store } from "../_shared/store.ts";
import type {
  DeviceStatusInput,
  LayoutSeat,
  SeatEventRow,
  SnapshotInput,
} from "../_shared/validation.ts";

export const TOKEN = "t".repeat(40);
export const ADMIN = "a".repeat(40);
export const VIEWER = "v".repeat(40);

export class FakeStore implements Store {
  events = new Map<string, SeatEventRow>();
  statuses: DeviceStatusInput[] = [];
  snapshots = new Map<string, SnapshotInput>();
  layouts = new Map<string, LayoutSeat[]>();
  dashboard: DashboardData = {
    currentStates: [],
    devices: [],
    layouts: [],
    latestSnapshot: null,
    snapshotUrl: null,
  };
  failWith: Error | null = null;

  insertSeatEvent(row: SeatEventRow): Promise<InsertOutcome> {
    if (this.failWith) return Promise.reject(this.failWith);
    if (this.events.has(row.event_id)) return Promise.resolve("duplicate");
    this.events.set(row.event_id, row);
    return Promise.resolve("created");
  }
  upsertDeviceStatus(input: DeviceStatusInput): Promise<void> {
    if (this.failWith) return Promise.reject(this.failWith);
    this.statuses.push(input);
    return Promise.resolve();
  }
  saveSnapshot(input: SnapshotInput): Promise<InsertOutcome> {
    if (this.snapshots.has(input.snapshot_id)) return Promise.resolve("duplicate");
    this.snapshots.set(input.snapshot_id, input);
    return Promise.resolve("created");
  }
  loadDashboard(_isTest: boolean): Promise<DashboardData> {
    return Promise.resolve(this.dashboard);
  }
  recentEvents(_isTest: boolean, _limit: number): Promise<unknown[]> {
    return Promise.resolve([...this.events.values()]);
  }
  replaceLayout(deviceId: string, seats: LayoutSeat[]): Promise<void> {
    this.layouts.set(deviceId, seats);
    return Promise.resolve();
  }
}

export { ConstraintError };

export function post(body: unknown, token: string | null = TOKEN, raw?: string): Request {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (token) headers["x-bridge-token"] = token;
  return new Request("http://localhost/fn", {
    method: "POST",
    headers,
    body: raw ?? JSON.stringify(body),
  });
}

export const validEvent = {
  event_id: "xiao-01-1a2b3c4d-000001",
  device_id: "xiao-01",
  seat_id: "seat_02",
  state: "occupied",
  confidence: 0.95,
  inference_ms: 180,
};
