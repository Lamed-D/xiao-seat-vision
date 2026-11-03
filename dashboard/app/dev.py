"""Developer-mode device controls ("테스트 모드").

All device traffic goes through the MQTT broker (see mqtt_link.py), so it works even when
the PC cannot reach the board directly (observed: AP drops PC->board traffic while the
board's own MQTT connection is stable):

  GET  /api/dev/status                 MQTT link, device online/offline, preview, last result
  POST /api/dev/stream?enable=1|0      camera preview ON/OFF (device publishes JPEGs, 2 fps)
  GET  /api/dev/stream.mjpg            MJPEG built from those frames (renews the preview
                                       while the browser watches, turns it off afterwards)
  POST /api/dev/test-video/{action}    play | pause | stop the video-feeder container
  POST /api/dev/test-image             body: an image. Sent to the device as TEST_VIDEO frames
                                       over MQTT; the device runs the same AI pipeline and the
                                       resulting events/snapshots reach Supabase with
                                       is_test=true (event log, test data).

The UI shows these controls in developer mode only; the dashboard port is published on
127.0.0.1 only.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from PIL import Image, ImageOps

log = logging.getLogger("dashboard.dev")

TEST_IMAGE_FRAMES = 5  # > XSV_STABLE_COUNT (3) so the seat state can actually change
FRAME_GAP_S = 1.2  # device inference interval is 1 s
MAX_UPLOAD = 8 * 1024 * 1024
PREVIEW_SECONDS = 60
PREVIEW_RENEW_S = 20
PREVIEW_MAX_S = 600  # one preview connection lasts at most 10 min
BOUNDARY = "xsvframe"


class Link(Protocol):  # implemented by mqtt_link.MqttLink (and fakes in tests)
    connected: bool

    def send_command(self, payload: dict[str, Any]) -> bool: ...
    def send_frame(self, jpeg: bytes) -> bool: ...
    def latest_frame(self) -> tuple[bytes | None, int, float]: ...
    def results_since(self, t: float) -> list[dict]: ...


def to_test_jpeg(data: bytes) -> bytes:
    """Any image -> 320x240 JPEG (centre crop to 4:3), what the camera would deliver."""
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img = ImageOps.fit(img, (320, 240), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()


def register(app: FastAPI, link: Link | None, feeder_url: str, frame_gap_s: float = FRAME_GAP_S) -> None:
    feeder_url = feeder_url.rstrip("/")

    def link_ready() -> JSONResponse | None:
        if link is None:
            return JSONResponse({"error": "mqtt_not_configured",
                                 "hint": "set MQTT_USERNAME / MQTT_PASSWORD for the dashboard in .env"},
                                status_code=503)
        if not link.connected:
            return JSONResponse({"error": "mqtt_not_connected"}, status_code=503)
        return None

    @app.get("/api/dev/status")
    async def dev_status(request: Request) -> dict:
        out: dict[str, Any] = {"transport": "mqtt", "mqtt_configured": link is not None,
                               "feeder_configured": bool(feeder_url)}
        if link is not None:
            frame, _seq, at = link.latest_frame()
            results = link.results_since(time.time() - 300)
            state = getattr(link, "state", None)
            out.update(mqtt_connected=link.connected,
                       device_status=getattr(state, "device_status", "unknown"),
                       preview_active=frame is not None and time.time() - at < 3,
                       last_result=results[-1] if results else None)
        if feeder_url:
            try:
                r = await request.app.state.http.get(f"{feeder_url}/status", timeout=5)
                out["feeder"] = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                out["feeder"] = {"error": "feeder_unreachable", "detail": type(exc).__name__}
        return out

    @app.post("/api/dev/stream")
    async def dev_stream(enable: int = 0) -> JSONResponse:
        if (err := link_ready()) is not None:
            return err
        ok = link.send_command({"cmd": "stream", "enable": bool(enable), "seconds": PREVIEW_SECONDS})
        return JSONResponse({"streaming": bool(enable) and ok}, status_code=200 if ok else 502)

    @app.get("/api/dev/stream.mjpg")
    async def dev_stream_mjpg(request: Request):
        if (err := link_ready()) is not None:
            return err
        link.send_command({"cmd": "stream", "enable": True, "seconds": PREVIEW_SECONDS})

        async def frames():
            # No request.is_disconnected(): under the http middleware it blocks forever.
            # When the browser goes away Starlette stops the generator -> finally runs.
            last_seq = -1
            renewed = started = time.monotonic()
            try:
                while time.monotonic() - started < PREVIEW_MAX_S:
                    frame, seq, _at = link.latest_frame()
                    if frame is not None and seq != last_seq:
                        last_seq = seq
                        yield (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                               f"Content-Length: {len(frame)}\r\n\r\n").encode() + frame + b"\r\n"
                    if time.monotonic() - renewed > PREVIEW_RENEW_S:
                        renewed = time.monotonic()
                        link.send_command({"cmd": "stream", "enable": True, "seconds": PREVIEW_SECONDS})
                    await asyncio.sleep(0.1)
            finally:
                link.send_command({"cmd": "stream", "enable": False})

        return StreamingResponse(frames(), media_type=f"multipart/x-mixed-replace;boundary={BOUNDARY}",
                                 headers={"cache-control": "no-store"})

    @app.post("/api/dev/test-video/{action}")
    async def dev_test_video(request: Request, action: str) -> JSONResponse:
        if action not in ("play", "pause", "stop"):
            return JSONResponse({"error": "unknown_action"}, status_code=404)
        if not feeder_url:
            return JSONResponse({"error": "feeder_not_configured"}, status_code=503)
        try:
            r = await request.app.state.http.post(f"{feeder_url}/{action}", timeout=20)
            return JSONResponse(r.json(), status_code=r.status_code)
        except (httpx.HTTPError, ValueError) as exc:
            return JSONResponse({"error": "feeder_unreachable", "detail": type(exc).__name__,
                                 "hint": "docker compose --profile test-video up -d video-feeder"},
                                status_code=502)

    @app.post("/api/dev/test-image")
    async def dev_test_image(request: Request) -> JSONResponse:
        if (err := link_ready()) is not None:
            return err
        raw = await request.body()
        if not raw or len(raw) > MAX_UPLOAD:
            return JSONResponse({"error": "image_required", "max_bytes": MAX_UPLOAD}, status_code=400)
        try:
            frame = to_test_jpeg(raw)
        except Exception:  # noqa: BLE001 - any decode problem is a bad upload
            return JSONResponse({"error": "not_an_image"}, status_code=422)

        started = time.time()
        sent = 0
        try:
            link.send_command({"cmd": "mode", "mode": "test_video"})
            await asyncio.sleep(0.3)
            for _ in range(TEST_IMAGE_FRAMES):
                if link.send_frame(frame):
                    sent += 1
                await asyncio.sleep(frame_gap_s)
            await asyncio.sleep(min(frame_gap_s, 1.0))  # time for the last result
        finally:
            link.send_command({"cmd": "mode", "mode": "live_camera"})
        results = link.results_since(started)
        return JSONResponse({"frames_sent": sent, "frame_size": [320, 240], "results": results,
                             "device_answered": bool(results)})
