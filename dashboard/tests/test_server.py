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
