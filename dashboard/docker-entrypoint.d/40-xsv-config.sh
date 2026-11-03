#!/bin/sh
# Writes config.js from environment at container start (no secrets: tokens are
# entered by each user in the dashboard settings and stay in their browser).
set -eu
api_url=$(printf '%s' "${DASHBOARD_API_URL:-}" | sed 's/["\\]//g')
printf 'window.XSV_CONFIG = { apiUrl: "%s" };\n' "$api_url" > /usr/share/nginx/html/config.js
