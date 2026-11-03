# MQTT broker (Eclipse Mosquitto 2.0.22, Docker)

Files: `mosquitto.conf` (committed), `acl` (committed), `scripts/` (committed),
`certs/` and `secrets/` (generated, **git-ignored**).

## Listeners
| Port | Use | Published to host |
|---|---|---|
| 8883 | MQTT over TLS (ESP32 on the LAN, bridge) | yes (`MQTT_BIND_ADDRESS`, default all interfaces) |
| 1883 | plaintext, internal Docker network only (healthcheck) | **no** |

Development vs. production:
- **Dev** (this repo): self-signed CA from `gen-dev-certs.sh`, broker on your PC's LAN.
- **Prod**: certificate from a public CA for a real hostname (e.g. Let's Encrypt) in `certs/broker/`
  (`server.crt` = full chain, `server.key`, `ca.crt`); bind 8883 to the LAN/VPN interface, firewall it,
  never expose 1883; rotate all passwords; keep `ca.key` offline.

## 1. Certificates
```bash
sh mqtt/scripts/gen-dev-certs.sh 192.168.0.10 my-pc.local
```
SAN always contains `mosquitto` (bridge inside Docker), `localhost`, `127.0.0.1` plus your arguments.
The ESP32 must connect with one of the given names/IPs — **not** `localhost` or `mosquitto`.

Register the CA on the ESP32 (`config.h`):
```cpp
#define XSV_MQTT_URI "mqtts://192.168.0.10:8883"
#define XSV_MQTT_CA_CERT R"PEM(
-----BEGIN CERTIFICATE-----
...contents of mqtt/certs/public/ca.crt...
-----END CERTIFICATE-----
)PEM"
```

## 2. Users (password file with PBKDF2 hashes)
```bash
sh mqtt/scripts/add-user.sh xiao-01                    # device: username == device_id (ACL)
sh mqtt/scripts/add-user.sh xiao-seat-vision-bridge     # bridge
sh mqtt/scripts/add-user.sh healthcheck <password>      # = MQTT_HEALTHCHECK_PASSWORD in .env
docker compose restart mosquitto                         # after changes
```

## 3. ACL (`acl`)
- devices: `write xiao-seat-vision/%u/{events,status,telemetry,snapshot}` only
- bridge: `read xiao-seat-vision/+/{events,status,telemetry,snapshot}`
- healthcheck: `read $SYS/broker/uptime`
Anything else is denied (a device cannot publish as another device — verified by the integration test).

## 4. Run / observe
```bash
docker compose up -d --build
docker compose ps
docker compose logs -f mosquitto
# subscribe as the bridge user (from inside the Docker network):
docker run --rm --network xiao-seat-vision_default -v "$PWD/mqtt/certs/public:/c:ro" eclipse-mosquitto:2.0.22 \
  mosquitto_sub -h mosquitto -p 8883 --cafile /c/ca.crt -u xiao-seat-vision-bridge -P <pw> -t 'xiao-seat-vision/#' -v
```
Note: a second client with the bridge's client id would kick the bridge; `mosquitto_sub` uses a random id.

## Persistence limits
`persistence true`, `autosave_interval 60`: messages queued for the bridge survive a clean restart; on a
crash up to 60 s of queued messages can be lost. `max_queued_messages 5000` per persistent client.
