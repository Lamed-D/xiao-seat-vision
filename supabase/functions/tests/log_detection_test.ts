import { assertEquals } from "jsr:@std/assert@1.0.13";
import { createLogDetectionHandler } from "../log-detection/handler.ts";
import { ConstraintError, FakeStore, post, TOKEN, validEvent } from "./fakes.ts";

function setup() {
  const store = new FakeStore();
  const handler = createLogDetectionHandler({ tokens: [TOKEN], store });
  return { store, handler };
}

Deno.test("201 on first event, 200 duplicate on replay (idempotent)", async () => {
  const { store, handler } = setup();
  const r1 = await handler(post(validEvent));
  assertEquals(r1.status, 201);
  const r2 = await handler(post(validEvent));
  assertEquals(r2.status, 200);
  assertEquals((await r2.json()).status, "duplicate");
  assertEquals(store.events.size, 1);
});

Deno.test("stores seat_id, defaults is_test/source, keeps legacy payloads", async () => {
  const { store, handler } = setup();
  const legacy = { ...validEvent, event_id: "xiao-01-legacy-1" } as Record<string, unknown>;
  delete legacy.seat_id;
  assertEquals((await handler(post(legacy))).status, 201);
  const row = store.events.get("xiao-01-legacy-1")!;
  assertEquals(row.seat_id, "seat_01");
  assertEquals(row.is_test, false);
  assertEquals(row.source, "camera");
});

Deno.test("test-video events are flagged", async () => {
  const { store, handler } = setup();
  const ev = { ...validEvent, is_test: true, source: "test_video" };
  assertEquals((await handler(post(ev))).status, 201);
  assertEquals(store.events.get(ev.event_id)!.source, "test_video");
  const bad = { ...validEvent, event_id: "xiao-01-x-2", is_test: false, source: "test_video" };
  assertEquals((await handler(post(bad))).status, 422);
});

Deno.test("method, auth, content-type and JSON errors", async () => {
  const { handler } = setup();
  assertEquals((await handler(new Request("http://x/", { method: "GET" }))).status, 405);
  assertEquals((await handler(post(validEvent, null))).status, 401);
  assertEquals((await handler(post(validEvent, "w".repeat(40)))).status, 401);
  assertEquals((await handler(post(null, TOKEN, "{not json"))).status, 400);
  const text = new Request("http://x/", {
    method: "POST",
    headers: { "x-bridge-token": TOKEN, "content-type": "text/plain" },
    body: "x",
  });
  assertEquals((await handler(text)).status, 415);
});

Deno.test("validation: state, confidence, inference_ms, ids, timestamp", async () => {
  const { handler } = setup();
  const cases: Record<string, unknown>[] = [
    { ...validEvent, state: "maybe" },
    { ...validEvent, confidence: 1.5 },
    { ...validEvent, confidence: "0.9" },
    { ...validEvent, inference_ms: -1 },
    { ...validEvent, inference_ms: 1.5 },
    { ...validEvent, device_id: "bad id" },
    { ...validEvent, seat_id: "seat/1" },
    { ...validEvent, event_id: "other-device-1" },
    { ...validEvent, timestamp: "yesterday" },
    { ...validEvent, is_test: "yes" },
  ];
  for (const c of cases) {
    assertEquals((await handler(post(c))).status, 422, JSON.stringify(c));
  }
  const unknownOk = { ...validEvent, event_id: "xiao-01-u-1", state: "unknown", confidence: 0 };
  assertEquals((await handler(post(unknownOk))).status, 201);
});

Deno.test("DB constraint errors map to 422, other errors to 500 (no detail leak)", async () => {
  const { store, handler } = setup();
  store.failWith = new ConstraintError("violates check constraint");
  assertEquals((await handler(post(validEvent))).status, 422);
  store.failWith = new Error("connection refused to db.internal:5432");
  const r = await handler(post(validEvent));
  assertEquals(r.status, 500);
  assertEquals(await r.json(), { error: "storage_error" });
});

Deno.test("rejects every request when no token is configured", async () => {
  const handler = createLogDetectionHandler({ tokens: [], store: new FakeStore() });
  assertEquals((await handler(post(validEvent))).status, 500);
});
