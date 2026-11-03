// Storage interface used by the handlers. The real implementation lives in
// supabase_store.ts (supabase-js, service role); tests inject fakes.

import type { DeviceStatusInput, LayoutSeat, SeatEventRow, SnapshotInput } from "./validation.ts";

export type InsertOutcome = "created" | "duplicate";

export interface CurrentStateRow {
  device_id: string;
  seat_id: string;
  is_test: boolean;
  state: string;
  confidence: number;
  inference_ms: number;
  event_id: string;
  device_ts: string | null;
  created_at: string;
}

export interface DeviceRow {
  device_id: string;
  status: string;
  status_changed_at: string | null;
  last_seen_at: string;
  telemetry: Record<string, unknown> | null;
  telemetry_at: string | null;
}

export interface LayoutRow {
  device_id: string;
  seat_id: string;
  label: string;
  roi_x: number;
  roi_y: number;
  roi_width: number;
  roi_height: number;
  sort_order: number;
}

export interface SnapshotRow {
  snapshot_id: string;
  device_id: string;
  storage_path: string;
  reason: string;
  width: number;
  height: number;
  seats: unknown[];
  detections: unknown[];
  device_ts: string | null;
  created_at: string;
}

export interface DashboardData {
  currentStates: CurrentStateRow[];
  devices: DeviceRow[];
  layouts: LayoutRow[];
  latestSnapshot: SnapshotRow | null;
  snapshotUrl: string | null;
}

export interface Store {
  insertSeatEvent(row: SeatEventRow): Promise<InsertOutcome>;
  upsertDeviceStatus(input: DeviceStatusInput): Promise<void>;
  saveSnapshot(input: SnapshotInput): Promise<InsertOutcome>;
  loadDashboard(isTest: boolean): Promise<DashboardData>;
  recentEvents(isTest: boolean, limit: number): Promise<unknown[]>;
  replaceLayout(deviceId: string, seats: LayoutSeat[]): Promise<void>;
}
