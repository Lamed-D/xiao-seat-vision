# Setup (end to end)

1. **Tools**: Arduino IDE 2.x + esp32 core **3.3.12**, Docker Desktop, (optional) Node 20+ for `npx supabase`.
2. **Certificates & users** (PC):
   ```bash
   sh mqtt/scripts/gen-dev-certs.sh <PC-LAN-IP> [<pc-hostname>]
   sh mqtt/scripts/add-user.sh xiao-01
   sh mqtt/scripts/add-user.sh xiao-seat-vision-bridge
   sh mqtt/scripts/add-user.sh healthcheck <password>   # same as MQTT_HEALTHCHECK_PASSWORD in .env
   ```
   Windows: run in Git Bash. If `openssl` is missing the script uses a container.
   Give the PC a fixed/DHCP-reserved IP — the certificate SAN contains it.
3. **Supabase**: create a project, then follow [supabase.md](supabase.md) (migrations, secrets, deploy).
4. **Env files**: `cp .env.example .env`, `cp bridge/.env.example bridge/.env`, fill in.
5. **Start**: `docker compose up -d --build` → `docker compose ps` (all `healthy`).
6. **Firmware**: copy `config.example.h` → `config.h`, paste `mqtt/certs/public/ca.crt` into
   `XSV_MQTT_CA_CERT`, set `XSV_MQTT_URI=mqtts://<PC-LAN-IP>:8883`, upload.
7. **Verify**: serial `[mqtt] connected`, `docker compose logs -f mqtt-bridge` shows `status ... stored`.
8. **Dashboard**: http://127.0.0.1:8080 → settings.

## Environment variables

| File | Variable | Purpose |
|---|---|---|
| `.env` | `MQTT_BIND_ADDRESS`, `MQTT_TLS_PORT` | where 8883 is published |
| `.env` | `MQTT_HEALTHCHECK_PASSWORD` | broker healthcheck user |
| `.env` | `DASHBOARD_API_URL`, `DASHBOARD_PORT` | dashboard defaults |
| `bridge/.env` | `MQTT_USERNAME/PASSWORD`, `SUPABASE_FUNCTIONS_URL`, `BRIDGE_API_TOKEN` | bridge |
| `video_feeder/.env` | `TARGET`, `ESP32_URL`, `ESP32_WEB_TOKEN`, `TEST_DEVICE_*` | test video |
| `config.h` | `XSV_*` | firmware |
| Supabase secrets | `BRIDGE_API_TOKENS`, `DASHBOARD_*` | Edge Functions |

## Troubleshooting
- `[mqtt] transport error ... cert_flags=0x..` → wrong CA or the URI host is not in the certificate SAN.
  Regenerate with the right IP (`FORCE=1`) and update the device. **Do not disable verification.**
- `connection refused by broker (code 5)` → username/password, or username ≠ device id.
- ESP32 cannot reach the broker → Windows firewall (8883 inbound, private network), same Wi-Fi/VLAN.
- COM port busy → close the Arduino IDE Serial Monitor before `capture_snapshot.py` / `esptool`.
