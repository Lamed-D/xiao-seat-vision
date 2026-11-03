"""FastAPI dashboard server tests. Supabase is replaced by an httpx MockTransport."""

import io
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import Settings, create_app  # noqa: E402
from app.people import build_people, crop_jpeg  # noqa: E402

API = "https://ref.supabase.co/functions/v1/dashboard-api"
VIEWER, ADMIN = "v" * 40, "a" * 40
SNAP_URL = "https://ref.supabase.co/storage/v1/object/sign/snapshots/x.jpg?token=signed"
SEATS = [
    {"device_id": "xiao-01", "seat_id": "seat_01", "label": "Seat 1", "state": "occupied",
     "roi": {"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.6}},
    {"device_id": "xiao-01", "seat_id": "seat_02", "label": "Seat 2", "state": "empty",
     "roi": {"x": 0.4, "y": 0.2, "width": 0.25, "height": 0.6}},
]


def jpeg(w=320, h=240) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (120, 130, 140)).save(buf, "JPEG")
    return buf.getvalue()


class Upstream:
    """Records calls and answers like the dashboard-api Edge Function."""

    def __init__(self, events_status=200):
        self.calls = []
        self.events_status = events_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if str(request.url).startswith(SNAP_URL.split("?")[0]):
            return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
        view = request.url.params.get("view")
        if view == "summary":
            return httpx.Response(200, json={
                "generated_at": "t", "counts": {"total": 2, "occupied": 1, "empty": 1, "unknown": 0},
                "seats": SEATS, "devices": [],
                "snapshot": {"url": SNAP_URL, "device_id": "xiao-01", "width": 320, "height": 240,
                             "created_at": "2026-11-03T10:00:00Z"},
            })
        if view == "debug":
            return httpx.Response(200, json={"latest_snapshot": {"detections": [
                {"cx": 0.8, "cy": 0.5, "confidence": 0.7}, {"cx": 0.2, "cy": 0.5, "confidence": 0.9}]}})
        if view == "events":
            if self.events_status != 200:
                return httpx.Response(self.events_status, json={"error": "unknown_view_or_method"})
            return httpx.Response(200, json={"since": "s", "sit_downs": {"total": 1, "per_seat": {"seat_01": 1}},
                                             "events": [{"event_id": "e1", "device_id": "xiao-01",
                                                         "seat_id": "seat_01", "seat_label": "Seat 1",
                                                         "created_at": "2026-11-03T10:00:01Z",
                                                         "snapshot": {"url": SNAP_URL, "width": 320, "height": 240,
                                                                      "created_at": "2026-11-03T10:00:00Z"},
                                                         "person_crop": {"x": 0.05, "y": 0.2, "width": 0.3,
                                                                         "height": 0.6}}]})
        if view == "layout":
            return httpx.Response(200, json={"status": "stored"})
        return httpx.Response(404, json={"error": "unknown_view_or_method"})


def client(settings=None, upstream=None):
    upstream = upstream or Upstream()
    app = create_app(settings or Settings(API, VIEWER, ADMIN), httpx.MockTransport(upstream))
    return TestClient(app), upstream


def test_static_and_config_without_secrets():
    with client()[0] as c:
        r = c.get("/")
        assert r.status_code == 200 and "viewmodel.js" in r.text
        assert r.headers["cache-control"] == "no-cache"
        cfg = c.get("/config.js").text
        assert '"proxy": true' in cfg and VIEWER not in cfg and ADMIN not in cfg
        assert c.get("/healthz").json() == {"status": "ok", "proxy": True, "admin": True}


def test_tokens_added_server_side_per_view():
    c, up = client()
    with c:
        c.get("/api/dashboard?view=summary")
        c.get("/api/dashboard?view=debug&data=test")
        c.put("/api/dashboard?view=layout", json={"device_id": "xiao-01", "seats": []})
    tokens = {(r.url.params.get("view"), r.headers.get("x-dashboard-token")) for r in up.calls if r.url.params.get("view")}
    assert ("summary", VIEWER) in tokens and ("debug", ADMIN) in tokens and ("layout", ADMIN) in tokens
    assert any(r.url.params.get("data") == "test" for r in up.calls)


def test_summary_gets_people_with_images_and_no_scores():
    with client()[0] as c:
        s = c.get("/api/dashboard?view=summary").json()
        assert [p["seat_id"] for p in s["people"]] == ["seat_01", None]  # left to right
        assert "confidence" not in json.dumps(s["people"])
        img = c.get(s["people"][0]["image_url"])
        assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
        assert Image.open(io.BytesIO(img.content)).height >= 240


def test_events_feed_gets_person_images():
    with client()[0] as c:
        f = c.get("/api/dashboard?view=events").json()
        url = f["events"][0]["person_image_url"]
        assert url.endswith("/seat-seat_01.jpg")
        assert c.get(url).status_code == 200
        assert c.get("/api/people/unknown/1.jpg").status_code == 404


def test_events_not_deployed_and_errors():
    with client(upstream=Upstream(events_status=404))[0] as c:
        assert c.get("/api/dashboard?view=events").status_code == 501
    with client(Settings("", "", ""))[0] as c:
        assert c.get("/api/dashboard?view=summary").status_code == 503

    def boom(_req):
        raise httpx.ConnectError("down")

    app = create_app(Settings(API, VIEWER, ADMIN), httpx.MockTransport(boom))
    with TestClient(app) as c:
        assert c.get("/api/dashboard?view=summary").status_code == 502
        assert c.get("/api/dashboard?view=nope").status_code == 404


def test_people_logic_and_crop():
    people = build_people([{"x": 0.15, "y": 0.45, "width": 0.1, "height": 0.1}], SEATS)
    assert people[0]["seat_id"] == "seat_01"
    c = people[0]["crop"]
    assert 0 <= c["x"] and c["x"] + c["width"] <= 1
    out = Image.open(io.BytesIO(crop_jpeg(jpeg(), {"x": 0.7, "y": 0.4, "width": 0.3, "height": 0.6})))
    assert out.height == 240 and out.width == 160  # 96x144 crop upscaled to 240 high


@pytest.mark.parametrize("view", ["summary", "debug"])
def test_upstream_status_passthrough(view):
    class Fail(Upstream):
        def __call__(self, request):
            return httpx.Response(401, json={"error": "unauthorized"})

    with client(upstream=Fail())[0] as c:
        assert c.get(f"/api/dashboard?view={view}").status_code == 401


def test_all_event_images_stay_available_for_a_full_feed():
    """Regression: a 16-entry cache evicted the newest of 30 event cards (404 thumbnails)."""
    events = [{"event_id": f"e{i}", "device_id": "xiao-01", "seat_id": "seat_01", "seat_label": "Seat 1",
               "created_at": f"2026-11-03T10:{i:02d}:01Z",
               "snapshot": {"url": SNAP_URL, "width": 320, "height": 240, "created_at": f"2026-11-03T10:{i:02d}:00Z"},
               "person_crop": {"x": 0.05, "y": 0.2, "width": 0.3, "height": 0.6}} for i in range(40)]

    class Many(Upstream):
        def __call__(self, request):
            if request.url.params.get("view") == "events":
                return httpx.Response(200, json={"since": "s", "sit_downs": {"total": 40, "per_seat": {}},
                                                 "events": events})
            return super().__call__(request)

    with client(upstream=Many())[0] as c:
        feed = c.get("/api/dashboard?view=events").json()
        urls = [e["person_image_url"] for e in feed["events"]]
        assert len(set(urls)) == 40
        assert all(c.get(u).status_code == 200 for u in urls)


# ---------------------------------------------------------------- 테스트 모드 (developer)
ESP32 = "http://172.16.0.50"
ESP_TOKEN = "esp-web-token-123"
FEEDER = "http://video-feeder:8090"


class Device:
    """Fake ESP32 web API + video feeder."""

    def __init__(self):
        self.calls = []
        self.mode = "live_camera"
        self.streaming = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, request.url.path, dict(request.url.params)))
        if url.startswith(FEEDER):
            return httpx.Response(200, json={"state": "playing" if request.url.path == "/play" else "stopped",
                                             "video": "demo.mp4", "frames_sent": 3, "video_time_s": 1.5})
        if request.url.params.get("token") != ESP_TOKEN:
            return httpx.Response(401, json={"error": "token"})
        if request.url.port == 81:
            return httpx.Response(200, content=b"--xsvframe\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg(),
                                  headers={"content-type": "multipart/x-mixed-replace;boundary=xsvframe"})
        if request.url.path == "/api/status":
            return httpx.Response(200, json={"streaming": self.streaming, "client": False})
        if request.url.path == "/api/stream":
            self.streaming = request.url.params.get("enable") == "1"
            return httpx.Response(200, json={"streaming": self.streaming})
        if request.url.path == "/api/mode":
            self.mode = request.url.params.get("mode")
            return httpx.Response(200, json={"mode": self.mode})
        if request.url.path == "/api/test-frame":
            assert request.content[:2] == b"\xff\xd8"
            return httpx.Response(202, json={"accepted": True, "last_result": {
                "status": "ok", "state": "unknown", "confidence": 0, "inference_ms": 123}})
        return httpx.Response(404, json={})


def dev_client(device, monkeypatch):
    monkeypatch.setenv("TEST_FRAME_GAP_S", "0")
    app = create_app(Settings(API, VIEWER, ADMIN, esp32_url=ESP32, esp32_token=ESP_TOKEN, feeder_url=FEEDER),
                     httpx.MockTransport(device))
    return TestClient(app)


def test_dev_config_flags_and_status(monkeypatch):
    d = Device()
    with dev_client(d, monkeypatch) as c:
        cfg = c.get("/config.js").text
        assert '"deviceControl": true' in cfg and '"testVideo": true' in cfg and ESP_TOKEN not in cfg
        s = c.get("/api/dev/status").json()
        assert s["device"]["streaming"] is False and s["feeder"]["video"] == "demo.mp4"
        assert ESP_TOKEN not in json.dumps(s)


def test_dev_camera_on_off_and_stream_proxy(monkeypatch):
    d = Device()
    with dev_client(d, monkeypatch) as c:
        assert c.post("/api/dev/stream?enable=1").json() == {"streaming": True}
        r = c.get("/api/dev/stream.mjpg")
        assert r.status_code == 200 and r.headers["content-type"].startswith("multipart/x-mixed-replace")
        assert b"\xff\xd8" in r.content
        assert c.post("/api/dev/stream?enable=0").json() == {"streaming": False}


def test_dev_test_video_controls(monkeypatch):
    d = Device()
    with dev_client(d, monkeypatch) as c:
        assert c.post("/api/dev/test-video/play").json()["state"] == "playing"
        assert c.post("/api/dev/test-video/stop").status_code == 200
        assert c.post("/api/dev/test-video/rewind").status_code == 404


def test_dev_test_image_runs_device_pipeline_and_restores_live_mode(monkeypatch):
    d = Device()
    buf = io.BytesIO()
    Image.new("RGB", (1000, 600), (10, 200, 30)).save(buf, "PNG")  # any format/size
    with dev_client(d, monkeypatch) as c:
        r = c.post("/api/dev/test-image", content=buf.getvalue(), headers={"content-type": "image/png"})
        assert r.status_code == 200
        body = r.json()
        assert body["frames_sent"] == 5 and len(body["results"]) == 4
        assert c.post("/api/dev/test-image", content=b"not an image").status_code == 422
    paths = [p for _m, p, _q in d.calls]
    assert paths[0] == "/api/mode" and paths.count("/api/test-frame") == 5
    assert d.mode == "live_camera"  # always switched back


def test_dev_controls_not_configured(monkeypatch):
    with client()[0] as c:  # no ESP32 / feeder settings
        assert c.post("/api/dev/stream?enable=1").status_code == 503
        assert c.post("/api/dev/test-video/play").status_code == 503
        assert '"deviceControl": false' in c.get("/config.js").text
