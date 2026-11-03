import { assert, assertEquals } from "jsr:@std/assert@1.0.13";
import { createDashboardHandler } from "../dashboard-api/handler.ts";
import { buildPeople, buildSummary } from "../_shared/summary.ts";
import type { DashboardData } from "../_shared/store.ts";
import { ADMIN, FakeStore, VIEWER } from "./fakes.ts";

const NOW = new Date("2026-11-03T10:00:00Z");

function data(): DashboardData {
  return {
    devices: [{
      device_id: "xiao-01",
      status: "online",
      status_changed_at: null,
      last_seen_at: "2026-11-03T09:59:30Z",
      telemetry_at: "2026-11-03T09:59:30Z",
      telemetry: {
        memory: { heap_free: 123 },
        seats: [
          { seat_id: "seat_01", label: "Seat 1", roi: { x: 0.1, y: 0.2, width: 0.2, height: 0.5 } },
          { seat_id: "seat_02", label: "Seat 2", roi: { x: 0.4, y: 0.2, width: 0.2, height: 0.5 } },
          { seat_id: "seat_03", label: "Seat 3", roi: { x: 0.7, y: 0.2, width: 0.2, height: 0.5 } },
        ],
      },
    }],
    currentStates: [
      {
        device_id: "xiao-01",
        seat_id: "seat_01",
        is_test: false,
        state: "occupied",
        confidence: 0.91,
        inference_ms: 120,
        event_id: "xiao-01-a-1",
        device_ts: null,
        created_at: "2026-11-03T09:58:00Z",
      },
      {
        device_id: "xiao-01",
        seat_id: "seat_02",
        is_test: false,
        state: "empty",
        confidence: 1,
        inference_ms: 120,
        event_id: "xiao-01-a-2",
        device_ts: null,
        created_at: "2026-11-03T09:58:00Z",
      },
    ],
    layouts: [],
    latestSnapshot: null,
    snapshotUrl: null,
  };
}

function req(view: string, token: string | null, method = "GET", body?: unknown): Request {
  const headers: Record<string, string> = { origin: "http://localhost:8080" };
  if (token) headers["x-dashboard-token"] = token;
  if (body) headers["content-type"] = "application/json";
  return new Request(`http://x/dashboard-api?view=${view}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
}

function handler(store = new FakeStore()) {
  store.dashboard = data();
  return {
    store,
    h: createDashboardHandler({
      viewerTokens: [VIEWER],
      adminTokens: [ADMIN],
      allowedOrigins: ["http://localhost:8080"],
      store,
      now: () => NOW,
    }),
  };
}

Deno.test("summary: counts per seat, seat without events is unknown", () => {
  const s = buildSummary(data(), NOW);
  assertEquals(s.counts, { total: 3, occupied: 1, empty: 1, unknown: 1 });
  assertEquals(s.seats.map((x) => x.state), ["occupied", "empty", "unknown"]);
  assertEquals(s.seats[0].roi, { x: 0.1, y: 0.2, width: 0.2, height: 0.5 });
});

Deno.test("summary: stale/offline device turns all its seats unknown", () => {
  const d = data();
  d.devices[0].last_seen_at = "2026-11-03T09:00:00Z";
  const s = buildSummary(d, NOW);
  assertEquals(s.counts.unknown, 3);
  assert(s.seats.every((x) => x.stale));
  const off = data();
  off.devices[0].status = "offline";
  assertEquals(buildSummary(off, NOW).counts.unknown, 3);
});

Deno.test("summary payload hides technical data (developer mode OFF view)", async () => {
  const { h } = handler();
  const r = await h(req("summary", VIEWER));
  assertEquals(r.status, 200);
  const text = await r.text();
  for (const hidden of ["confidence", "inference_ms", "heap_free", "telemetry", "event_id"]) {
    assert(!text.includes(hidden), `summary leaked ${hidden}`);
  }
  assertEquals(r.headers.get("access-control-allow-origin"), "http://localhost:8080");
});

Deno.test("debug view requires the admin token", async () => {
  const { h } = handler();
  assertEquals((await h(req("debug", null))).status, 401);
  assertEquals((await h(req("debug", VIEWER))).status, 403);
  const r = await h(req("debug", ADMIN));
  assertEquals(r.status, 200);
  const body = await r.json();
  assertEquals(body.current_states[0].confidence, 0.91);
  assertEquals(body.devices[0].telemetry.memory.heap_free, 123);
});

Deno.test("layout PUT: admin only, validated, stored", async () => {
  const { h, store } = handler();
  const layout = {
    device_id: "xiao-01",
    seats: [
      { seat_id: "seat_01", label: "좌석 1", roi: { x: 0.05, y: 0.25, width: 0.25, height: 0.6 } },
      { seat_id: "seat_02", label: "좌석 2", roi: { x: 0.37, y: 0.25, width: 0.25, height: 0.6 } },
    ],
  };
  assertEquals((await h(req("layout", VIEWER, "PUT", layout))).status, 403);
  assertEquals((await h(req("layout", ADMIN, "PUT", layout))).status, 200);
  assertEquals(store.layouts.get("xiao-01")!.length, 2);
  const bad = {
    device_id: "xiao-01",
    seats: [{ ...layout.seats[0], roi: { x: 0.9, y: 0, width: 0.5, height: 0.5 } }],
  };
  assertEquals((await h(req("layout", ADMIN, "PUT", bad))).status, 422);
  const dup = { device_id: "xiao-01", seats: [layout.seats[0], layout.seats[0]] };
  assertEquals((await h(req("layout", ADMIN, "PUT", dup))).status, 422);
});

Deno.test("CORS preflight only for allowed origins", async () => {
  const { h } = handler();
  const ok = await h(
    new Request("http://x/", { method: "OPTIONS", headers: { origin: "http://localhost:8080" } }),
  );
  assertEquals(ok.status, 204);
  assertEquals(ok.headers.get("access-control-allow-origin"), "http://localhost:8080");
  const evil = await h(
    new Request("http://x/", { method: "OPTIONS", headers: { origin: "https://evil.example" } }),
  );
  assertEquals(evil.headers.get("access-control-allow-origin"), null);
});

Deno.test("people: one crop per detection, seat assigned, no scores in summary", () => {
  const d = data();
  d.latestSnapshot = {
    snapshot_id: "xiao-01-a-s1",
    device_id: "xiao-01",
    storage_path: "p",
    reason: "state_change",
    width: 320,
    height: 240,
    seats: [],
    detections: [
      { x: 0.78, y: 0.4, width: 0.05, height: 0.05, cx: 0.8, cy: 0.42, confidence: 0.9 },
      { x: 0.18, y: 0.4, width: 0.05, height: 0.05, cx: 0.2, cy: 0.42, confidence: 0.8 },
      { x: 0.5, y: 0.05, width: 0.05, height: 0.05, cx: 0.52, cy: 0.07, confidence: 0.7 },
    ],
    device_ts: null,
    created_at: "2026-11-03T09:59:00Z",
  };
  d.snapshotUrl = "https://example/snap.jpg";
  const s = buildSummary(d, NOW);
  assertEquals(s.people.map((p) => p.index), [1, 2, 3]);
  assertEquals(s.people.map((p) => p.seat_id), ["seat_01", null, "seat_03"]); // sorted left to right
  for (const p of s.people) {
    assert(p.crop.x >= 0 && p.crop.x + p.crop.width <= 1.000001);
    assert(p.crop.y >= 0 && p.crop.y + p.crop.height <= 1.000001);
  }
  assert(!JSON.stringify(s.people).includes("confidence"));
});

Deno.test("people: empty without snapshot or detections", () => {
  assertEquals(buildSummary(data(), NOW).people, []);
  assertEquals(buildPeople([], []), []);
});
