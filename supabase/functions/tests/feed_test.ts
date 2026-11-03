import { assert, assertEquals } from "jsr:@std/assert@1.0.13";
import {
  buildFeed,
  type FeedEventRow,
  type FeedSnapshotRow,
  matchSnapshot,
  personCrop,
} from "../_shared/feed.ts";
import { createDashboardHandler } from "../dashboard-api/handler.ts";
import { ADMIN, FakeStore, VIEWER } from "./fakes.ts";

const NOW = new Date("2026-11-03T10:00:00Z");
const seats = [
  {
    seat_id: "seat_01",
    label: "Seat 1",
    state: "occupied",
    roi: { x: 0.1, y: 0.2, width: 0.25, height: 0.6 },
  },
  {
    seat_id: "seat_02",
    label: "Seat 2",
    state: "empty",
    roi: { x: 0.4, y: 0.2, width: 0.25, height: 0.6 },
  },
];

function ev(id: string, seat: string, at: string, state = "occupied"): FeedEventRow {
  return {
    event_id: id,
    device_id: "xiao-01",
    seat_id: seat,
    state,
    confidence: 0.81,
    created_at: at,
  };
}
function snap(id: string, at: string, opts: Partial<FeedSnapshotRow> = {}): FeedSnapshotRow {
  return {
    snapshot_id: id,
    device_id: "xiao-01",
    created_at: at,
    width: 320,
    height: 240,
    seats,
    detections: [{ cx: 0.2, cy: 0.5, confidence: 0.9 }, { cx: 0.9, cy: 0.5, confidence: 0.95 }],
    url: `https://x/${id}.jpg?token=t`,
    ...opts,
  };
}

Deno.test("matchSnapshot: nearest within 30 s, prefers a snapshot showing the seat occupied", () => {
  const e = ev("e1", "seat_01", "2026-11-03T09:00:00Z");
  const near = snap("near", "2026-11-03T09:00:02Z", { seats: [{ ...seats[0], state: "empty" }] });
  const occ = snap("occ", "2026-11-03T09:00:10Z");
  const far = snap("far", "2026-11-03T09:01:00Z");
  assertEquals(matchSnapshot(e, [near, occ, far])?.snapshot_id, "occ");
  assertEquals(matchSnapshot(e, [far]), null);
  assertEquals(matchSnapshot({ ...e, device_id: "other" }, [occ]), null);
});

Deno.test("personCrop: detection inside the seat ROI, window kept inside the image", () => {
  const c = personCrop(snap("s", "2026-11-03T09:00:00Z"), seats[0].roi)!;
  assert(c.x >= 0 && c.x + c.width <= 1 && c.y >= 0 && c.y + c.height <= 1);
  assert(Math.abs(c.x + c.width / 2 - 0.2) < 1e-9); // centred on the person at cx 0.2
  assertEquals(personCrop(snap("s", "2026-11-03T09:00:00Z"), seats[1].roi), null); // nobody in seat 2
});

Deno.test("buildFeed: only sit-downs, counts per seat, confidence only for admin", () => {
  const events = [
    ev("e2", "seat_01", "2026-11-03T09:30:00Z"),
    ev("e1", "seat_02", "2026-11-03T09:00:00Z", "empty"),
  ];
  const today = [{ seat_id: "seat_01" }, { seat_id: "seat_01" }, { seat_id: "seat_02" }];
  const viewer = buildFeed(
    events,
    [snap("a", "2026-11-03T09:30:01Z")],
    today,
    "since",
    new Map(),
    false,
    NOW,
  );
  assertEquals(viewer.events.length, 1);
  assertEquals(viewer.sit_downs, { total: 3, per_seat: { seat_01: 2, seat_02: 1 } });
  assertEquals(viewer.events[0].seat_label, "Seat 1");
  assert(viewer.events[0].snapshot?.url.includes("a.jpg"));
  assert(viewer.events[0].person_crop);
  assert(!JSON.stringify(viewer).includes("confidence"));
  const admin = buildFeed(
    events,
    [],
    today,
    "since",
    new Map([["xiao-01/seat_01", "창가 1"]]),
    true,
    NOW,
  );
  assertEquals(admin.events[0].confidence, 0.81);
  assertEquals(admin.events[0].seat_label, "창가 1");
  assertEquals(admin.events[0].snapshot, null);
});

Deno.test("dashboard-api view=events: viewer allowed, limit clamped", async () => {
  const store = new FakeStore();
  store.feed.events = [ev("e1", "seat_01", "2026-11-03T09:59:00Z")];
  store.feed.snapshots = [snap("a", "2026-11-03T09:59:05Z")];
  const h = createDashboardHandler({
    viewerTokens: [VIEWER],
    adminTokens: [ADMIN],
    allowedOrigins: [],
    store,
    now: () => NOW,
  });
  const r = await h(
    new Request("http://x/dashboard-api?view=events&limit=500", {
      headers: { "x-dashboard-token": VIEWER },
    }),
  );
  assertEquals(r.status, 200);
  const body = await r.json();
  assertEquals(body.events.length, 1);
  assertEquals(body.since, "2026-11-02T10:00:00.000Z");
  assertEquals((await h(new Request("http://x/dashboard-api?view=events"))).status, 401);
});
