# Test video (virtual camera)

The `video-feeder` container plays an MP4 from `test-videos/`, samples frames at a fixed FPS, resizes them
(default 320×240) and JPEG-encodes them. No MP4 decoding on the ESP32.

```bash
cp video_feeder/.env.example video_feeder/.env
docker compose --profile test-video up -d --build video-feeder
curl -X POST "http://127.0.0.1:8090/config?video=demo.mp4&fps=3&loop=1&width=320&height=240"
curl -X POST http://127.0.0.1:8090/play     # /pause /stop
curl http://127.0.0.1:8090/status
```

## Path A — `TARGET=esp32` (hardware: validates on-device AI)
1. Firmware with `XSV_WEB_TOKEN` set; feeder `ESP32_URL=http://<device-ip>`, `ESP32_WEB_TOKEN`.
2. On play the feeder switches the device to `test_video` (`POST /api/mode`), then posts each JPEG to
   `POST /api/test-frame`. On stop it switches back to `live_camera`.
3. The device decodes the JPEG size from its SOF header (never trusts the client), then runs the **same**
   `inference::classify()` → seat mapper → state machines → MQTT path as camera frames.
4. Events are flagged `is_test: true`, `source: "test_video"`; seat states are reset on every mode switch.

**Back-pressure:** the device has one pending slot; a newer frame replaces an unprocessed one (`dropped`
counter in the response). The feeder sends synchronously and never catches up, so no backlog forms.
Each response includes the device's latest classification (`last_result`).

## Path B — `TARGET=mqtt` (no hardware: validates MQTT → bridge → Supabase → dashboard)
Publishes as test device `TEST_DEVICE_ID` (create it: `sh mqtt/scripts/add-user.sh xiao-test-01`).
States come from a **ground-truth labels CSV** (`<video>.labels.csv`), *not* from AI; events use fixed
`confidence: 1.0`, `inference_ms: 0`, and are flagged as test data. Snapshots (JPEG frames) are published on
change and every 30 s. View them in the dashboard with "테스트 영상 데이터 보기".

## What each path proves
| | Path A (ESP32) | Path B (Docker only) |
|---|---|---|
| Edge Impulse on-device inference | ✅ | ❌ |
| Seat mapping / debouncing on device | ✅ | ❌ (labels) |
| MQTT TLS / ACL / bridge / Supabase / dashboard | ✅ | ✅ |
