// Payload validation for xiao-seat-vision. Mirrors the DB CHECK constraints
// (supabase/migrations) and the Python bridge validation (bridge/validation.py).

export const ID_RE = /^[A-Za-z0-9_-]{1,32}$/;
export const EVENT_ID_RE = /^[A-Za-z0-9_-]{1,128}$/;
export const STATES = ["empty", "occupied", "unknown"] as const;
export const SOURCES = ["camera", "test_video"] as const;
export const MAX_INFERENCE_MS = 600_000;

export type SeatState = typeof STATES[number];
export type Source = typeof SOURCES[number];

export interface SeatEventRow {
  event_id: string;
  device_id: string;
  seat_id: string;
  state: SeatState;
  confidence: number;
  inference_ms: number;
  is_test: boolean;
  source: Source;
  device_ts: string | null;
}

export type Validation<T> = { ok: true; value: T } | {
  ok: false;
  errors: string[];
};

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function optionalTimestamp(
  v: unknown,
  field: string,
  errors: string[],
): string | null {
  if (v === undefined || v === null) return null;
  if (typeof v !== "string" || v.length > 40 || !Number.isFinite(Date.parse(v))) {
    errors.push(`${field} must be an ISO-8601 timestamp`);
    return null;
  }
  return new Date(Date.parse(v)).toISOString();
}

function testFlags(
  body: Obj,
  errors: string[],
): { is_test: boolean; source: Source } {
  const isTest = body.is_test ?? false;
  if (typeof isTest !== "boolean") errors.push("is_test must be a boolean");
  const source = body.source ?? (isTest === true ? "test_video" : "camera");
  if (!SOURCES.includes(source as Source)) {
    errors.push(`source must be one of ${SOURCES.join(", ")}`);
  } else if ((source === "test_video") !== (isTest === true)) {
    errors.push("is_test must be true exactly when source is test_video");
  }
  return { is_test: isTest === true, source: source as Source };
}

export function validateSeatEvent(body: unknown): Validation<SeatEventRow> {
  if (!isObj(body)) return { ok: false, errors: ["body must be a JSON object"] };
  const errors: string[] = [];

  const { event_id, device_id, state, confidence, inference_ms } = body;
  const seat_id = body.seat_id ?? "seat_01"; // pre-multi-seat payloads

  if (typeof device_id !== "string" || !ID_RE.test(device_id)) {
    errors.push("device_id must match ^[A-Za-z0-9_-]{1,32}$");
  }
  if (typeof seat_id !== "string" || !ID_RE.test(seat_id)) {
    errors.push("seat_id must match ^[A-Za-z0-9_-]{1,32}$");
  }
  if (typeof event_id !== "string" || !EVENT_ID_RE.test(event_id)) {
    errors.push("event_id must match ^[A-Za-z0-9_-]{1,128}$");
  } else if (
    typeof device_id === "string" && !event_id.startsWith(`${device_id}-`)
  ) {
    errors.push("event_id must start with '<device_id>-'");
  }
  if (!STATES.includes(state as SeatState)) {
    errors.push(`state must be one of ${STATES.join(", ")}`);
  }
  if (
    typeof confidence !== "number" || !Number.isFinite(confidence) ||
    confidence < 0 || confidence > 1
  ) {
    errors.push("confidence must be a number between 0 and 1");
  }
  if (
    typeof inference_ms !== "number" || !Number.isInteger(inference_ms) ||
    inference_ms < 0 || inference_ms > MAX_INFERENCE_MS
  ) {
    errors.push(`inference_ms must be an integer between 0 and ${MAX_INFERENCE_MS}`);
  }
  const flags = testFlags(body, errors);
  const device_ts = optionalTimestamp(body.timestamp, "timestamp", errors);

  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    value: {
      event_id: event_id as string,
      device_id: device_id as string,
      seat_id: seat_id as string,
      state: state as SeatState,
      confidence: confidence as number,
      inference_ms: inference_ms as number,
      is_test: flags.is_test,
      source: flags.source,
      device_ts,
    },
  };
}

// ------------------------------------------------------------ device status
export interface DeviceStatusInput {
  device_id: string;
  kind: "status" | "telemetry";
  status: "online" | "offline" | null;
  telemetry: Obj | null;
  device_ts: string | null;
}

export function validateDeviceStatus(body: unknown): Validation<DeviceStatusInput> {
  if (!isObj(body)) return { ok: false, errors: ["body must be a JSON object"] };
  const errors: string[] = [];
  const { device_id, kind, payload } = body;
  if (typeof device_id !== "string" || !ID_RE.test(device_id)) {
    errors.push("device_id must match ^[A-Za-z0-9_-]{1,32}$");
  }
  if (kind !== "status" && kind !== "telemetry") {
    errors.push("kind must be status or telemetry");
  }
  if (!isObj(payload)) errors.push("payload must be an object");
  let status: "online" | "offline" | null = null;
  let telemetry: Obj | null = null;
  if (isObj(payload)) {
    if (payload.device_id !== device_id) {
      errors.push("payload.device_id must equal device_id");
    }
    if (kind === "status") {
      if (payload.status !== "online" && payload.status !== "offline") {
        errors.push("payload.status must be online or offline");
      } else {
        status = payload.status;
      }
    } else {
      if (JSON.stringify(payload).length > 12_000) {
        errors.push("telemetry payload too large");
      }
      telemetry = payload;
    }
  }
  const device_ts = isObj(payload)
    ? optionalTimestamp(payload.timestamp, "payload.timestamp", errors)
    : null;
  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    value: {
      device_id: device_id as string,
      kind: kind as "status" | "telemetry",
      status,
      telemetry,
      device_ts,
    },
  };
}

// ----------------------------------------------------------------- snapshot
export const MAX_SNAPSHOT_JPEG_BYTES = 256 * 1024;

export interface SnapshotInput {
  snapshot_id: string;
  device_id: string;
  reason: "state_change" | "periodic";
  is_test: boolean;
  source: Source;
  width: number;
  height: number;
  seats: unknown[];
  detections: unknown[];
  device_ts: string | null;
  jpeg: Uint8Array;
}

function decodeBase64(b64: string): Uint8Array | null {
  try {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

export function validateSnapshot(body: unknown): Validation<SnapshotInput> {
  if (!isObj(body)) return { ok: false, errors: ["body must be a JSON object"] };
  const errors: string[] = [];
  const { snapshot_id, device_id, reason, width, height, image_b64 } = body;
  if (typeof device_id !== "string" || !ID_RE.test(device_id)) {
    errors.push("device_id must match ^[A-Za-z0-9_-]{1,32}$");
  }
  if (typeof snapshot_id !== "string" || !EVENT_ID_RE.test(snapshot_id)) {
    errors.push("snapshot_id must match ^[A-Za-z0-9_-]{1,128}$");
  } else if (
    typeof device_id === "string" && !snapshot_id.startsWith(`${device_id}-`)
  ) {
    errors.push("snapshot_id must start with '<device_id>-'");
  }
  if (reason !== "state_change" && reason !== "periodic") {
    errors.push("reason must be state_change or periodic");
  }
  for (const [name, v] of [["width", width], ["height", height]] as const) {
    if (typeof v !== "number" || !Number.isInteger(v) || v < 1 || v > 1920) {
      errors.push(`${name} must be an integer between 1 and 1920`);
    }
  }
  const seats = body.seats ?? [];
  const detections = body.detections ?? [];
  if (!Array.isArray(seats) || seats.length > 32) {
    errors.push("seats must be an array (max 32)");
  }
  if (!Array.isArray(detections) || detections.length > 64) {
    errors.push("detections must be an array (max 64)");
  }
  const flags = testFlags(body, errors);
  const device_ts = optionalTimestamp(body.timestamp, "timestamp", errors);

  let jpeg: Uint8Array | null = null;
  if (typeof image_b64 !== "string" || image_b64.length === 0) {
    errors.push("image_b64 must be a non-empty base64 string");
  } else if (image_b64.length > Math.ceil(MAX_SNAPSHOT_JPEG_BYTES / 3) * 4) {
    errors.push("image too large");
  } else {
    jpeg = decodeBase64(image_b64);
    if (!jpeg) errors.push("image_b64 is not valid base64");
    else if (jpeg.length < 4 || jpeg[0] !== 0xff || jpeg[1] !== 0xd8) {
      errors.push("image is not a JPEG");
    }
  }

  if (errors.length > 0 || !jpeg) return { ok: false, errors };
  return {
    ok: true,
    value: {
      snapshot_id: snapshot_id as string,
      device_id: device_id as string,
      reason: reason as "state_change" | "periodic",
      is_test: flags.is_test,
      source: flags.source,
      width: width as number,
      height: height as number,
      seats: seats as unknown[],
      detections: detections as unknown[],
      device_ts,
      jpeg,
    },
  };
}

// ------------------------------------------------------------------- layout
export interface LayoutSeat {
  seat_id: string;
  label: string;
  roi: { x: number; y: number; width: number; height: number };
}

export function validateLayout(
  body: unknown,
): Validation<{ device_id: string; seats: LayoutSeat[] }> {
  if (!isObj(body)) return { ok: false, errors: ["body must be a JSON object"] };
  const errors: string[] = [];
  const { device_id, seats } = body;
  if (typeof device_id !== "string" || !ID_RE.test(device_id)) {
    errors.push("device_id must match ^[A-Za-z0-9_-]{1,32}$");
  }
  if (!Array.isArray(seats) || seats.length < 1 || seats.length > 32) {
    errors.push("seats must be an array of 1..32 seats");
    return { ok: false, errors };
  }
  const ids = new Set<string>();
  seats.forEach((s, i) => {
    if (!isObj(s)) {
      errors.push(`seats[${i}] must be an object`);
      return;
    }
    if (typeof s.seat_id !== "string" || !ID_RE.test(s.seat_id)) {
      errors.push(`seats[${i}].seat_id invalid`);
    } else if (ids.has(s.seat_id)) {
      errors.push(`seats[${i}].seat_id duplicated`);
    } else {
      ids.add(s.seat_id);
    }
    if (typeof s.label !== "string" || s.label.length < 1 || s.label.length > 64) {
      errors.push(`seats[${i}].label must be 1..64 chars`);
    }
    const r = s.roi;
    const nums = isObj(r) ? [r.x, r.y, r.width, r.height] : [];
    if (
      nums.length !== 4 || !nums.every((n) => typeof n === "number" && Number.isFinite(n))
    ) {
      errors.push(`seats[${i}].roi needs numeric x, y, width, height`);
    } else {
      const [x, y, w, h] = nums as number[];
      if (x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > 1.0001 || y + h > 1.0001) {
        errors.push(`seats[${i}].roi must lie within 0..1`);
      }
    }
  });
  if (errors.length > 0) return { ok: false, errors };
  return {
    ok: true,
    value: { device_id: device_id as string, seats: seats as LayoutSeat[] },
  };
}
