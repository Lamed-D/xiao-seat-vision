# Dashboard

Static page (`dashboard/`, nginx container, http://127.0.0.1:8080). No build step.

## Token proxy (local, no login)
If `.env` contains `DASHBOARD_VIEWER_TOKEN` (and optionally `DASHBOARD_ADMIN_TOKEN`), the container serves
`/api/dashboard` as a reverse proxy to `dashboard-api` and adds the token server-side
(`view=summary` → viewer, `view=debug|layout` → admin). The browser never receives the tokens.
Because this grants token-less access, the container is published on **127.0.0.1 only** — keep it that way.
Without these variables the proxy returns 503 and users must enter tokens in the settings.
A token typed into the settings always takes precedence (direct call to Supabase).

## Normal view (developer mode OFF — default)
- Counts: total / occupied / empty (/ unknown if any)
- Latest snapshot with one badge per seat (green = empty, orange = occupied, grey = unknown)
- Seat list, last update time
- Hidden: confidence, inference time, FPS, memory, MQTT details, model version, coordinates, raw JSON, logs.
  The summary API does not even return them (tested in `supabase/functions/tests/dashboard_test.ts`),
  and the view model drops them (tested in `dashboard/viewmodel.test.js`).

## Developer mode (settings → 개발자 모드)
Adds: per-seat confidence and inference ms, ROI rectangles + FOMO centroids over the image, ROI numbers,
inference time/FPS, heap/PSRAM, MQTT status + last seen, model name/version, recent events, raw JSON.
Data comes from `dashboard-api?view=debug`, which requires the **admin token** — the toggle itself is
only a display preference stored in the browser.

## Streaming
Independent of developer mode. Needs the ESP32 address and `XSV_WEB_TOKEN` in settings. The button calls
the device (`POST /api/stream?enable=1|0`) which really starts/stops capture + encoding + sending; the
`<img>` connection is closed when turned off.

## Seat layout API (for a future drag-and-drop editor)
`PUT dashboard-api?view=layout` (admin):
```json
{ "device_id": "xiao-01",
  "seats": [ { "seat_id": "seat_01", "label": "좌석 1",
               "roi": { "x": 0.05, "y": 0.25, "width": 0.25, "height": 0.60 } } ] }
```
Stored in `seat_layouts` (validated 0..1, unique ids). Labels/ROIs from this table override the
device-reported ones **for display**. Not done yet: pushing the layout to the device (planned: retained
`xiao-seat-vision/<device>/config` topic + NVS on the ESP32). Until then edit `seats_config.h`.

## Stale data
A device that is `offline` (LWT) or silent for 3 minutes shows all its seats as `unknown`.
