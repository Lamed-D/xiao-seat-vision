import base64
import math

import pytest

from conftest import PREFIX
from validation import (
    ValidationError,
    parse_json,
    parse_topic,
    validate_event,
    validate_snapshot,
    validate_status,
)


def test_parse_topic():
    assert parse_topic(f"{PREFIX}/xiao-01/events", PREFIX) == ("xiao-01", "events")
    assert parse_topic(f"{PREFIX}/xiao-01/snapshot", PREFIX) == ("xiao-01", "snapshot")
    for bad in ("seat-guardian/xiao-01/events", f"{PREFIX}/xiao 01/events", f"{PREFIX}/xiao-01/foo", f"{PREFIX}/xiao-01"):
        with pytest.raises(ValidationError):
            parse_topic(bad, PREFIX)


def test_parse_json_errors():
    with pytest.raises(ValidationError):
        parse_json(b"{not json")
    with pytest.raises(ValidationError):
        parse_json(b"[1,2]")
    with pytest.raises(ValidationError):
        parse_json(b"\xff\xfe")
    with pytest.raises(ValidationError):
        parse_json(b"{}" + b" " * 20000)


def test_valid_event_normalized(event):
    ev = validate_event(event, "xiao-01")
    assert ev["seat_id"] == "seat_02"
    assert ev["is_test"] is False and ev["source"] == "camera"
    legacy = dict(event)
    del legacy["seat_id"]
    assert validate_event(legacy, "xiao-01")["seat_id"] == "seat_01"


def test_topic_device_must_match_payload(event):
    with pytest.raises(ValidationError, match="does not match topic"):
        validate_event(event, "xiao-02")


@pytest.mark.parametrize(
    "patch",
    [
        {"state": "maybe"},
        {"confidence": 1.01},
        {"confidence": -0.1},
        {"confidence": math.nan},
        {"confidence": True},
        {"confidence": "0.9"},
        {"inference_ms": -1},
        {"inference_ms": 1.5},
        {"inference_ms": True},
        {"event_id": "xiao-02-abc-1"},
        {"event_id": "xiao-01-bad id"},
        {"seat_id": "seat/1"},
        {"is_test": "yes"},
        {"is_test": False, "source": "test_video"},
        {"timestamp": "not-a-date"},
    ],
)
def test_invalid_events(event, patch):
    with pytest.raises(ValidationError):
        validate_event({**event, **patch}, "xiao-01")


def test_missing_fields(event):
    for field in ("event_id", "device_id", "state", "confidence", "inference_ms"):
        broken = dict(event)
        del broken[field]
        with pytest.raises(ValidationError):
            validate_event(broken, "xiao-01")


def test_unknown_state_and_test_flags(event):
    ev = validate_event({**event, "state": "unknown", "confidence": 0, "is_test": True, "source": "test_video",
                         "timestamp": "2026-11-03T10:00:00Z"}, "xiao-01")
    assert ev["state"] == "unknown" and ev["is_test"] is True and ev["timestamp"].endswith("Z")


def test_status():
    assert validate_status({"device_id": "xiao-01", "status": "offline"}, "xiao-01")["kind"] == "status"
    with pytest.raises(ValidationError):
        validate_status({"device_id": "xiao-01", "status": "zzz"}, "xiao-01")


def test_snapshot_requires_jpeg():
    jpeg = base64.b64encode(b"\xff\xd8\xff\xe0abc\xff\xd9").decode()
    snap = {"snapshot_id": "xiao-01-a-s1", "device_id": "xiao-01", "reason": "periodic",
            "width": 320, "height": 240, "image_b64": jpeg}
    assert validate_snapshot(snap, "xiao-01")["snapshot_id"] == "xiao-01-a-s1"
    with pytest.raises(ValidationError):
        validate_snapshot({**snap, "image_b64": base64.b64encode(b"\x89PNG").decode()}, "xiao-01")
    with pytest.raises(ValidationError):
        validate_snapshot({**snap, "image_b64": "@@@"}, "xiao-01")
