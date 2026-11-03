#!/usr/bin/env sh
# Adds/updates a Mosquitto user in mqtt/secrets/passwd (git-ignored, hashed).
#
#   sh mqtt/scripts/add-user.sh <username> [password]
#
# Devices: username MUST equal the device_id (ACL uses %u), e.g. xiao-01.
# Bridge:  xiao-seat-vision-bridge.  Healthcheck: healthcheck.
# Without a password argument a random one is generated and printed once.
# After changing users: docker compose restart mosquitto
set -eu

USER_NAME="${1:?usage: $0 <username> [password]}"
PASS="${2:-$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')}"
DIR="$(cd "$(dirname "$0")/.." && pwd)/secrets"
mkdir -p "$DIR"
[ -f "$DIR/passwd" ] || : > "$DIR/passwd"

# On Linux/macOS run as the current user so the file is not owned by root (otherwise
# the chmod below fails). Docker Desktop on Windows does not map ownership.
USER_OPT=""
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) USER_OPT="--user $(id -u):$(id -g)" ;;
esac
# shellcheck disable=SC2086
MSYS_NO_PATHCONV=1 docker run --rm $USER_OPT -v "$DIR:/s" eclipse-mosquitto:2.0.22 \
  mosquitto_passwd -b /s/passwd "$USER_NAME" "$PASS"
# Broker runs as uid 1883 in the container; file holds only PBKDF2 hashes.
chmod 644 "$DIR/passwd"

echo "user '$USER_NAME' written to $DIR/passwd"
if [ "$#" -lt 2 ]; then
  echo "generated password (store it now, it is not shown again): $PASS"
fi
