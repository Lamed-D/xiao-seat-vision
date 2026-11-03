# Architecture

## Firmware (`firmware/xiao_seat_vision`, Arduino IDE 2.x, esp32 core 3.3.12 / ESP-IDF 5.5)

| Module | Responsibility |
|---|---|
| `xiao_seat_vision.ino` | `setup()/loop()`, millis() scheduling, per-seat state machines, events |
| `camera_manager` | XIAO Sense pin map (Seeed `camera_pins.h`), QVGA JPEG, 2 PSRAM buffers + `GRAB_LATEST`, sensor PID detection, `FrameGuard` (RAII return of frame buffers) |
| `inference_manager` | Edge Impulse wrapper. FOMO → person detections in full-frame coordinates; classification → single-seat fallback. Without a model: `ModelNotIntegrated` (no fake output) |
| `seat_mapper` | ROI matching policy (pure C++, host-tested) |
| `occupancy_manager` | Debounce: N (=3) consecutive observations per seat (pure C++, host-tested) |
| `network_manager` | Wi-Fi STA, non-blocking reconnect with backoff 10→60 s, SNTP |
| `mqtt_manager` | `esp-mqtt` (own FreeRTOS task): mqtts:// only, CA verification, user/password, LWT, QoS 1 event queue with PUBACK tracking |
| `report_manager` | Telemetry + snapshot JSON (PSRAM buffers) |
| `stream_manager` | Optional MJPEG preview (port 81) + control page/API (port 80) |
| `test_input` | TEST_VIDEO mode: single-slot JPEG input over HTTP |

### Loop and blocking
`loop()` never sleeps longer than 5 ms. The only blocking work is one capture + inference.
MQTT connect/TLS/reconnect run inside the esp-mqtt task; HTTP servers run in httpd tasks.

### Camera ownership / frame buffers
Only the loop task calls `esp_camera_fb_get()`; every frame is returned by `FrameGuard` at scope exit.
The stream handler and test input never touch the driver: they exchange **copies** in PSRAM protected by
FreeRTOS mutexes. Inference allocates its RGB888 buffer per frame in PSRAM and frees it before returning.

### Multi-seat matching (FOMO)
1. Detections below `XSV_PERSON_MIN_CONFIDENCE` are ignored.
2. A detection belongs to the seat whose ROI contains its **centroid**.
3. Overlap: the seat with the closest ROI centre (normalized by ROI size) wins only if clearly closer
   (`best × 1.25 < second`); otherwise it is ambiguous → those seats observe `unknown` for that frame.
4. A detection is assigned to at most one seat (no double counting).
5. Each seat has its own `OccupancyManager` — seats never influence each other.
6. No valid inference for `XSV_STALE_MS` (15 s) → all seats forced to `unknown` (event emitted).

Empty-seat confidence: FOMO only returns detections above threshold, so "no person in ROI" has no model
score. The firmware reports `1.0` for a confirmed `empty` observation. `unknown` uses `0`.

### Limitations of centroid-based seat assignment (document for installers)
- FOMO outputs object **centroids** on a coarse grid (input/8: 12×12 cells at 96×96, 20×20 at 160×160),
  not seat contact. A person standing behind/in front of a seat may be assigned to it.
- Perspective: with an oblique camera, a person's centroid can fall into the neighbouring seat's ROI.
  Mount the camera high and as frontal as possible; leave gaps between ROIs; draw ROIs around the
  torso area, not the whole chair.
- Occlusion between neighbouring people merges/loses detections.
- The model sees the **centre crop** of the 4:3 frame (square input → x ∈ [0.125, 0.875]); ROIs outside are
  reported at boot.
- Practical seat count (estimate, **not yet validated on hardware**): 96×96 input → 2–4 seats in a row;
  160×160 → 4–6. Each person needs ≳2 grid cells of separation. Validate with the hardware checklist.

## Message flow and delivery guarantees
1. ESP32 publishes events with QoS 1 and keeps them in a 16-slot RAM queue until PUBACK (resends after
   15 s or reconnect). The queue is lost on reboot/power loss.
2. Mosquitto: persistence on (autosave 60 s). Queued messages for the bridge's persistent session survive
   a broker restart, except those received in the last ≤ 60 s before a crash.
3. Bridge: `clean_session=False`, fixed client id, QoS 1, **manual ack** → PUBACK only after the Edge Function
   returned 2xx (stored/duplicate) or a permanent 4xx (then written to `dead_letter.jsonl`). Bridge down →
   broker keeps the messages (max 5000 queued). 401/403/404/5xx/network → retried with backoff until success.
4. Idempotency: `event_id = <device>-<boot-random>-<seq>`, UNIQUE in the DB; the function returns
   `200 duplicate` for repeats. Snapshots likewise by `snapshot_id`.
5. Telemetry (3 attempts) and snapshots (5 attempts) are best-effort.

Not guaranteed in the MVP: events produced while the device is offline for longer than its queue (16),
events lost on device reboot before PUBACK, broker disk loss.

## Security
- MQTT: TLS only from outside (1883 not published), anonymous off, PBKDF2 password file, ACL per device
  (`username == device_id`, write only own topics), bridge read-only.
- Supabase: RLS on all tables with no policies; only the service role (inside Edge Functions) writes/reads.
  Bridge token ≠ service role key; dashboard viewer/admin tokens separate. Secrets only in Supabase secrets,
  `.env` files (git-ignored) and `config.h` (git-ignored). Functions never log token values.
- Local web on the ESP32 (stream/test input): token in query string over plain HTTP — LAN only.
