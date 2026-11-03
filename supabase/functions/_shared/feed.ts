// "Sit-down" event feed for the dashboard (pure; unit-tested).
//
// Every time a seat becomes occupied the device publishes a seat event and (rate-limited)
// a snapshot. They arrive separately, so they are paired here by device and time.

import type { Roi } from "./summary.ts";

export const MATCH_WINDOW_MS = 30_000;
export const CROP = { width: 0.3, height: 0.6 };

export interface FeedEventRow {
  event_id: string;
  device_id: string;
  seat_id: string;
  state: string;
  confidence: number;
  created_at: string;
}

export interface FeedSnapshotRow {
  snapshot_id: string;
  device_id: string;
  created_at: string;
  width: number;
  height: number;
  seats: unknown[];
  detections: unknown[];
  url: string | null; // signed URL
}

export interface FeedItem {
  event_id: string;
  device_id: string;
  seat_id: string;
  seat_label: string;
  created_at: string;
  snapshot: { url: string; width: number; height: number; created_at: string } | null;
  /** Window around the person on this seat in the snapshot (normalized), if found. */
  person_crop: Roi | null;
  confidence?: number; // admin only
}

export interface Feed {
  generated_at: string;
  since: string;
  sit_downs: { total: number; per_seat: Record<string, number> };
  events: FeedItem[];
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null;
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function seatInfo(
  snapshot: FeedSnapshotRow | null,
  seatId: string,
): { label?: string; roi?: Roi; state?: string } {
  for (const s of snapshot?.seats ?? []) {
    if (!isObj(s) || s.seat_id !== seatId) continue;
    const r = isObj(s.roi) ? s.roi : null;
    const roi = r && [r.x, r.y, r.width, r.height].every((n) => num(n) !== null)
      ? { x: r.x as number, y: r.y as number, width: r.width as number, height: r.height as number }
      : undefined;
    return {
      label: typeof s.label === "string" ? s.label : undefined,
      roi,
      state: typeof s.state === "string" ? s.state : undefined,
    };
  }
  return {};
}

/** Detection centre inside the seat ROI with the highest confidence -> crop window. */
export function personCrop(snapshot: FeedSnapshotRow, roi: Roi | undefined): Roi | null {
  if (!roi) return null;
  let best: { cx: number; cy: number; c: number } | null = null;
  for (const d of snapshot.detections ?? []) {
    if (!isObj(d)) continue;
    const cx = num(d.cx) ?? ((num(d.x) ?? NaN) + (num(d.width) ?? NaN) / 2);
    const cy = num(d.cy) ?? ((num(d.y) ?? NaN) + (num(d.height) ?? NaN) / 2);
    if (!Number.isFinite(cx) || !Number.isFinite(cy)) continue;
    if (cx < roi.x || cx > roi.x + roi.width || cy < roi.y || cy > roi.y + roi.height) continue;
    const c = num(d.confidence) ?? 0;
    if (!best || c > best.c) best = { cx, cy, c };
  }
  if (!best) return null;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  return {
    x: clamp(Math.min(best.cx - CROP.width / 2, 1 - CROP.width)),
    y: clamp(Math.min(best.cy - CROP.height / 2, 1 - CROP.height)),
    width: CROP.width,
    height: CROP.height,
  };
}

/** Nearest snapshot of the same device within the window; prefers ones that show the seat occupied. */
export function matchSnapshot(
  ev: FeedEventRow,
  snapshots: FeedSnapshotRow[],
): FeedSnapshotRow | null {
  const t = Date.parse(ev.created_at);
  let best: FeedSnapshotRow | null = null;
  let bestScore = Infinity;
  for (const s of snapshots) {
    if (s.device_id !== ev.device_id || !s.url) continue;
    const dt = Math.abs(Date.parse(s.created_at) - t);
    if (dt > MATCH_WINDOW_MS) continue;
    const shows = seatInfo(s, ev.seat_id).state === "occupied";
    const score = dt + (shows ? 0 : MATCH_WINDOW_MS); // occupied snapshots always win
    if (score < bestScore) {
      best = s;
      bestScore = score;
    }
  }
  return best;
}

export function buildFeed(
  events: FeedEventRow[],
  snapshots: FeedSnapshotRow[],
  todayEvents: { seat_id: string }[],
  since: string,
  labels: Map<string, string>,
  isAdmin: boolean,
  now: Date = new Date(),
): Feed {
  const perSeat: Record<string, number> = {};
  for (const e of todayEvents) perSeat[e.seat_id] = (perSeat[e.seat_id] ?? 0) + 1;

  const items = events
    .filter((e) => e.state === "occupied")
    .map((e) => {
      const snap = matchSnapshot(e, snapshots);
      const info = seatInfo(snap, e.seat_id);
      const item: FeedItem = {
        event_id: e.event_id,
        device_id: e.device_id,
        seat_id: e.seat_id,
        seat_label: labels.get(`${e.device_id}/${e.seat_id}`) ?? info.label ?? e.seat_id,
        created_at: e.created_at,
        snapshot: snap && snap.url
          ? { url: snap.url, width: snap.width, height: snap.height, created_at: snap.created_at }
          : null,
        person_crop: snap ? personCrop(snap, info.roi) : null,
      };
      if (isAdmin) item.confidence = e.confidence;
      return item;
    });

  return {
    generated_at: now.toISOString(),
    since,
    sit_downs: { total: todayEvents.length, per_seat: perSeat },
    events: items,
  };
}
