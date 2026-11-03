FROM nginx:1.27.2-alpine

COPY index.html app.js viewmodel.js style.css /usr/share/nginx/html/
COPY docker-entrypoint.d/40-xsv-config.sh /docker-entrypoint.d/40-xsv-config.sh
RUN chmod +x /docker-entrypoint.d/40-xsv-config.sh \
 && printf 'window.XSV_CONFIG = { apiUrl: "" };\n' > /usr/share/nginx/html/config.js \
 && chown nginx /usr/share/nginx/html/config.js

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
