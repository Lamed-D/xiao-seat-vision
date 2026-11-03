# Test results

What has actually been executed, where, and what it proves. Anything not listed here has **not**
been verified. Update this file whenever a test is (re)run.

## Hardware — 2026-11-03

Board: XIAO ESP32S3 Sense on COM5 (USB VID 303A / PID 1001), firmware `feat/initial-mvp`,
Arduino IDE 2.x, esp32 core 3.3.12, Tools → PSRAM = OPI PSRAM, no `config.h` (network disabled),
no Edge Impulse model. Tool: `firmware/tools/camera_viewer.py COM5`.

| Check | Result |
|---|---|
| Chip / flash / core | ESP32-S3 rev2, 8 MB flash, core 3.3.12 ✅ |
| PSRAM | `psram: total=8388608 free=8249216` ✅ |
| Camera sensor | **OV3660** (`camera=ready sensor=OV3660`) ✅ |
| Frames | 320×240 JPEG, 6.8–7.3 KB, ~3.6 fps over USB serial, 103 frames ✅ |
| `cam_hal: FB-OVF` | 0 in 103 frames (after JPEG quality fix, see below) ✅ |
| Capture failures | 0 ✅ |
| Memory over the run | heap free 274 028 B, PSRAM free 8 249 216 B — unchanged ✅ |
| Image | focus/exposure OK; camera was tilted on the desk (mounting not final) |

Issues found and fixed on hardware:
- **PSRAM disabled in Arduino IDE** → `psram: NOT FOUND`, QQVGA DRAM fallback and repeated
  `cam_hal: FB-OVF`. Fix: Tools → PSRAM → OPI PSRAM (build size 1,118,7xx B = PSRAM off,
  1,123,8xx B = PSRAM on). Firmware now also uses JPEG quality 15 (QVGA) / 30 (fallback).

### On-device AI (mock model) — 2026-11-03
- [x] Model recognised at boot: `type=fomo`, input 96x96, label `person`, field of view x 0.125–0.875
- [x] EON + ESP-NN crashed (CORRUPT HEAP); fixed with TFLite export + arena x2 (see docs/edge-impulse.md)
- [x] 3-minute run: 180 inferences, **121–125 ms** each (+85 ms decode/resize), 0 crashes
- [x] Heap after Wi-Fi/TLS stayed ~155–157 KB free; PSRAM ~8.0 MB free
- [x] Seat events → MQTT (PUBACK) → bridge → `seat_events` (27 rows); snapshots → Storage + `seat_snapshots`;
      dashboard summary shows occupied/empty per seat
- [ ] **Accuracy**: the mock model (street pedestrians) reports persons on an empty desk scene → false
      positives and flapping seat states are expected. Retrain with photos from the real camera position.

### Network — 2026-11-03
`scripts/setup_local.py` (certs with SAN 172.16.139.140 / PC-LAMED, users, env files), `docker compose up`:
mosquitto / mqtt-bridge / dashboard healthy. Firmware with `config.h` uploaded via arduino-cli:
- [x] Wi-Fi connected (device IP 172.16.139.18, RSSI -68 dBm)
- [x] MQTT over TLS with CA + hostname verification: `[mqtt] connected`, broker log `New client connected ... as xiao-01`
- [x] Bridge received the retained `online` status; forwarding returns HTTP 404 until the Edge Functions are deployed (message kept and retried)
- Heap after Wi-Fi/TLS: free ~162 KB, max block ~117 KB; PSRAM free ~8.0 MB

Not yet tested on hardware: LWT offline, broker/Wi-Fi outage recovery, streaming, TEST_VIDEO input, Edge Impulse
inference, multi-seat detection, long-run stability → [hardware-test.md](hardware-test.md).

## Software — 2026-11-03 (local machine)

| Test | Command | Result |
|---|---|---|
| Firmware compile, PSRAM=opi | `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi,FlashSize=8M,PartitionScheme=default_8MB ...` | ✅ 1,123,846 B (33 %), 0 sketch warnings (`--warnings all`) |
| Firmware compile, PSRAM=disabled | same with `PSRAM=disabled` | ✅ 1,118,700 B |
| Firmware logic (occupancy, seat mapper) | `sh firmware/tests/run.sh` (gcc:14 container) | ✅ 42 checks, 0 failures |
| Bridge | `pytest bridge/tests` | ✅ 33 passed |
| Video feeder | `pytest video_feeder/tests` (python:3.12 container) | ✅ 4 passed |
| Dashboard view model | `node --test dashboard/viewmodel.test.js` | ✅ 4 passed |
| Edge Functions | `deno fmt --check && deno lint && deno check && deno test` | ✅ 15 passed |
| DB migrations | `sh supabase/tests/run_migration_tests.sh` (PostgreSQL 15) | ✅ all checks passed |
| Docker integration | `bash tests/integration/run.sh` | ✅ 14/14 (TLS/SAN, auth, ACL, bridge, duplicates, persistence, dashboard health) |
| Commit messages | `scripts/check_commits.py` | ✅ all commits conform |
| Supabase deployment | `db push --remote`, `secrets set`, `functions deploy` | ✅ 001+002 applied (PostgreSQL 17), 4 functions ACTIVE (`verify_jwt=false`); 401 without token, 405 on GET, 403 debug with viewer token |
| End to end (device → DB) | bridge log + `dashboard-api` | ✅ `xiao-01` status `online` and 30 s telemetry stored; summary: 3 seats `unknown` (no model) |
| Dashboard token proxy | `curl 127.0.0.1:8080/api/dashboard?view=summary|debug` | ✅ 200 / 200 without browser token; tokens absent from `config.js`; without tokens the proxy is disabled (503) |
| Edge Impulse pipeline | `scripts/edge_impulse.py upload / train / deploy` | ✅ 170 images, 423 boxes; FOMO 96×96 int8 trained, **validation F1 0.768 (mock pedestrian data)** |
| Firmware compile **with** model | arduino-cli, core 3.3.12, `config.h` define | ✅ 1,257,350 B (37 %), RAM 20 %, 0 sketch warnings |
| GitHub Actions run | `feat/initial-mvp` @ 5104621 | ✅ all jobs green ([run 41572132643](https://github.com/Lamed-D/xiao-seat-vision/actions/runs/41572132643)); Docker integration first failed on Linux only, reproduced with docker-in-docker and fixed |
