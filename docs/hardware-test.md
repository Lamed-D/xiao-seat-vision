# Hardware test checklist (XIAO ESP32S3 Sense)

None of these have been executed yet. CI only proves compilation. Record date, firmware commit, sensor.

## 1. Board, PSRAM, camera
- [ ] Boot log: `chip=ESP32-S3`, `core=3.3.12`, flash 8 MB
- [ ] `[mem] psram: total≈8 MB` (Tools → PSRAM → OPI PSRAM)
- [ ] `[camera] ready: sensor=OV2640|OV3660` — note which one
- [ ] `capture_snapshot.py` saves a correct 320×240 JPEG (orientation, focus, field of view)
- [ ] 30 min idle: `[mem]` free heap/PSRAM stable (no frame-buffer leak)

## 2. Network
- [ ] Wi-Fi connects; unplug the AP → `connection lost` → reconnect with backoff, loop keeps running
- [ ] `[mqtt] connected`; bridge log `status from xiao-01 stored`; DB `device_status.status = online`
- [ ] Wrong CA / wrong host → `certificate verification failed`, no connection (verification stays on)
- [ ] Power off the board → retained `offline` (LWT) within ~45 s (keepalive 30 s × 1.5)
- [ ] Stop the broker for 2 min with events pending → delivered after reconnect, no duplicates in DB

## 3. AI (after model integration)
- [ ] Boot log shows `type=fomo`, input size, labels, field of view
- [ ] `inference_ms` and FPS in telemetry; acceptable target ≤ 300 ms at 96×96 (measure)
- [ ] Free heap/PSRAM after 1 h of inference (no decline)
- [ ] Multi-seat: one person per seat → only that seat `occupied`; others unaffected
- [ ] Person moves seat 1 → seat 2: seat 1 `empty`, seat 2 `occupied`, seat 3 no event
- [ ] Person between two overlapping ROIs → `unknown` (not both occupied)
- [ ] Brief false positive (1–2 frames) does not change state (3-frame debounce)
- [ ] Camera unplugged / inference errors → after 15 s seats `unknown`
- [ ] Find the max reliable seat count for your mount and model input; document it

## 4. Streaming (optional feature)
- [ ] OFF by default; inference and MQTT run normally
- [ ] ON from the device page and the dashboard: QVGA ≤ 3 FPS; inference interval 2 s
- [ ] Inference and MQTT keep working while streaming; events still arrive
- [ ] Close the browser tab → `client detached`; after 60 s `stream OFF (no client)`
- [ ] Toggle ON/OFF 20×: heap/PSRAM return to baseline; inference speed returns to the OFF value
- [ ] Second viewer gets 503 (one stream client)

## 5. Test video (Path A)
- [ ] `video-feeder` TARGET=esp32 → device logs `[infer] [test] ...`, events `source=test_video`
- [ ] Feed faster than inference → `dropped` grows, no memory growth, no backlog
- [ ] Stop → device back to `live_camera`, seat states reset

## 6. End to end
- [ ] Each seat change → one row in `seat_events` (correct `seat_id`, `event_id` unique)
- [ ] Snapshot rows + Storage objects appear; dashboard shows the image with badges
- [ ] Dashboard developer mode OFF hides technical values; ON shows them with the admin token
