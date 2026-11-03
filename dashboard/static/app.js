// xiao-seat-vision dashboard (static, no build step).
// Tabs: ① 전체 현황 (live overview) ② 이벤트 로그 (photo log of sit-down events).
// Settings live in localStorage of this browser only. Developer mode defaults to OFF.
(function () {
  "use strict";
  const { buildViewModel, buildFeedModel, personBoxes } = window.XsvViewModel;
  const $ = (id) => document.getElementById(id);
  const CFG = window.XSV_CONFIG || {};
  const DEFAULTS = {
    apiUrl: CFG.apiUrl || "",
    viewerToken: "",
    adminToken: "",
    devMode: false, // must stay OFF by default
    testData: false,
    esp32Url: "",
    esp32Token: "",
  };
  const REFRESH_MS = 10000;

  const store = {
    get(k, d) {
      try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; }
    },
    set(k, v) {
      try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ }
    },
  };
  let settings = { ...DEFAULTS, ...store.get("xsv-settings", {}) };
  let streaming = false;
  let lastFeed = null;
  let lastSeenEvent = store.get("xsv-last-seen-event", null);
  // #events / #overview in the URL selects the tab (shareable link); else last used tab.
  let activeTab = typeof location !== "undefined" && /^#(events|overview)$/.test(location.hash || "")
    ? location.hash.slice(1)
    : store.get("xsv-tab", "overview");

  // ---------------------------------------------------------------- API
  // Token-less mode: the dashboard server proxies /api/dashboard and adds the tokens
  // server-side. A token typed into the settings takes precedence (direct call).
  function useProxy() {
    return !!CFG.proxy && !settings.viewerToken;
  }
  function adminAvailable() {
    return useProxy() ? !!CFG.proxyAdmin : !!settings.adminToken;
  }
  async function api(view, token) {
    let r;
    if (useProxy()) {
      const url = new URL("/api/dashboard", location.origin);
      url.searchParams.set("view", view);
      if (settings.testData) url.searchParams.set("data", "test");
      r = await fetch(url);
    } else {
      const url = new URL(settings.apiUrl);
      url.searchParams.set("view", view);
      if (settings.testData) url.searchParams.set("data", "test");
      r = await fetch(url, { headers: { "x-dashboard-token": token } });
    }
    if (!r.ok) {
      const err = new Error(`${view}: HTTP ${r.status}`);
      err.status = r.status;
      throw err;
    }
    return r.json();
  }

  // ---------------------------------------------------------------- helpers
  function el(tag, attrs = {}, text) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text; // never innerHTML with data
    return e;
  }

  /** Helmet-detection style boxes with labels, positioned in % of the image. */
  function drawBoxes(container, boxes) {
    if (!container) return;
    container.replaceChildren();
    for (const b of boxes) {
      if (!b) continue;
      const d = el("div", { class: `det-box det-${b.kind}` });
      d.style.left = `${b.x * 100}%`;
      d.style.top = `${b.y * 100}%`;
      d.style.width = `${b.w * 100}%`;
      d.style.height = `${b.h * 100}%`;
      d.append(el("span", { class: "det-label" }, b.label));
      container.append(d);
    }
  }

  function showTab(name) {
    activeTab = name === "events" ? "events" : "overview";
    store.set("xsv-tab", activeTab);
    if (typeof history !== "undefined" && history.replaceState) history.replaceState(null, "", `#${activeTab}`);
    for (const t of document.querySelectorAll(".tab")) {
      const on = t.dataset.tab === activeTab;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", on ? "true" : "false");
    }
    $("tab-overview").hidden = activeTab !== "overview";
    $("tab-events").hidden = activeTab !== "events";
    if (activeTab === "events" && lastFeed?.items.length) {
      lastSeenEvent = lastFeed.items[0].key;
      store.set("xsv-last-seen-event", lastSeenEvent);
      $("events-badge").hidden = true;
    }
  }

  function openViewer(item) {
    if (!item.snapshotUrl) return;
    $("viewer-title").textContent = `${item.seatLabel} 착석`;
    $("viewer-img").src = item.snapshotUrl;
    drawBoxes($("viewer-boxes"), [item.box]);
    $("viewer-meta").textContent = `${item.date} ${item.time} (${item.ago})`;
    $("viewer").showModal();
  }

  // ---------------------------------------------------------------- overview
  function renderBasic(vm, summary) {
    $("c-total").textContent = vm.counts.total;
    $("c-occupied").textContent = vm.counts.occupied;
    $("c-empty").textContent = vm.counts.empty;
    $("c-unknown").textContent = vm.counts.unknown;
    $("c-unknown-box").hidden = vm.counts.unknown === 0;
    $("data-badge").hidden = !settings.testData;

    const online = (summary.devices ?? []).some((d) => d.online);
    $("live-dot").className = `live-dot ${online ? "on" : "off"}`;
    $("live-text").textContent = online ? "장치 연결됨" : "장치 연결 안 됨";

    const hasSnap = !!vm.snapshotUrl;
    $("snapshot-wrap").hidden = !hasSnap;
    $("no-snapshot").hidden = hasSnap;
    if (hasSnap && $("snapshot").src !== vm.snapshotUrl) $("snapshot").src = vm.snapshotUrl;
    $("snapshot-time").textContent = summary.snapshot
      ? `촬영 ${new Date(summary.snapshot.created_at).toLocaleTimeString()}`
      : "";

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
    drawBoxes($("boxes"), personBoxes(summary.people, vm.dev?.personConfidence));
    $("updated").textContent = vm.updatedAt ? new Date(vm.updatedAt).toLocaleString() : "–";
  }

  function renderPeople(vm) {
    const list = $("people-list");
    list.replaceChildren();
    $("people-section").hidden = !(vm.snapshotUrl && vm.people.length);
    $("people-count").textContent = vm.people.length ? `(${vm.people.length}명)` : "";
    vm.people.forEach((p, i) => {
      const li = el("li");
      let thumb;
      if (p.imageUrl) {
        thumb = el("img", { class: "person-thumb", src: p.imageUrl, alt: p.title });
      } else {
        thumb = el("div", { class: "person-thumb", role: "img", "aria-label": p.title });
        const c = p.crop;
        thumb.style.backgroundImage = `url("${vm.snapshotUrl}")`;
        thumb.style.backgroundSize = `${100 / c.width}% ${100 / c.height}%`;
        thumb.style.backgroundPosition = `${c.width >= 1 ? 0 : (c.x / (1 - c.width)) * 100}% ${
          c.height >= 1 ? 0 : (c.y / (1 - c.height)) * 100
        }%`;
      }
      const meta = el("div", { class: "person-meta" });
      meta.append(el("b", {}, p.title), document.createTextNode(p.seatText));
      const conf = vm.dev?.personConfidence?.[i];
      if (vm.dev && typeof conf === "number") meta.append(el("div", { class: "muted small" }, `신뢰도 ${conf.toFixed(2)}`));
      li.append(thumb, meta);
      list.append(li);
    });
  }

  function renderOverlay(vm) {
    const svg = $("overlay");
    svg.replaceChildren();
    if (!vm.dev) return; // ROI outlines only in developer mode
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

  // ---------------------------------------------------------------- event log
  function eventCard(item, big) {
    const li = el("li", { class: `event-card${big ? "" : " compact"}` });
    const media = el("div", { class: "event-media snapshot-wrap" });
    if (item.snapshotUrl) {
      media.append(el("img", { src: item.snapshotUrl, alt: `${item.seatLabel} 착석 사진`, loading: "lazy" }));
      const boxes = el("div", { class: "boxes" });
      media.append(boxes);
      drawBoxes(boxes, [item.box]);
    } else {
      media.append(el("div", { class: "no-photo" }, "사진 없음"));
    }
    const meta = el("div", { class: "event-meta" });
    meta.append(
      el("span", { class: "event-tag" }, "착석"),
      el("b", {}, item.seatLabel),
      el("span", { class: "muted small" }, `${item.time} · ${item.ago}`),
    );
    if (item.personImageUrl && big) {
      meta.append(el("img", { class: "event-person", src: item.personImageUrl, alt: "사람", loading: "lazy" }));
    }
    li.append(media, meta);
    li.addEventListener("click", () => openViewer(item));
    return li;
  }

  function renderFeed(feed) {
    lastFeed = feed;
    $("c-today").textContent = feed.todayTotal ?? "–";

    const recent = $("recent-events");
    recent.replaceChildren();
    for (const it of feed.items.slice(0, 4)) recent.append(eventCard(it, false));
    $("recent-empty").hidden = feed.items.length > 0;

    // seat filter options
    const sel = $("seat-filter");
    const current = sel.value;
    const seats = [...new Set(feed.items.map((i) => i.seatLabel))].sort();
    sel.replaceChildren(el("option", { value: "" }, "전체"), ...seats.map((s) => el("option", { value: s }, s)));
    sel.value = seats.includes(current) ? current : "";

    const grid = $("event-grid");
    grid.replaceChildren();
    const shown = feed.items.filter((i) => !sel.value || i.seatLabel === sel.value);
    for (const it of shown) grid.append(eventCard(it, true));
    $("events-empty").hidden = shown.length > 0;

    // "new events" badge on the tab
    const idx = feed.items.findIndex((i) => i.key === lastSeenEvent);
    const fresh = idx === -1 ? feed.items.length : idx;
    if (activeTab === "events") {
      if (feed.items.length) {
        lastSeenEvent = feed.items[0].key;
        store.set("xsv-last-seen-event", lastSeenEvent);
      }
      $("events-badge").hidden = true;
    } else {
      $("events-badge").hidden = fresh === 0;
      $("events-badge").textContent = fresh > 99 ? "99+" : String(fresh);
    }
  }

  function feedUnavailable(message) {
    $("c-today").textContent = "–";
    $("events-message").hidden = false;
    $("events-message").textContent = message;
    $("recent-empty").hidden = false;
  }

  // ---------------------------------------------------------------- developer
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
      g.append(kv(`MQTT ${d.deviceId}`,
        `${d.status}, 마지막 수신 ${d.lastSeen ? new Date(d.lastSeen).toLocaleTimeString() : "–"}, 입력 ${d.inputMode ?? "–"}`));
    }
    panel.append(g);

    panel.append(el("h2", {}, "최근 이벤트 (원시)"));
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

  // ---------------------------------------------------------------- refresh
  async function refresh() {
    $("error").textContent = "";
    if (!useProxy() && (!settings.apiUrl || !settings.viewerToken)) {
      $("error").textContent = "설정에서 API URL과 조회 토큰을 입력하세요.";
      return;
    }
    try {
      const [summary, feedRaw] = await Promise.all([
        api("summary", settings.viewerToken),
        api("events", settings.viewerToken).catch((e) => ({ __error: e })),
      ]);
      let debug = null;
      if (settings.devMode && adminAvailable()) {
        try {
          debug = await api("debug", settings.adminToken); // fetched only in developer mode
        } catch {
          debug = null;
        }
      }
      const vm = buildViewModel(summary, debug, settings.devMode);
      renderBasic(vm, summary);
      renderPeople(vm);
      renderOverlay(vm);
      renderDev(vm);
      if (feedRaw.__error) {
        feedUnavailable(feedRaw.__error.status === 501 || feedRaw.__error.status === 404
          ? "이벤트 로그를 보려면 Supabase 함수 업데이트가 필요합니다: python scripts/supabase_cli.py functions deploy dashboard-api --use-api"
          : `이벤트 로그를 불러오지 못했습니다 (${feedRaw.__error.message})`);
      } else {
        $("events-message").hidden = true;
        renderFeed(buildFeedModel(feedRaw, settings.devMode));
      }
    } catch (e) {
      $("error").textContent = `불러오기 실패: ${e.message}`;
    }
  }

  // ---------------------------------------------------------------- streaming
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

  // ---------------------------------------------------------------- wiring
  for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => showTab(t.dataset.tab));
  for (const b of document.querySelectorAll("[data-goto]")) b.addEventListener("click", () => showTab(b.dataset.goto));
  $("seat-filter").addEventListener("change", () => lastFeed && renderFeed(lastFeed));
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
    store.set("xsv-settings", settings);
    applySettingsUi();
    refresh();
  });

  applySettingsUi();
  showTab(activeTab);
  refresh();
  setInterval(refresh, REFRESH_MS);
})();
