#!/bin/sh
# Runs before nginx starts.
#  1. Writes config.js (API URL + whether the token proxy is available) — no secrets in it.
#  2. Writes the nginx site config. If DASHBOARD_VIEWER_TOKEN is set, /api/dashboard is a
#     reverse proxy to the dashboard-api Edge Function that adds the token server-side:
#       view=summary         -> viewer token
#       view=debug / layout  -> admin token (developer mode, ROI editing)
#     The browser never sees the tokens. compose.yaml publishes this container on
#     127.0.0.1 only, so token-less access is limited to this PC.
set -eu

clean() { printf '%s' "$1" | tr -d '"\\\r\n;{}$ '; }
api_url=$(clean "${DASHBOARD_API_URL:-}")
viewer=$(clean "${DASHBOARD_VIEWER_TOKEN:-}")
admin=$(clean "${DASHBOARD_ADMIN_TOKEN:-}")

proxy=false
if [ -n "$api_url" ] && [ -n "$viewer" ]; then
  proxy=true
fi

printf 'window.XSV_CONFIG = { apiUrl: "%s", proxy: %s, proxyAdmin: %s };\n' \
  "$api_url" "$proxy" "$([ -n "$admin" ] && echo true || echo false)" \
  > /usr/share/nginx/html/config.js

conf=/etc/nginx/conf.d/default.conf
{
  if [ "$proxy" = true ]; then
    echo "map \$arg_view \$xsv_token {"
    echo "  default \"$viewer\";"
    echo "  summary \"$viewer\";"
    echo "  debug   \"${admin:-$viewer}\";"
    echo "  layout  \"${admin:-$viewer}\";"
    echo "}"
  fi
  echo "server {"
  echo "  listen 80;"
  echo "  root /usr/share/nginx/html;"
  echo "  location / { try_files \$uri \$uri/ =404; }"
  echo "  location = /config.js { add_header Cache-Control no-store; }"
  if [ "$proxy" = true ]; then
    echo "  location = /api/dashboard {"
    echo "    proxy_pass $api_url;"
    echo "    proxy_set_header x-dashboard-token \$xsv_token;"
    echo "    proxy_set_header Origin \"\";"
    echo "    proxy_ssl_server_name on;"
    echo "    proxy_ssl_verify on;"
    echo "    proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;"
    echo "    proxy_ssl_verify_depth 4;"
    echo "    proxy_read_timeout 30s;"
    echo "  }"
  else
    echo "  location = /api/dashboard { return 503 '{\"error\":\"proxy_disabled\"}'; default_type application/json; }"
  fi
  echo "}"
} > "$conf"

echo "xsv-dashboard: token proxy $( [ "$proxy" = true ] && echo enabled || echo disabled )"
