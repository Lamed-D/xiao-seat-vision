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

Not yet tested on hardware: Wi-Fi, MQTT/TLS, LWT, streaming, TEST_VIDEO input, Edge Impulse
inference, multi-seat detection, long-run stability → [hardware-test.md](hardware-test.md).

## Software — 2026-11-03 (local machine)

| Test | Command | Result |
|---|---|---|
| Firmware compile, PSRAM=opi | `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3:PSRAM=opi,FlashSize=8M,PartitionScheme=default_8MB ...` | ✅ 1,123,846 B (33 %), 0 sketch warnings (`--warnings all`) |
| Firmware compile, PSRAM=disabled | same with `PSRAM=disabled` | ✅ 1,118,700 B |
| Firmware compile **with** Edge Impulse model | — | ⏳ not possible yet (no model library) |
| Firmware logic (occupancy, seat mapper) | `sh firmware/tests/run.sh` (gcc:14 container) | ✅ 42 checks, 0 failures |
| Bridge | `pytest bridge/tests` | ✅ 33 passed |
| Video feeder | `pytest video_feeder/tests` (python:3.12 container) | ✅ 4 passed |
| Dashboard view model | `node --test dashboard/` | ✅ 4 passed |
| Edge Functions | `deno fmt --check && deno lint && deno check && deno test` | ✅ 15 passed |
| DB migrations | `sh supabase/tests/run_migration_tests.sh` (PostgreSQL 15) | ✅ all checks passed |
| Docker integration | `bash tests/integration/run.sh` | ✅ 14/14 (TLS/SAN, auth, ACL, bridge, duplicates, persistence, dashboard health) |
| Commit messages | `scripts/check_commits.py` | ✅ all commits conform |
| Supabase deployment | — | ⏳ not done (no project credentials here) |
| GitHub Actions run | — | ⏳ not run yet (branch not pushed) |
