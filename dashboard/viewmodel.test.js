// node --test dashboard/
const test = require("node:test");
const assert = require("node:assert/strict");
const { buildViewModel } = require("./viewmodel.js");

const summary = {
  generated_at: "2026-11-03T10:00:00Z",
  counts: { total: 3, occupied: 1, empty: 1, unknown: 1 },
  seats: [
    { device_id: "xiao-01", seat_id: "seat_01", label: "좌석 1", state: "occupied", roi: { x: 0.1, y: 0.2, width: 0.2, height: 0.4 } },
    { device_id: "xiao-01", seat_id: "seat_02", label: "좌석 2", state: "empty", roi: { x: 0.4, y: 0.2, width: 0.2, height: 0.4 } },
    { device_id: "xiao-01", seat_id: "seat_03", label: "좌석 3", state: "unknown", roi: null },
  ],
  snapshot: { url: "https://example/snap.jpg" },
};
const debug = {
  current_states: [{ device_id: "xiao-01", seat_id: "seat_01", confidence: 0.912, inference_ms: 133, event_id: "xiao-01-a-1" }],
  devices: [{ device_id: "xiao-01", status: "online", last_seen_at: "2026-11-03T09:59:50Z",
    telemetry: { perf: { inference_ms: 133, inference_fps: 0.95 }, memory: { heap_free: 150000, psram_free: 7000000 }, model: { name: "m", version: 3 } } }],
  latest_snapshot: { detections: [{ cx: 0.2, cy: 0.4, confidence: 0.9 }] },
  recent_events: [],
};

test("developer mode OFF: no technical data in the view model", () => {
  const vm = buildViewModel(summary, debug, false);
  assert.equal(vm.dev, null);
  const text = JSON.stringify(vm);
  for (const hidden of ["confidence", "inference", "heap", "psram", "0.912", "event_id", "detections", "roi"]) {
    assert.ok(!text.includes(hidden), `leaked ${hidden}`);
  }
  assert.deepEqual(vm.counts, summary.counts);
  assert.equal(vm.seats[0].stateText, "사용 중");
  assert.deepEqual(vm.seats[0].anchor, { x: 0.2, y: 0.4 });
});

test("developer mode ON: confidence, ROI, perf, memory, detections present", () => {
  const vm = buildViewModel(summary, debug, true);
  assert.equal(vm.dev.available, true);
  assert.equal(vm.dev.seats[0].confidence, 0.912);
  assert.deepEqual(vm.dev.seats[0].roi, summary.seats[0].roi);
  assert.equal(vm.dev.perf.inference_ms, 133);
  assert.equal(vm.dev.memory.psram_free, 7000000);
  assert.equal(vm.dev.detections.length, 1);
  assert.equal(vm.dev.devices[0].status, "online");
});

test("developer mode ON without admin data degrades gracefully", () => {
  const vm = buildViewModel(summary, null, true);
  assert.equal(vm.dev.available, false);
  assert.equal(vm.seats.length, 3);
});

test("unexpected states render as unknown", () => {
  const vm = buildViewModel({ ...summary, seats: [{ ...summary.seats[0], state: "weird" }] }, null, false);
  assert.equal(vm.seats[0].state, "unknown");
});
