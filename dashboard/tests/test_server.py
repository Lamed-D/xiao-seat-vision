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


# ---------------------------------------------------------------- 테스트 모드 (developer, MQTT)
FEEDER = "http://video-feeder:8090"


class FakeLink:
    """Stands in for the MQTT link: records commands/frames, answers like the device."""

    def __init__(self, connected=True):
        self.connected = connected
        self.commands, self.frames, self._results = [], [], []
        self._frame, self._seq = None, 0

    def send_command(self, payload):
        self.commands.append(payload)
        if payload.get("cmd") == "stream" and payload.get("enable"):
            self._frame, self._seq = jpeg(), self._seq + 1  # device starts publishing
        return True

    def send_frame(self, data):
        self.frames.append(data)
        import time as _t
        self._results.append({"status": "ok", "persons": 1, "inference_ms": 122, "received_at": _t.time()})
        return True

    def latest_frame(self):
        return self._frame, self._seq, __import__("time").time()

    def results_since(self, t):
        return [r for r in self._results if r["received_at"] >= t]


def feeder_transport(request: httpx.Request) -> httpx.Response:
    if str(request.url).startswith(FEEDER):
        return httpx.Response(200, json={"state": "playing" if request.url.path == "/play" else "stopped",
                                         "video": "demo.mp4", "frames_sent": 3, "video_time_s": 1.5})
    return Upstream()(request)


def dev_client(link, monkeypatch):
    monkeypatch.setenv("TEST_FRAME_GAP_S", "0")
    app = create_app(Settings(API, VIEWER, ADMIN, feeder_url=FEEDER), httpx.MockTransport(feeder_transport),
                     link=link)
    return TestClient(app)


def test_dev_config_and_status(monkeypatch):
    with dev_client(FakeLink(), monkeypatch) as c:
        cfg = c.get("/config.js").text
        assert '"deviceControl": true' in cfg and '"testVideo": true' in cfg
        s = c.get("/api/dev/status").json()
        assert s["transport"] == "mqtt" and s["mqtt_connected"] is True
        assert s["feeder"]["video"] == "demo.mp4"


def test_dev_camera_preview_over_mqtt(monkeypatch):
    link = FakeLink()
    with dev_client(link, monkeypatch) as c:
        assert c.post("/api/dev/stream?enable=1").json() == {"streaming": True}
        assert c.post("/api/dev/stream?enable=0").json() == {"streaming": False}
    assert link.commands[0] == {"cmd": "stream", "enable": True, "seconds": 60}
    assert link.commands[1]["enable"] is False


def test_dev_test_image_over_mqtt_restores_live_mode(monkeypatch):
    link = FakeLink()
    buf = io.BytesIO()
    Image.new("RGB", (1000, 600), (10, 200, 30)).save(buf, "PNG")  # any format/size
    with dev_client(link, monkeypatch) as c:
        r = c.post("/api/dev/test-image", content=buf.getvalue(), headers={"content-type": "image/png"})
        assert r.status_code == 200
        body = r.json()
        assert body["frames_sent"] == 5 and body["device_answered"] is True and len(body["results"]) == 5
        assert c.post("/api/dev/test-image", content=b"not an image").status_code == 422
    assert link.commands[0] == {"cmd": "mode", "mode": "test_video"}
    assert link.commands[-1] == {"cmd": "mode", "mode": "live_camera"}  # always switched back
    frame = Image.open(io.BytesIO(link.frames[0]))
    assert frame.size == (320, 240) and link.frames[0][:2] == bytes([0xFF, 0xD8])


def test_dev_test_video_controls(monkeypatch):
    with dev_client(FakeLink(), monkeypatch) as c:
        assert c.post("/api/dev/test-video/play").json()["state"] == "playing"
        assert c.post("/api/dev/test-video/rewind").status_code == 404


def test_dev_controls_unavailable(monkeypatch):
    with dev_client(FakeLink(connected=False), monkeypatch) as c:
        assert c.post("/api/dev/stream?enable=1").status_code == 503
    with client()[0] as c:  # no MQTT configured at all
        assert c.post("/api/dev/test-image", content=b"x").status_code == 503
        assert '"deviceControl": false' in c.get("/config.js").text


def test_dev_preview_mjpeg_delivers_frames(monkeypatch):
    """Regression: request.is_disconnected() under the http middleware blocked the stream."""
    from app import dev as devmod
    monkeypatch.setattr(devmod, "PREVIEW_MAX_S", 0.5)  # let the endless stream end for the test
    link = FakeLink()
    with dev_client(link, monkeypatch) as c:
        r = c.get("/api/dev/stream.mjpg")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        assert b"Content-Type: image/jpeg" in r.content and bytes([0xFF, 0xD8]) in r.content
    assert link.commands[0]["enable"] is True
    assert link.commands[-1] == {"cmd": "stream", "enable": False}  # turned off afterwards
