"""xiao-seat-vision dashboard server (FastAPI + uvicorn).

Serves the static dashboard and a small API in front of the Supabase `dashboard-api`
Edge Function:

  GET  /                      dashboard (static/, Cache-Control: no-cache)
  GET  /config.js             runtime config for the page (no secrets)
  GET  /api/dashboard?view=summary|debug[&data=test]   proxy, token added server-side
  PUT  /api/dashboard?view=layout                      proxy (admin token)
  GET  /api/dashboard?view=events[&data=test]          sit-down feed (+ person image URLs)
  GET  /api/people/{key}/{name}.jpg                    person crop of a snapshot
  GET  /healthz               liveness + configuration flags
  GET  /docs                  OpenAPI UI

Tokens come from DASHBOARD_VIEWER_TOKEN / DASHBOARD_ADMIN_TOKEN and are never sent to the
browser. Because this grants token-less access, compose.yaml publishes the port on
127.0.0.1 only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import dev
from .people import build_people, crop_jpeg

log = logging.getLogger("dashboard")
STATIC_DIR = Path(os.environ.get("STATIC_DIR", Path(__file__).resolve().parents[1] / "static"))


@dataclass(frozen=True)
class Settings:
    api_url: str = ""
    viewer_token: str = ""
    admin_token: str = ""
    timeout_s: float = 15.0
    feeder_url: str = ""
    # MQTT link for the developer test mode (device control via the broker)
    mqtt_host: str = ""
    mqtt_port: int = 8883
    mqtt_tls: bool = True
    mqtt_ca_cert: str = ""
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_prefix: str = "xiao-seat-vision"
    device_id: str = "xiao-01"

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            api_url=os.environ.get("DASHBOARD_API_URL", "").strip(),
            viewer_token=os.environ.get("DASHBOARD_VIEWER_TOKEN", "").strip(),
            admin_token=os.environ.get("DASHBOARD_ADMIN_TOKEN", "").strip(),
            timeout_s=float(os.environ.get("UPSTREAM_TIMEOUT_S", "15")),
            feeder_url=os.environ.get("FEEDER_URL", "").strip(),
            mqtt_host=os.environ.get("MQTT_HOST", "").strip(),
            mqtt_port=int(os.environ.get("MQTT_PORT", "8883")),
            mqtt_tls=os.environ.get("MQTT_TLS", "true").lower() in ("1", "true", "yes"),
            mqtt_ca_cert=os.environ.get("MQTT_CA_CERT", "").strip(),
            mqtt_username=os.environ.get("MQTT_USERNAME", "").strip(),
            mqtt_password=os.environ.get("MQTT_PASSWORD", "").strip(),
            mqtt_prefix=os.environ.get("MQTT_TOPIC_PREFIX", "xiao-seat-vision").strip(),
            device_id=os.environ.get("DEVICE_ID", "xiao-01").strip(),
        )

    @property
    def mqtt(self) -> bool:
        return bool(self.mqtt_host and self.mqtt_username and self.mqtt_password)

    @property
    def proxy(self) -> bool:
        return bool(self.api_url and self.viewer_token)

    def token_for(self, view: str) -> str:
        if view in ("debug", "layout"):
            return self.admin_token or self.viewer_token
        return self.viewer_token


class SnapshotCache:
    """Remembers the latest snapshots' signed URL + crops so thumbnails can be served."""

    # Must hold at least one entry per event card (feed limit 100) plus the live view;
    # entries are small until a thumbnail is requested.
    def __init__(self, size: int = 256) -> None:
        self._items: OrderedDict[str, dict] = OrderedDict()
        self._size = size

    @staticmethod
    def key(snapshot: dict) -> str:
        raw = f"{snapshot.get('device_id')}|{snapshot.get('created_at')}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def put(self, key: str, url: str, crops: dict[str, dict]) -> None:
        item = self._items.get(key, {"crops": {}})
        item["url"] = url  # latest signed URL for this snapshot
        item["crops"].update(crops)
        self._items[key] = item
        self._items.move_to_end(key)
        while len(self._items) > self._size:
            self._items.popitem(last=False)

    def get(self, key: str) -> dict | None:
        return self._items.get(key)


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None,
               link=None) -> FastAPI:
    settings = settings or Settings.from_env()
    cache = SnapshotCache()
    owns_link = False
    if link is None and settings.mqtt:
        from .mqtt_link import MqttLink

        link = MqttLink(settings.mqtt_host, settings.mqtt_port, settings.mqtt_username,
                        settings.mqtt_password, settings.mqtt_ca_cert or None, settings.mqtt_prefix,
                        settings.device_id, tls=settings.mqtt_tls)
        owns_link = True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # TLS verification stays on (httpx default).
        app.state.http = httpx.AsyncClient(timeout=settings.timeout_s, transport=transport)
        if owns_link:
            link.start()
        log.info("dashboard server: token proxy %s, admin %s",
                 "enabled" if settings.proxy else "disabled",
                 "available" if settings.admin_token else "missing")
        yield
        if owns_link:
            link.stop()
        await app.state.http.aclose()

    app = FastAPI(title="xiao-seat-vision dashboard", version="1.0.0", lifespan=lifespan)

    async def upstream(request: Request, view: str, data: str | None,
                       method: str = "GET", body: bytes | None = None) -> httpx.Response:
        params = {"view": view}
        if data == "test":
            params["data"] = "test"
        headers = {"x-dashboard-token": settings.token_for(view)}
        if body is not None:
            headers["content-type"] = "application/json"
        return await request.app.state.http.request(
            method, settings.api_url, params=params, headers=headers, content=body)

    def passthrough(resp: httpx.Response) -> Response:
        return Response(resp.content, status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "application/json"),
                        headers={"cache-control": "no-store"})

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "proxy": settings.proxy, "admin": bool(settings.admin_token)}

    @app.get("/config.js")
    async def config_js() -> Response:
        cfg = {"apiUrl": settings.api_url, "proxy": settings.proxy, "proxyAdmin": bool(settings.admin_token),
               "deviceControl": link is not None,
               "testVideo": bool(settings.feeder_url)}
        return Response(f"window.XSV_CONFIG = {json.dumps(cfg)};\n", media_type="application/javascript",
                        headers={"cache-control": "no-store"})

    @app.api_route("/api/dashboard", methods=["GET", "PUT"])
    async def dashboard(request: Request, view: str = "summary", data: str | None = None) -> Response:
        if not settings.proxy:
            return JSONResponse({"error": "proxy_disabled"}, status_code=503)
        if view not in ("summary", "debug", "layout", "events"):
            return JSONResponse({"error": "unknown_view"}, status_code=404)
        try:
            if request.method == "PUT":
                if view != "layout":
                    return JSONResponse({"error": "method_not_allowed"}, status_code=405)
                return passthrough(await upstream(request, view, data, "PUT", await request.body()))
            resp = await upstream(request, view, data)
            if view == "events":
                if resp.status_code == 404:
                    # Edge Function deployed before the events view existed.
                    return JSONResponse({"error": "events_view_not_deployed"}, status_code=501)
                if resp.status_code != 200:
                    return passthrough(resp)
                feed = resp.json()
                add_event_images(feed)
                return JSONResponse(feed, headers={"cache-control": "no-store"})
            if view != "summary" or resp.status_code != 200:
                return passthrough(resp)
            summary = resp.json()
            await add_people(request, summary, data)
            return JSONResponse(summary, headers={"cache-control": "no-store"})
        except httpx.HTTPError as exc:
            log.warning("upstream error: %s", type(exc).__name__)
            return JSONResponse({"error": "upstream_unreachable"}, status_code=502)

    async def add_people(request: Request, summary: dict, data: str | None) -> None:
        """Per-person entries + thumbnail URLs. Uses the summary's own `people` when the
        deployed Edge Function provides them, otherwise derives them from the debug view."""
        snap = summary.get("snapshot")
        if not snap:
            summary["people"] = []
            return
        people = summary.get("people")
        if people is None and settings.admin_token:
            dbg = await upstream(request, "debug", data)
            latest = dbg.json().get("latest_snapshot") if dbg.status_code == 200 else None
            detections = (latest or {}).get("detections") or []
            seats = [s for s in summary.get("seats", []) if s.get("device_id") == snap.get("device_id")]
            people = build_people(detections, seats)
        people = people or []
        key = SnapshotCache.key(snap)
        cache.put(key, snap["url"], {str(p["index"]): p["crop"] for p in people})
        for p in people:
            p["image_url"] = f"/api/people/{key}/{p['index']}.jpg"
        summary["people"] = people

    def add_event_images(feed: dict) -> None:
        # oldest first, so the newest events are the most recently used cache entries
        for e in reversed(feed.get("events", [])):
            snap, crop = e.get("snapshot"), e.get("person_crop")
            if not snap:
                continue
            key = SnapshotCache.key({"device_id": e.get("device_id"), "created_at": snap.get("created_at")})
            name = f"seat-{e.get('seat_id')}"
            cache.put(key, snap["url"], {name: crop} if crop else {})
            if crop:
                e["person_image_url"] = f"/api/people/{key}/{name}.jpg"

    @app.get("/api/people/{key}/{index}.jpg")
    async def person_image(request: Request, key: str, index: str) -> Response:
        item = cache.get(key)
        if not item or index not in item["crops"]:
            return JSONResponse({"error": "unknown_person"}, status_code=404)
        thumbs = item.setdefault("thumbs", {})
        if index not in thumbs:
            if "jpeg" not in item:
                try:
                    r = await request.app.state.http.get(item["url"])
                except httpx.HTTPError:
                    return JSONResponse({"error": "snapshot_unreachable"}, status_code=502)
                if r.status_code != 200 or r.content[:2] != b"\xff\xd8":
                    return JSONResponse({"error": "snapshot_unavailable"}, status_code=502)
                item["jpeg"] = r.content
            thumbs[index] = crop_jpeg(item["jpeg"], item["crops"][index])
        # Content for a given key never changes (key = snapshot identity).
        return Response(thumbs[index], media_type="image/jpeg",
                        headers={"cache-control": "private, max-age=600"})

    dev.register(app, link, settings.feeder_url,
                 frame_gap_s=float(os.environ.get("TEST_FRAME_GAP_S", dev.FRAME_GAP_S)))

    @app.middleware("http")
    async def no_cache_static(request: Request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["cache-control"] = "no-cache"
        return response

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()

