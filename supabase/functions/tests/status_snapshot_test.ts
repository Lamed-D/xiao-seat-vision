import { assertEquals } from "jsr:@std/assert@1.0.13";
import { createDeviceStatusHandler } from "../device-status/handler.ts";
import { createIngestSnapshotHandler } from "../ingest-snapshot/handler.ts";
import { FakeStore, post, TOKEN } from "./fakes.ts";

const JPEG_B64 = btoa(String.fromCharCode(0xff, 0xd8, 0xff, 0xe0, 1, 2, 3, 0xff, 0xd9));

Deno.test("device-status: online/offline and telemetry", async () => {
  const store = new FakeStore();
  const h = createDeviceStatusHandler({ tokens: [TOKEN], store });
  const online = {
    device_id: "xiao-01",
    kind: "status",
    payload: { device_id: "xiao-01", status: "online" },
  };
  assertEquals((await h(post(online))).status, 200);
  const tele = {
    device_id: "xiao-01",
    kind: "telemetry",
    payload: { device_id: "xiao-01", seats: [], memory: { heap_free: 1 } },
  };
  assertEquals((await h(post(tele))).status, 200);
  assertEquals(store.statuses.length, 2);
  const mismatch = {
    device_id: "xiao-01",
    kind: "status",
    payload: { device_id: "xiao-02", status: "online" },
  };
  assertEquals((await h(post(mismatch))).status, 422);
  const badStatus = {
    device_id: "xiao-01",
    kind: "status",
    payload: { device_id: "xiao-01", status: "sleepy" },
  };
  assertEquals((await h(post(badStatus))).status, 422);
});

Deno.test("ingest-snapshot: stores JPEG once, rejects non-JPEG", async () => {
  const store = new FakeStore();
  const h = createIngestSnapshotHandler({ tokens: [TOKEN], store });
  const snap = {
    snapshot_id: "xiao-01-1a2b3c4d-s000001",
    device_id: "xiao-01",
    reason: "state_change",
    width: 320,
    height: 240,
    seats: [{ seat_id: "seat_01", state: "occupied" }],
    detections: [{ cx: 0.2, cy: 0.5, confidence: 0.9 }],
    image_b64: JPEG_B64,
  };
  assertEquals((await h(post(snap))).status, 201);
  assertEquals((await h(post(snap))).status, 200);
  assertEquals(store.snapshots.get(snap.snapshot_id)!.jpeg[0], 0xff);
  const png = { ...snap, snapshot_id: "xiao-01-x-s2", image_b64: btoa("\x89PNG....") };
  assertEquals((await h(post(png))).status, 422);
  const badB64 = { ...snap, snapshot_id: "xiao-01-x-s3", image_b64: "@@@" };
  assertEquals((await h(post(badB64))).status, 422);
  const wrongPrefix = { ...snap, snapshot_id: "xiao-02-x-s4" };
  assertEquals((await h(post(wrongPrefix))).status, 422);
});
