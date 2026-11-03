// xiao-seat-vision dashboard (static, no build step).
// Settings live in localStorage of this browser only. Developer mode defaults to OFF.
(function () {
  "use strict";
  const { buildViewModel } = window.XsvViewModel;
  const $ = (id) => document.getElementById(id);
  const DEFAULTS = {
    apiUrl: (window.XSV_CONFIG && window.XSV_CONFIG.apiUrl) || "",
    viewerToken: "",
    adminToken: "",
    devMode: false, // must stay OFF by default
    testData: false,
    esp32Url: "",
    esp32Token: "",
  };
  const REFRESH_MS = 10000;

  function loadSettings() {
    try {
      return { ...DEFAULTS, ...JSON.parse(localStorage.getItem("xsv-settings") || "{}") };
    } catch {
      return { ...DEFAULTS };
    }
  }
  function saveSettings(s) {
    try {
      localStorage.setItem("xsv-settings", JSON.stringify(s));
    } catch { /* private mode: settings live for this page only */ }
  }
  let settings = loadSettings();
  let streaming = false;

  async function api(view, token) {
    const url = new URL(settings.apiUrl);
    url.searchParams.set("view", view);
    if (settings.testData) url.searchParams.set("data", "test");
    const r = await fetch(url, { headers: { "x-dashboard-token": token } });
    if (!r.ok) throw new Error(`${view}: HTTP ${r.status}`);
    return r.json();
  }

  function el(tag, attrs = {}, text) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text; // never innerHTML with data
    return e;
  }

  function renderBasic(vm) {
    $("c-total").textContent = vm.counts.total;
    $("c-occupied").textContent = vm.counts.occupied;
    $("c-empty").textContent = vm.counts.empty;
    $("c-unknown").textContent = vm.counts.unknown;
    $("c-unknown-box").hidden = vm.counts.unknown === 0;
    $("data-badge").hidden = !settings.testData;

    const hasSnap = !!vm.snapshotUrl;
    $("snapshot-wrap").hidden = !hasSnap;
    $("no-snapshot").hidden = hasSnap;
    if (hasSnap && $("snapshot").src !== vm.snapshotUrl) $("snapshot").src = vm.snapshotUrl;

    const badges = $("badges");
    badges.replaceChildren();
    const list = $("seat-list");
    list.replaceChildren();
    for (const s of vm.seats) {
      if (s.anchor) {
        const b = el("span", { class: `seat-badge state-${s.state}` }, `${s.label} · ${s.stateText}`);
        b.style.left = `${s.anchor.x * 100}%`;
        b.style.top = `${s.anchor.y * 100}%`;
        badges.append(b);
      }
      const li = el("li", { class: `state-${s.state}` });
      li.append(el("span", { class: "name" }, s.label), el("span", { class: "detail" }, s.stateText));
      list.append(li);
    }
    $("updated").textContent = vm.updatedAt ? new Date(vm.updatedAt).toLocaleString() : "–";
  }

  function renderOverlay(vm) {
    const svg = $("overlay");
    svg.replaceChildren();
    if (!vm.dev) return; // ROI lines and markers only in developer mode
    const ns = "http://www.w3.org/2000/svg";
    for (const s of vm.dev.seats) {
      if (!s.roi) continue;
      const r = document.createElementNS(ns, "rect");
      r.setAttribute("x", s.roi.x);
      r.setAttribute("y", s.roi.y);
      r.setAttribute("width", s.roi.width);
      r.setAttribute("height", s.roi.height);
      r.setAttribute("fill", "none");
      r.setAttribute("stroke", "#4dabf7");
      r.setAttribute("stroke-width", "0.004");
      r.setAttribute("stroke-dasharray", "0.01 0.006");
      svg.append(r);
    }
    for (const d of vm.dev.detections) {
      if (typeof d.cx !== "number") continue;
      const c = document.createElementNS(ns, "circle");
      c.setAttribute("cx", d.cx);
      c.setAttribute("cy", d.cy);
      c.setAttribute("r", "0.012");
      c.setAttribute("fill", "#ffd43b");
      c.setAttribute("stroke", "#000");
      c.setAttribute("stroke-width", "0.003");
      svg.append(c);
    }
  }

  function kv(label, value) {
    const d = el("div", { class: "kv" });
    d.append(el("b", {}, label), document.createTextNode(value ?? "–"));
    return d;
  }

  function renderDev(vm) {
    const root = $("dev-root");
    root.replaceChildren(); // OFF -> nothing technical exists in the DOM
    if (!vm.dev) return;
    const panel = el("section", { class: "panel dev" });
    panel.append(el("h2", {}, "개발자 모드"));
    if (!vm.dev.available) {
      panel.append(el("p", { class: "muted" }, "관리자 토큰이 없거나 거부되어 상세 데이터를 불러오지 못했습니다."));
      root.append(panel);
      return;
    }
    const seatTable = el("table");
    const head = el("tr");
    for (const h of ["좌석", "상태", "신뢰도", "추론(ms)", "ROI (x,y,w,h)"]) head.append(el("th", {}, h));
    seatTable.append(head);
    for (const s of vm.dev.seats) {
      const tr = el("tr");
      const roi = s.roi ? [s.roi.x, s.roi.y, s.roi.width, s.roi.height].map((n) => n.toFixed(3)).join(", ") : "–";
      tr.append(
        el("td", {}, `${s.label} (${s.seatId})`),
        el("td", {}, s.state),
        el("td", {}, s.confidence == null ? "–" : s.confidence.toFixed(3)),
        el("td", {}, s.inferenceMs ?? "–"),
        el("td", {}, roi),
      );
      seatTable.append(tr);
    }
    panel.append(seatTable);

    const g = el("div", { class: "grid" });
    const p = vm.dev.perf || {};
    const m = vm.dev.memory || {};
    const model = vm.dev.model || {};
    g.append(
      kv("추론 시간", p.inference_ms != null ? `${p.inference_ms} ms (전처리 ${p.preprocess_ms} ms)` : null),
      kv("추론 FPS", p.inference_fps != null ? p.inference_fps.toFixed(2) : null),
      kv("Heap 여유", m.heap_free != null ? `${m.heap_free} B (최소 ${m.heap_min_free})` : null),
      kv("PSRAM 여유", m.psram_free != null ? `${m.psram_free} B (최소 ${m.psram_min_free})` : null),
      kv("모델", model.name ? `${model.name} v${model.version} (${model.type}, ${model.input?.join("x")})` : null),
      kv("감지 중심점", `${vm.dev.detections.length}개`),
    );
    for (const d of vm.dev.devices) {
      g.append(kv(`MQTT ${d.deviceId}`, `${d.status}, 마지막 수신 ${d.lastSeen ? new Date(d.lastSeen).toLocaleTimeString() : "–"}, 입력 ${d.inputMode ?? "–"}`));
    }
    panel.append(g);

    panel.append(el("h2", {}, "최근 이벤트"));
    const ev = el("table");
    for (const e of vm.dev.recentEvents) {
      const tr = el("tr");
      tr.append(el("td", {}, new Date(e.created_at).toLocaleTimeString()), el("td", {}, e.seat_id), el("td", {}, e.state),
        el("td", {}, e.event_id), el("td", {}, e.source));
      ev.append(tr);
    }
    panel.append(ev);
    const details = el("details");
    details.append(el("summary", {}, "원시 JSON"), el("pre", {}, JSON.stringify(vm.dev.raw, null, 2)));
    panel.append(details);
    root.append(panel);
  }

  async function refresh() {
    $("error").textContent = "";
    if (!settings.apiUrl || !settings.viewerToken) {
      $("error").textContent = "설정에서 API URL과 조회 토큰을 입력하세요.";
      return;
    }
    try {
      const summary = await api("summary", settings.viewerToken);
      let debug = null;
      if (settings.devMode && settings.adminToken) {
        try {
          debug = await api("debug", settings.adminToken); // fetched only in developer mode
        } catch (e) {
          debug = null;
        }
      }
      const vm = buildViewModel(summary, debug, settings.devMode);
      renderBasic(vm);
      renderOverlay(vm);
      renderDev(vm);
    } catch (e) {
      $("error").textContent = `불러오기 실패: ${e.message}`;
    }
  }

  // ---- optional live stream (independent of developer mode) ----
  function streamUrl() {
    const u = new URL(settings.esp32Url);
    return `${u.protocol}//${u.hostname}:81/stream?token=${encodeURIComponent(settings.esp32Token)}&t=${Date.now()}`;
  }
  async function setStream(on) {
    const u = new URL("/api/stream", settings.esp32Url);
    u.searchParams.set("enable", on ? "1" : "0");
    u.searchParams.set("token", settings.esp32Token);
    if (!on) {
      $("stream").removeAttribute("src"); // closes the MJPEG connection
      $("stream").hidden = true;
    }
    try {
      const r = await fetch(u, { method: "POST" });
      const j = await r.json();
      streaming = !!j.streaming;
    } catch (e) {
      $("error").textContent = `스트리밍 제어 실패: ${e.message}`;
      streaming = false;
    }
    $("stream-toggle").textContent = streaming ? "스트리밍 끄기" : "스트리밍 켜기";
    if (streaming) {
      $("stream").src = streamUrl();
      $("stream").hidden = false;
    }
  }

  function applySettingsUi() {
    $("stream-section").hidden = !(settings.esp32Url && settings.esp32Token);
  }

  $("stream-toggle").addEventListener("click", () => setStream(!streaming));
  $("open-settings").addEventListener("click", () => {
    const f = $("settings-form");
    for (const [k, v] of Object.entries(settings)) {
      if (!f.elements[k]) continue;
      if (f.elements[k].type === "checkbox") f.elements[k].checked = !!v;
      else f.elements[k].value = v;
    }
    $("settings").showModal();
  });
  $("settings").addEventListener("close", () => {
    if ($("settings").returnValue !== "save") return;
    const f = $("settings-form");
    const next = { ...settings };
    for (const k of Object.keys(DEFAULTS)) {
      if (!f.elements[k]) continue;
      next[k] = f.elements[k].type === "checkbox" ? f.elements[k].checked : f.elements[k].value.trim();
    }
    settings = next;
    saveSettings(settings);
    applySettingsUi();
    refresh();
  });

  applySettingsUi();
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
