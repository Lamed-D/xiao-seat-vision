// node --test dashboard/tests/viewmodel.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { buildViewModel } = require("../static/viewmodel.js");

const summary = {
  generated_at: "2026-11-03T10:00:00Z",
  counts: { total: 3, occupied: 1, empty: 1, unknown: 1 },
  seats: [
    { device_id: "xiao-01", seat_id: "seat_01", label: "좌석 1", state: "occupied", roi: { x: 0.1, y: 0.2, width: 0.2, height: 0.4 } },
    { device_id: "xiao-01", seat_id: "seat_02", label: "좌석 2", state: "empty", roi: { x: 0.4, y: 0.2, width: 0.2, height: 0.4 } },
    { device_id: "xiao-01", seat_id: "seat_03", label: "좌석 3", state: "unknown", roi: null },
  ],
  snapshot: { url: "https://example/snap.jpg" },
  people: [
    { index: 1, seat_id: "seat_01", seat_label: "좌석 1", crop: { x: 0.05, y: 0.1, width: 0.3, height: 0.6 } },
    { index: 2, seat_id: null, seat_label: null, crop: { x: 0.6, y: 0.2, width: 0.3, height: 0.6 } },
  ],
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
  for (const hidden of ["confidence", "inference", "heap", "psram", "0.912", "event_id", "detections", '"roi"']) {
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

test("people: labels per person, seat text, scores only in developer mode", () => {
  const off = buildViewModel(summary, debug, false);
  assert.deepEqual(off.people.map((p) => [p.title, p.seatText]), [["사람 1", "좌석 1"], ["사람 2", "좌석 밖"]]);
  assert.ok(!JSON.stringify(off.people).includes("confidence"));
  const on = buildViewModel(summary, debug, true);
  assert.deepEqual(on.dev.personConfidence, [0.9]);
});

test("index.html loads config.js, viewmodel.js, app.js in this order (app.js depends on both)", () => {
  const html = require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "static", "index.html"), "utf8");
  const order = [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(order, ["config.js", "viewmodel.js", "app.js"]);
});

test("app.js runs against a minimal DOM and renders counts (smoke test)", async () => {
  const vm = require("node:vm");
  const fs = require("node:fs");
  const path = require("node:path");
  const elements = {};
  const makeEl = (id) => ({
    id, hidden: false, textContent: "", style: {}, src: "", returnValue: "",
    elements: {}, children: [], className: "", value: "", dataset: {},
    classList: { toggle() {}, add() {} },
    setAttribute() {}, removeAttribute() {}, append(...c) { this.children.push(...c); },
    replaceChildren() { this.children = []; }, addEventListener() {}, showModal() {},
  });
  const document = {
    querySelectorAll: () => [],
    getElementById: (id) => (elements[id] ??= makeEl(id)),
    createElement: (t) => makeEl(t),
    createElementNS: (_ns, t) => makeEl(t),
    createTextNode: (t) => ({ text: t }),
  };
  const summary = { generated_at: "2026-11-03T10:00:00Z", counts: { total: 3, occupied: 1, empty: 2, unknown: 0 },
    seats: [], snapshot: null, people: [], devices: [{ device_id: "xiao-01", online: true }] };
  const feed = { since: "s", sit_downs: { total: 5, per_seat: {} }, events: [{ event_id: "e1", seat_id: "seat_01",
    seat_label: "Seat 1", created_at: "2026-11-03T09:59:00Z", snapshot: { url: "https://x/a.jpg" },
    person_crop: { x: 0.1, y: 0.2, width: 0.3, height: 0.6 } }] };
  const ctx = {
    window: {}, document, console, URL, URLSearchParams, setInterval() {}, clearInterval() {}, setTimeout, localStorage: { getItem: () => null, setItem() {} },
    location: { origin: "http://127.0.0.1:8080", search: "", hash: "" },
    fetch: async (u) => ({ ok: true, json: async () => (String(u).includes("events") ? feed : summary) }),
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext('window.XSV_CONFIG = { apiUrl: "", proxy: true, proxyAdmin: true };', ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "static", "viewmodel.js"), "utf8"), ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8"), ctx);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(elements["c-total"].textContent, 3);
  assert.equal(elements["c-occupied"].textContent, 1);
  assert.equal(elements["error"].textContent, "");
  assert.equal(elements["c-today"].textContent, 5);
  assert.equal(elements["event-grid"].children.length, 1);
  assert.equal(elements["live-text"].textContent, "장치 연결됨");
});

test("feed model: sit-down cards with box label, confidence only in developer mode", () => {
  const { buildFeedModel, personBoxes } = require("../static/viewmodel.js");
  const feed = { sit_downs: { total: 2, per_seat: { seat_01: 2 } }, events: [{ event_id: "e1", seat_id: "seat_01",
    seat_label: "Seat 1", created_at: "2026-11-03T09:58:00Z", confidence: 0.8, snapshot: { url: "u" },
    person_crop: { x: 0.1, y: 0.2, width: 0.3, height: 0.6 } }] };
  const now = Date.parse("2026-11-03T10:00:00Z");
  const off = buildFeedModel(feed, false, now);
  assert.equal(off.todayTotal, 2);
  assert.equal(off.items[0].ago, "2분 전");
  assert.equal(off.items[0].box.label, "착석"); // no seat name on the photo
  assert.equal(off.items[0].seatLabel, "Seat 1"); // seat shown in the card text instead
  assert.equal(buildFeedModel(feed, true, now).items[0].box.label, "착석 0.80");
  const boxes = personBoxes([{ index: 1, seat_label: null, crop: { x: 0, y: 0, width: 0.3, height: 0.6 } }]);
  assert.equal(boxes[0].kind, "other");
  const seated = personBoxes([{ index: 2, seat_label: "Seat 1", crop: { x: 0, y: 0, width: 0.3, height: 0.6 } }]);
  assert.equal(seated[0].label, "사람 2");
  assert.equal(seated[0].kind, "seated");
});
