"""Validation of MQTT topics and payloads (mirrors supabase/functions/_shared/validation.ts)."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from datetime import datetime
from typing import Any

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
EVENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
STATES = ("empty", "occupied", "unknown")
SOURCES = ("camera", "test_video")
KINDS = ("events", "status", "telemetry", "snapshot")
MAX_INFERENCE_MS = 600_000
MAX_JSON_BYTES = 16 * 1024
MAX_SNAPSHOT_BYTES = 400 * 1024
MAX_SNAPSHOT_JPEG = 256 * 1024


class ValidationError(ValueError):
    """Permanent problem with a message: it will never succeed on retry."""


def parse_topic(topic: str, prefix: str) -> tuple[str, str]:
    """`<prefix>/<device_id>/<kind>` -> (device_id, kind)."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != prefix:
        raise ValidationError(f"unexpected topic {topic!r}")
    device_id, kind = parts[1], parts[2]
    if not ID_RE.match(device_id):
        raise ValidationError("device_id in topic is invalid")
    if kind not in KINDS:
        raise ValidationError(f"unknown topic kind {kind!r}")
    return device_id, kind


def parse_json(payload: bytes, max_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
    if len(payload) > max_bytes:
        raise ValidationError(f"payload larger than {max_bytes} bytes")
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise ValidationError("payload must be a JSON object")
    return obj


def _is_number(v: Any) -> bool:
    # bool is a subclass of int in Python; reject it explicitly.
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_device(obj: dict[str, Any], topic_device: str) -> str:
    device_id = obj.get("device_id")
    if not isinstance(device_id, str) or not ID_RE.match(device_id):
        raise ValidationError("device_id must match ^[A-Za-z0-9_-]{1,32}$")
    if device_id != topic_device:
        raise ValidationError(
            f"device_id {device_id!r} does not match topic device {topic_device!r}"
        )
    return device_id


def _test_flags(obj: dict[str, Any]) -> tuple[bool, str]:
    is_test = obj.get("is_test", False)
    if not isinstance(is_test, bool):
        raise ValidationError("is_test must be a boolean")
    source = obj.get("source", "test_video" if is_test else "camera")
    if source not in SOURCES:
        raise ValidationError(f"source must be one of {SOURCES}")
    if (source == "test_video") != is_test:
        raise ValidationError("is_test must be true exactly when source is test_video")
    return is_test, source


def _optional_timestamp(obj: dict[str, Any]) -> str | None:
    ts = obj.get("timestamp")
    if ts is None:
        return None
    if not isinstance(ts, str) or len(ts) > 40:
        raise ValidationError("timestamp must be an ISO-8601 string")
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("timestamp must be an ISO-8601 string") from None
    return ts


def validate_event(obj: dict[str, Any], topic_device: str) -> dict[str, Any]:
    device_id = _check_device(obj, topic_device)

    event_id = obj.get("event_id")
    if not isinstance(event_id, str) or not EVENT_ID_RE.match(event_id):
        raise ValidationError("event_id must match ^[A-Za-z0-9_-]{1,128}$")
    if not event_id.startswith(f"{device_id}-"):
        raise ValidationError("event_id must start with '<device_id>-'")

    seat_id = obj.get("seat_id", "seat_01")
    if not isinstance(seat_id, str) or not ID_RE.match(seat_id):
        raise ValidationError("seat_id must match ^[A-Za-z0-9_-]{1,32}$")

    state = obj.get("state")
    if state not in STATES:
        raise ValidationError(f"state must be one of {STATES}")

    confidence = obj.get("confidence")
    if not _is_number(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValidationError("confidence must be a number between 0 and 1")

    inference_ms = obj.get("inference_ms")
    if (
        not isinstance(inference_ms, int)
        or isinstance(inference_ms, bool)
        or not 0 <= inference_ms <= MAX_INFERENCE_MS
    ):
        raise ValidationError(f"inference_ms must be an integer 0..{MAX_INFERENCE_MS}")

    is_test, source = _test_flags(obj)
    event: dict[str, Any] = {
        "event_id": event_id,
        "device_id": device_id,
        "seat_id": seat_id,
        "state": state,
        "confidence": float(confidence),
        "inference_ms": inference_ms,
        "is_test": is_test,
        "source": source,
    }
    ts = _optional_timestamp(obj)
    if ts is not None:
        event["timestamp"] = ts
    return event


def validate_status(obj: dict[str, Any], topic_device: str) -> dict[str, Any]:
    device_id = _check_device(obj, topic_device)
    if obj.get("status") not in ("online", "offline"):
        raise ValidationError("status must be online or offline")
    return {"device_id": device_id, "kind": "status", "payload": obj}


def validate_telemetry(obj: dict[str, Any], topic_device: str) -> dict[str, Any]:
    device_id = _check_device(obj, topic_device)
    _optional_timestamp(obj)
    if len(json.dumps(obj)) > 12_000:
        raise ValidationError("telemetry payload too large")
    return {"device_id": device_id, "kind": "telemetry", "payload": obj}


def validate_snapshot(obj: dict[str, Any], topic_device: str) -> dict[str, Any]:
    device_id = _check_device(obj, topic_device)
    snapshot_id = obj.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not EVENT_ID_RE.match(snapshot_id):
        raise ValidationError("snapshot_id must match ^[A-Za-z0-9_-]{1,128}$")
    if not snapshot_id.startswith(f"{device_id}-"):
        raise ValidationError("snapshot_id must start with '<device_id>-'")
    if obj.get("reason") not in ("state_change", "periodic"):
        raise ValidationError("reason must be state_change or periodic")
    for name in ("width", "height"):
        v = obj.get(name)
        if not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 1920:
            raise ValidationError(f"{name} must be an integer 1..1920")
    image_b64 = obj.get("image_b64")
    if not isinstance(image_b64, str) or not image_b64:
        raise ValidationError("image_b64 missing")
    try:
        jpeg = base64.b64decode(image_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError("image_b64 is not valid base64") from None
    if len(jpeg) > MAX_SNAPSHOT_JPEG or jpeg[:2] != b"\xff\xd8":
        raise ValidationError("image is not a JPEG (or too large)")
    _test_flags(obj)
    _optional_timestamp(obj)
    return obj
