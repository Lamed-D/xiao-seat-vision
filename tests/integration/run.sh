#!/usr/bin/env bash
# Docker-only integration test (no ESP32, no Supabase project):
#   compose config/build -> mosquitto (TLS, auth, ACL) -> mqtt-bridge -> mock Edge Functions
#
# Checks: anonymous refused, wrong password refused, ACL blocks cross-device publish,
# plaintext port not published, events/status/telemetry forwarded, duplicate event_id
# not re-posted, events published while the bridge is down are delivered after restart
# (persistent session + manual ack).
#
# Usage (repo root): bash tests/integration/run.sh
# It refuses to run if .env / bridge/.env / mqtt/certs / mqtt/secrets already exist,
# so it never overwrites a real local setup. Everything it creates is removed at the end.
set -euo pipefail
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PROJECT="xsv-it-$$"
NET="${PROJECT}_default"
IMG=eclipse-mosquitto:2.0.22
PASS_DEV="dev-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
PASS_OTHER="oth-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
PASS_BRIDGE="brg-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
PASS_HC="hc-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
TOKEN="$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')"
FAIL=0

for f in .env bridge/.env mqtt/certs mqtt/secrets; do
  if [ -e "$f" ]; then echo "refusing to run: $f exists (local setup would be overwritten)"; exit 2; fi
done

compose() { docker compose -p "$PROJECT" "$@"; }
cleanup() {
  echo "--- cleanup"
  compose logs --no-color mqtt-bridge 2>/dev/null | tail -n 40 || true
  docker rm -f "${PROJECT}-mock" >/dev/null 2>&1 || true
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf mqtt/certs mqtt/secrets .env bridge/.env
}
trap cleanup EXIT

check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; FAIL=1; fi; }
mq() { docker run --rm --network "$NET" -v "$ROOT/mqtt/certs/public:/c:ro" "$IMG" "$@"; }

echo "--- setup (dev certs, users, env)"
sh mqtt/scripts/gen-dev-certs.sh 127.0.0.1 >/dev/null
sh mqtt/scripts/add-user.sh xiao-test-01 "$PASS_DEV" >/dev/null
sh mqtt/scripts/add-user.sh xiao-other "$PASS_OTHER" >/dev/null
sh mqtt/scripts/add-user.sh xiao-seat-vision-bridge "$PASS_BRIDGE" >/dev/null
sh mqtt/scripts/add-user.sh healthcheck "$PASS_HC" >/dev/null
printf 'MQTT_HEALTHCHECK_PASSWORD=%s\nMQTT_BIND_ADDRESS=127.0.0.1\nMQTT_TLS_PORT=18883\nDASHBOARD_PORT=18080\n' "$PASS_HC" > .env
cat > bridge/.env <<EOF
MQTT_USERNAME=xiao-seat-vision-bridge
MQTT_PASSWORD=$PASS_BRIDGE
MQTT_CLIENT_ID=xsv-it-bridge
SUPABASE_FUNCTIONS_URL=http://${PROJECT}-mock:8000/functions/v1
BRIDGE_TEST_ALLOW_HTTP=1
BRIDGE_API_TOKEN=$TOKEN
LOG_LEVEL=INFO
EOF

echo "--- docker compose config / build"
compose config --quiet
compose build --quiet mqtt-bridge dashboard

echo "--- start broker + mock functions"
compose up -d --wait mosquitto
docker run -d --name "${PROJECT}-mock" --network "$NET" -e MOCK_TOKEN="$TOKEN" \
  -v "$ROOT/tests/integration/mock_functions.py:/m.py:ro" python:3.12.7-slim python /m.py >/dev/null

TLS="-h mosquitto -p 8883 --cafile /c/ca.crt"
EVENT='{"event_id":"xiao-test-01-it-000001","device_id":"xiao-test-01","seat_id":"seat_01","state":"occupied","confidence":0.9,"inference_ms":120}'

echo "--- broker security"
check "anonymous connection refused" "! mq mosquitto_pub $TLS -t xiao-seat-vision/x/events -m x -q 1 2>/dev/null"
check "wrong password refused" "! mq mosquitto_pub $TLS -u xiao-test-01 -P wrong -t xiao-seat-vision/xiao-test-01/events -m x -q 1 2>/dev/null"
# `docker compose port` output for unpublished ports differs between versions/platforms,
# so list all published mappings of the container and make sure 1883 is not among them.
check "plaintext 1883 not published on host" "docker port \$(compose ps -q mosquitto) | grep -q '^8883/' && ! docker port \$(compose ps -q mosquitto) | grep -q '^1883/'"
BROKER_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$(compose ps -q mosquitto)")"
# Same broker, but addressed by its container IP, which is NOT in the certificate SAN.
# -d makes mosquitto_pub print the TLS reason instead of only "Protocol error".
SAN_OUT="$(mq mosquitto_pub -d -h "$BROKER_IP" -p 8883 --cafile /c/ca.crt -u xiao-test-01 -P "$PASS_DEV" \
  -t xiao-seat-vision/xiao-test-01/events -m x -q 1 2>&1 || true)"
echo "    client output: $(echo "$SAN_OUT" | grep -i 'error' | tr '\n' ' ')"
check "TLS rejects host not in certificate SAN ($BROKER_IP)" "echo \"\$SAN_OUT\" | grep -qi 'verification failed'"
check "TLS by service name (in SAN) succeeds" "mq mosquitto_pub $TLS -u xiao-test-01 -P $PASS_DEV -t xiao-seat-vision/xiao-test-01/status -m '{\"device_id\":\"xiao-test-01\",\"status\":\"online\"}' -q 1"

# ACL: device may not publish as another device (broker drops silently)
docker run -d --name "${PROJECT}-sub" --network "$NET" -v "$ROOT/mqtt/certs/public:/c:ro" "$IMG" \
  mosquitto_sub $TLS -u xiao-seat-vision-bridge -P "$PASS_BRIDGE" -i xsv-it-acl-check -t 'xiao-seat-vision/xiao-other/events' -C 1 -W 6 >/dev/null
sleep 1
mq mosquitto_pub $TLS -u xiao-test-01 -P "$PASS_DEV" -t xiao-seat-vision/xiao-other/events -m spoof -q 1 || true
check "ACL blocks cross-device publish" "[ \"\$(docker wait ${PROJECT}-sub)\" != 0 ] && [ -z \"\$(docker logs ${PROJECT}-sub 2>/dev/null)\" ]"
docker rm -f "${PROJECT}-sub" >/dev/null

echo "--- bridge forwarding"
compose up -d --wait mqtt-bridge
pub() { mq mosquitto_pub $TLS -u xiao-test-01 -P "$PASS_DEV" -t "xiao-seat-vision/xiao-test-01/$1" -m "$2" -q 1; }
pub events "$EVENT"
pub events "$EVENT"  # QoS1 redelivery / duplicate
pub events '{"event_id":"xiao-test-01-it-bad","device_id":"xiao-test-01","state":"maybe","confidence":0.9,"inference_ms":1}'
pub status '{"device_id":"xiao-test-01","status":"online"}'
pub telemetry '{"device_id":"xiao-test-01","seats":[],"memory":{"heap_free":1}}'
sleep 4

reqs() { docker exec "${PROJECT}-mock" python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/_requests').read().decode())"; }
count() { reqs | python -c "import json,sys;r=json.load(sys.stdin);print(sum(1 for x in r if $1))"; }
check "event forwarded once (duplicate skipped)" "[ \$(count \"x['fn']=='log-detection' and x['body'].get('event_id')=='xiao-test-01-it-000001'\") = 1 ]"
check "invalid event never forwarded" "[ \$(count \"x['body'].get('event_id')=='xiao-test-01-it-bad'\") = 0 ]"
check "status + telemetry forwarded" "[ \$(count \"x['fn']=='device-status'\") -ge 2 ]"
check "bridge authenticated with token" "[ \$(count \"not x['authorized']\") = 0 ]"

echo "--- dashboard"
compose up -d --wait dashboard
check "dashboard serves index on 127.0.0.1 only" "docker exec \$(compose ps -q dashboard) wget -q -O - http://127.0.0.1/ | grep -q '좌석 현황' && compose port dashboard 80 | grep -q '^127.0.0.1:'"
check "dashboard config.js generated" "docker exec \$(compose ps -q dashboard) cat /usr/share/nginx/html/config.js | grep -q 'XSV_CONFIG'"

echo "--- persistence: event published while bridge is down"
compose stop mqtt-bridge >/dev/null
pub events '{"event_id":"xiao-test-01-it-000002","device_id":"xiao-test-01","seat_id":"seat_02","state":"empty","confidence":1,"inference_ms":100}'
compose start mqtt-bridge >/dev/null
sleep 6
check "queued event delivered after bridge restart" "[ \$(count \"x['body'].get('event_id')=='xiao-test-01-it-000002'\") = 1 ]"
check "bridge container healthy" "[ \"\$(docker inspect -f '{{.State.Health.Status}}' \$(compose ps -q mqtt-bridge))\" = healthy ] || sleep 20 && [ \"\$(docker inspect -f '{{.State.Health.Status}}' \$(compose ps -q mqtt-bridge))\" = healthy ]"

compose ps
if [ "$FAIL" -ne 0 ]; then echo "INTEGRATION TEST FAILED"; exit 1; fi
echo "INTEGRATION TEST PASSED"
