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

  const api = { buildViewModel, STATE_TEXT };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.XsvViewModel = api;
})(typeof window !== "undefined" ? window : globalThis);
