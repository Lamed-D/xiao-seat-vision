"""Developer-mode device controls ("테스트 모드"), proxied server-side.

  GET  /api/dev/status                 device (/api/status) + test-video feeder status
  POST /api/dev/stream?enable=1|0      camera preview stream ON/OFF on the ESP32
  GET  /api/dev/stream.mjpg            MJPEG preview proxied from the ESP32 (port 81)
  POST /api/dev/test-video/{action}    play | pause | stop the video-feeder (TARGET=esp32)
  POST /api/dev/test-image             body: an image (jpeg/png). Sent to the ESP32 as
                                       TEST_VIDEO frames, so the device runs the same AI
                                       pipeline; resulting events/snapshots are stored in
                                       Supabase with is_test=true (event log, test data).

ESP32_URL / ESP32_WEB_TOKEN / FEEDER_URL come from the environment; the web token is only
used server-side. The UI shows these controls in developer mode only. Access is limited to
this PC because the dashboard port is published on 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from PIL import Image, ImageOps

log = logging.getLogger("dashboard.dev")

TEST_IMAGE_FRAMES = 4  # > XSV_STABLE_COUNT (3) so the seat state can actually change
FRAME_GAP_S = 1.3  # device inference interval is 1 s
MAX_UPLOAD = 8 * 1024 * 1024
DEVICE_TIMEOUT_S = 20  # Wi-Fi latency spikes of several seconds were observed


def to_test_jpeg(data: bytes) -> bytes:
    """Any image -> 320x240 JPEG (centre crop to 4:3), what the camera would deliver."""
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img = ImageOps.fit(img, (320, 240), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()


def register(app: FastAPI, esp32_url: str, esp32_token: str, feeder_url: str,
             frame_gap_s: float = FRAME_GAP_S) -> None:
    esp32_url = esp32_url.rstrip("/")
    feeder_url = feeder_url.rstrip("/")

    def device_ready() -> JSONResponse | None:
        if not (esp32_url and esp32_token):
            return JSONResponse({"error": "device_not_configured",
                                 "hint": "set ESP32_URL and ESP32_WEB_TOKEN in .env"}, status_code=503)
        return None

    async def device(request: Request, method: str, path: str, **kw) -> httpx.Response:
        params = {**kw.pop("params", {}), "token": esp32_token}
        return await request.app.state.http.request(method, f"{esp32_url}{path}", params=params,
                                                    timeout=DEVICE_TIMEOUT_S, **kw)

    async def json_or_error(coro, what: str) -> tuple[Any, int]:
        try:
            r = await coro
        except httpx.HTTPError as exc:
            return {"error": f"{what}_unreachable", "detail": type(exc).__name__}, 502
        try:
            return r.json(), r.status_code
        except ValueError:
            return {"error": f"{what}_bad_response", "status": r.status_code}, 502

    @app.get("/api/dev/status")
    async def dev_status(request: Request) -> dict:
        out: dict[str, Any] = {"device_configured": bool(esp32_url and esp32_token),
                               "feeder_configured": bool(feeder_url)}
        if out["device_configured"]:
            out["device"], code = await json_or_error(device(request, "GET", "/api/status"), "device")
            if code != 200:
                out["device"] = {**out["device"], "http_status": code}
        if feeder_url:
            out["feeder"], code = await json_or_error(
                request.app.state.http.get(f"{feeder_url}/status", timeout=5), "feeder")
            if code != 200:
                out["feeder"] = {**out["feeder"], "http_status": code}
        return out

    @app.post("/api/dev/stream")
    async def dev_stream(request: Request, enable: int = 0) -> JSONResponse:
        if (err := device_ready()) is not None:
            return err
        body, code = await json_or_error(
            device(request, "POST", "/api/stream", params={"enable": "1" if enable else "0"}), "device")
        return JSONResponse(body, status_code=code)

    @app.get("/api/dev/stream.mjpg")
    async def dev_stream_mjpg(request: Request):
        if (err := device_ready()) is not None:
            return err
        host = httpx.URL(esp32_url).host
        url = f"http://{host}:81/stream"
        client: httpx.AsyncClient = request.app.state.http
        req = client.build_request("GET", url, params={"token": esp32_token},
                                   timeout=httpx.Timeout(10, read=None))
        try:
            upstream = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            return JSONResponse({"error": "stream_unreachable", "detail": type(exc).__name__}, status_code=502)
        if upstream.status_code != 200:
            body = await upstream.aread()
            await upstream.aclose()
            return JSONResponse({"error": "stream_refused", "status": upstream.status_code,
                                 "detail": body[:200].decode(errors="replace")}, status_code=409)

        async def relay():
            try:
                async for chunk in upstream.aiter_bytes():  # no content-encoding on MJPEG
                    yield chunk
            finally:
                await upstream.aclose()  # browser closed the image -> close the device stream

        return StreamingResponse(relay(), media_type=upstream.headers.get(
            "content-type", "multipart/x-mixed-replace;boundary=xsvframe"),
            headers={"cache-control": "no-store"})

    @app.post("/api/dev/test-video/{action}")
    async def dev_test_video(request: Request, action: str) -> JSONResponse:
        if action not in ("play", "pause", "stop"):
            return JSONResponse({"error": "unknown_action"}, status_code=404)
        if not feeder_url:
            return JSONResponse({"error": "feeder_not_configured"}, status_code=503)
        body, code = await json_or_error(
            request.app.state.http.post(f"{feeder_url}/{action}", timeout=20), "feeder")
        if code == 502:
            body["hint"] = "start it: docker compose --profile test-video up -d video-feeder"
        return JSONResponse(body, status_code=code)

    @app.post("/api/dev/test-image")
    async def dev_test_image(request: Request) -> JSONResponse:
        if (err := device_ready()) is not None:
            return err
        raw = await request.body()
        if not raw or len(raw) > MAX_UPLOAD:
            return JSONResponse({"error": "image_required", "max_bytes": MAX_UPLOAD}, status_code=400)
        try:
            frame = to_test_jpeg(raw)
        except Exception:  # noqa: BLE001 - any decode problem is a bad upload
            return JSONResponse({"error": "not_an_image"}, status_code=422)

        results = []
        try:
            r = await device(request, "POST", "/api/mode", params={"mode": "test_video"})
            if r.status_code != 200:
                return JSONResponse({"error": "mode_switch_failed", "status": r.status_code}, status_code=502)
            sent = 0
            for i in range(TEST_IMAGE_FRAMES + 1):
                try:
                    r = await device(request, "POST", "/api/test-frame", content=frame,
                                     headers={"content-type": "image/jpeg"})
                except httpx.TimeoutException:
                    results.append({"status": "timeout"})  # one slow frame does not abort the test
                    continue
                if r.status_code != 202:
                    return JSONResponse({"error": "frame_rejected", "status": r.status_code,
                                         "detail": r.text[:200]}, status_code=502)
                sent += 1
                # last_result describes the frame processed before this request
                if i > 0:
                    results.append(r.json().get("last_result"))
                await asyncio.sleep(frame_gap_s)
        except httpx.HTTPError as exc:
            return JSONResponse({"error": "device_unreachable", "detail": type(exc).__name__}, status_code=502)
        finally:
            try:
                await device(request, "POST", "/api/mode", params={"mode": "live_camera"})
            except httpx.HTTPError:
                log.warning("could not switch the device back to live_camera")
        return JSONResponse({"frames_sent": sent, "frame_size": [320, 240],
                             "results": results})
