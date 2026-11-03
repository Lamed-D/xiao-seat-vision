import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PREFIX = "xiao-seat-vision"


@pytest.fixture
def event():
    return {
        "event_id": "xiao-01-1a2b3c4d-000001",
        "device_id": "xiao-01",
        "seat_id": "seat_02",
        "state": "occupied",
        "confidence": 0.95,
        "inference_ms": 180,
    }


def payload(obj) -> bytes:
    return json.dumps(obj).encode()
