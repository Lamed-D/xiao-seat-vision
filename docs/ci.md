# CI (`.github/workflows/ci.yml`)

Triggers: `push`, `pull_request`, `workflow_dispatch`. No Supabase/MQTT secrets are needed.

| Job | What it does |
|---|---|
| `firmware` | Arduino CLI 1.5.1 → esp32 core **3.3.12** (same as the local Arduino IDE) → compile `firmware/xiao_seat_vision` with `esp32:esp32:XIAO_ESP32S3:PSRAM=opi,FlashSize=8M,PartitionScheme=default_8MB,USBMode=hwcdc,CDCOnBoot=default`. If `firmware/ei-model/*.zip` exists, installs it and compiles again with the model. Uploads `.bin/.elf`. The job summary states whether the model was integrated. |
| `firmware-logic` | host g++: occupancy state machine + seat mapper tests |
| `bridge` | pytest: JSON/topic validation, duplicates, retry/backoff, mocked Supabase HTTP |
| `video-feeder` | pytest: frame sampling/resizing/looping, label parsing |
| `dashboard` | `node --test`: developer-mode OFF hides technical data |
| `supabase` | `deno fmt --check`, `deno lint`, `deno check`, `deno test`, migrations on PostgreSQL 15 |
| `docker` | `tests/integration/run.sh`: compose config/build, mosquitto TLS/auth/ACL, bridge → mock functions, persistence |
| `commit-messages` | PRs only: every commit subject + PR title must be Conventional Commits |

## Honest limits
- A green firmware job means **it compiles** — not that the camera, PSRAM, AI or MQTT work on the board.
- Without `firmware/ei-model/*.zip` no AI code path is compiled (`model_not_integrated` build only).
- Supabase is tested with an in-memory fake and a stubbed Postgres, not a real project; Storage upload
  and signed URLs are only exercised after deployment.
- Hardware tests: [hardware-test.md](hardware-test.md).

## Things to check on GitHub
- Actions tab → all jobs green; `firmware` summary shows core version and model status.
- Artifact `firmware-esp32-3.3.12-<sha>` contains `xiao_seat_vision.ino.bin`.
- Settings → Branches: protect `main`, require these checks, allow squash merge (PR title is checked).
- When upgrading the Arduino IDE core, update `ESP32_CORE_VERSION` in the workflow in the same PR.
