// Pure view-model builder for the dashboard (no DOM; unit-tested with `node --test`).
//
// Developer mode OFF -> the view model contains ONLY user-facing data. The renderer
// never touches `summary` / `debug` directly, so technical values cannot leak into
// the normal view by accident. Developer mode is a display setting, not security:
// the debug data is only returned by the server for the admin token.

(function (root) {
  "use strict";

  const STATE_TEXT = { occupied: "사용 중", empty: "비어 있음", unknown: "확인 불가" };

  function seatKey(s) {
    return `${s.device_id}/${s.seat_id}`;
  }

  function buildViewModel(summary, debug, devMode) {
    const seats = (summary?.seats ?? []).map((s) => ({
      key: seatKey(s),
      label: s.label || s.seat_id,
      state: ["occupied", "empty", "unknown"].includes(s.state) ? s.state : "unknown",
      stateText: STATE_TEXT[s.state] ?? STATE_TEXT.unknown,
      // ROI is used only to position the badge on the image, never printed.
      anchor: s.roi ? { x: s.roi.x + s.roi.width / 2, y: s.roi.y + s.roi.height / 2 } : null,
    }));
    // Per-person thumbnails: crop window only (no scores, no raw coordinates shown).
    const people = (summary?.people ?? []).map((p) => ({
      index: p.index,
      title: `사람 ${p.index}`,
      seatText: p.seat_label ? `${p.seat_label}` : "좌석 밖",
      crop: p.crop,
      imageUrl: p.image_url ?? null, // server-cut JPEG (dashboard server); CSS crop as fallback
    }));
    const vm = {
      counts: summary?.counts ?? { total: 0, occupied: 0, empty: 0, unknown: 0 },
      seats,
      people,
      snapshotUrl: summary?.snapshot?.url ?? null,
      updatedAt: summary?.generated_at ?? null,
      dev: null,
    };
    if (!devMode) return vm;

    // ---- developer mode only ----
    const states = new Map((debug?.current_states ?? []).map((c) => [seatKey(c), c]));
    const devices = debug?.devices ?? [];
    const telemetry = devices[0]?.telemetry ?? null;
    vm.dev = {
      available: !!debug,
      seats: (summary?.seats ?? []).map((s) => {
        const c = states.get(seatKey(s));
        return {
          key: seatKey(s),
          label: s.label || s.seat_id,
          seatId: s.seat_id,
          state: s.state,
          confidence: c ? c.confidence : null,
          inferenceMs: c ? c.inference_ms : null,
          roi: s.roi,
          eventId: c ? c.event_id : null,
        };
      }),
      detections: debug?.latest_snapshot?.detections ?? [],
      // detections sorted left->right to match the person numbering of the summary
      personConfidence: [...(debug?.latest_snapshot?.detections ?? [])]
        .filter((d) => typeof d.cx === "number")
        .sort((a, b) => a.cx - b.cx)
        .map((d) => d.confidence),
      perf: telemetry?.perf ?? null,
      memory: telemetry?.memory ?? null,
      model: telemetry?.model ?? null,
      devices: devices.map((d) => ({
        deviceId: d.device_id,
        status: d.status,
        lastSeen: d.last_seen_at,
        telemetryAt: d.telemetry_at,
        streaming: d.telemetry?.streaming ?? null,
        inputMode: d.telemetry?.input_mode ?? null,
      })),
      recentEvents: (debug?.recent_events ?? []).slice(0, 20),
      raw: debug,
    };
    return vm;
  }

  // ---- detection boxes (helmet-detection style overlay) ----
  // FOMO returns person centres; the box is the display window around the centre.
  function personBoxes(people, devConfidence) {
    return (people ?? []).map((p, i) => {
      const onSeat = !!p.seat_label;
      const conf = devConfidence?.[i];
      return {
        x: p.crop.x, y: p.crop.y, w: p.crop.width, h: p.crop.height,
        kind: onSeat ? "seated" : "other",
        label: `사람 ${p.index}${onSeat ? ` · ${p.seat_label}` : ""}${typeof conf === "number" ? ` ${conf.toFixed(2)}` : ""}`,
      };
    });
  }

  function timeAgo(iso, now) {
    const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
    if (s < 60) return `${s}초 전`;
    if (s < 3600) return `${Math.floor(s / 60)}분 전`;
    if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
    return `${Math.floor(s / 86400)}일 전`;
  }

  /** Sit-down feed cards. Confidence only when developer mode is on (and the server sent it). */
  function buildFeedModel(feed, devMode, now = Date.now()) {
    const items = (feed?.events ?? []).map((e) => ({
      key: e.event_id,
      seatLabel: e.seat_label || e.seat_id,
      time: new Date(e.created_at).toLocaleTimeString(),
      date: new Date(e.created_at).toLocaleDateString(),
      ago: timeAgo(e.created_at, now),
      snapshotUrl: e.snapshot?.url ?? null,
      personImageUrl: e.person_image_url ?? null,
      box: e.person_crop
        ? {
          x: e.person_crop.x, y: e.person_crop.y, w: e.person_crop.width, h: e.person_crop.height,
          kind: "seated",
          label: `착석 · ${e.seat_label || e.seat_id}${devMode && typeof e.confidence === "number" ? ` ${e.confidence.toFixed(2)}` : ""}`,
        }
        : null,
    }));
    return {
      todayTotal: feed?.sit_downs?.total ?? null,
      perSeat: feed?.sit_downs?.per_seat ?? {},
      items,
    };
  }

  const api = { buildViewModel, buildFeedModel, personBoxes, timeAgo, STATE_TEXT };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.XsvViewModel = api;
})(typeof window !== "undefined" ? window : globalThis);
