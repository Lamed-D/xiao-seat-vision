#!/usr/bin/env sh
# Generates a DEVELOPMENT CA + broker certificate for Mosquitto (mqtt/certs/, git-ignored).
#
#   sh mqtt/scripts/gen-dev-certs.sh 192.168.0.10 [my-pc.local ...]
#
# Every argument is added to the certificate SAN: pass the LAN IP and/or hostname the
# ESP32 uses in XSV_MQTT_URI. "mosquitto" (Docker service name, used by the bridge),
# "localhost" and 127.0.0.1 are always included. The first argument is also the CN
# (mbedTLS on the ESP32 matches the host against CN and DNS SANs).
#
# Layout:
#   certs/broker/   server.crt, server.key, ca.crt -> mounted into mosquitto only
#   certs/public/   ca.crt                         -> mounted into clients (bridge, feeder)
#   certs/private/  ca.key                         -> never mounted, never committed
#
# Uses openssl from the host if present, otherwise an alpine/openssl container.
set -eu

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <lan-ip-or-hostname> [more names...]" >&2
  exit 2
fi

DIR="$(cd "$(dirname "$0")/.." && pwd)/certs"
mkdir -p "$DIR/broker" "$DIR/public" "$DIR/private"
if [ -f "$DIR/private/ca.key" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "certs already exist in $DIR (FORCE=1 to regenerate; devices then need the new ca.crt)" >&2
  exit 1
fi

CN="$1"
SAN="DNS:mosquitto,DNS:localhost,IP:127.0.0.1"
for name in "$@"; do
  if echo "$name" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    SAN="$SAN,IP:$name,DNS:$name"   # DNS:<ip> too: mbedTLS compares the host string
  else
    SAN="$SAN,DNS:$name"
  fi
done

cat > "$DIR/server.ext" <<EOF
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$SAN
EOF

run_openssl() {
  if command -v openssl >/dev/null 2>&1; then
    (cd "$DIR" && MSYS_NO_PATHCONV=1 openssl "$@")
  else
    MSYS_NO_PATHCONV=1 docker run --rm -v "$DIR:/w" -w /w alpine/openssl:3.3.2 "$@"
  fi
}

run_openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 825 \
  -subj "/CN=xiao-seat-vision dev CA" -keyout private/ca.key -out public/ca.crt
run_openssl req -newkey rsa:2048 -nodes -sha256 \
  -subj "/CN=$CN" -keyout broker/server.key -out server.csr
run_openssl x509 -req -in server.csr -CA public/ca.crt -CAkey private/ca.key \
  -CAcreateserial -days 825 -sha256 -extfile server.ext -out broker/server.crt
rm -f "$DIR/server.csr" "$DIR/server.ext" "$DIR/public/ca.srl" "$DIR/ca.srl"
cp "$DIR/public/ca.crt" "$DIR/broker/ca.crt"

# The broker runs as uid 1883 inside the container and must read the key.
# Dev only: make broker files readable. ca.key stays private.
chmod 644 "$DIR/broker/server.key" "$DIR/broker/server.crt" "$DIR/broker/ca.crt" "$DIR/public/ca.crt"
chmod 600 "$DIR/private/ca.key"

echo "created in $DIR:"
echo "  public/ca.crt      -> firmware config.h (XSV_MQTT_CA_CERT); mounted into the bridge"
echo "  broker/server.crt  SAN: $SAN"
echo "  private/ca.key     -> keep private, never commit"
