"""Supabase HTTP calls are mocked: no network, no secrets."""

import json
import threading

import requests

from conftest import PREFIX, payload
from forwarder import EdgeFunctionClient, Outcome, PostResult, classify, deliver
from processor import Disposition, MessageProcessor

TOKEN = "t" * 40


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records requests and replays scripted responses / exceptions."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make(script, stop=None, dead_letter=None):
    session = FakeSession(script)
    client = EdgeFunctionClient("https://example.supabase.co/functions/v1/", TOKEN, 5, session)
    proc = MessageProcessor(client, PREFIX, stop or threading.Event(), dead_letter, retry_base_delay=0)
    return proc, session


def test_classify():
    assert classify(201, {"status": "created"}) is Outcome.STORED
    assert classify(200, {"status": "duplicate"}) is Outcome.DUPLICATE
    for s in (400, 413, 415, 422):
        assert classify(s, None) is Outcome.REJECTED
    for s in (401, 403, 404, 408, 429, 500, 502, 503):
        assert classify(s, None) is Outcome.RETRY


def test_event_posted_with_token_and_endpoint(event):
    proc, session = make([FakeResponse(201, {"status": "created"})])
    assert proc.handle(f"{PREFIX}/xiao-01/events", payload(event)) is Disposition.ACK
    call = session.calls[0]
    assert call["url"] == "https://example.supabase.co/functions/v1/log-detection"
    assert call["headers"] == {"x-bridge-token": TOKEN}
    assert call["json"]["seat_id"] == "seat_02"
    assert proc.stats["stored"] == 1


def test_duplicate_event_id_not_reposted(event):
    proc, session = make([FakeResponse(201, {"status": "created"})])
    topic = f"{PREFIX}/xiao-01/events"
    proc.handle(topic, payload(event))
    # QoS 1 redelivery of the same event: acked without a second HTTP call
    assert proc.handle(topic, payload(event)) is Disposition.ACK
    assert len(session.calls) == 1
    assert proc.stats["duplicate"] == 1


def test_server_side_duplicate_counts_as_success(event):
    proc, _ = make([FakeResponse(200, {"status": "duplicate"})])
    assert proc.handle(f"{PREFIX}/xiao-01/events", payload(event)) is Disposition.ACK
    assert proc.stats["duplicate"] == 1


def test_event_retried_until_stored(event):
    script = [requests.ConnectionError("down"), FakeResponse(503), FakeResponse(401, {"error": "unauthorized"}),
              FakeResponse(201, {"status": "created"})]
    proc, session = make(script)
    assert proc.handle(f"{PREFIX}/xiao-01/events", payload(event)) is Disposition.ACK
    assert len(session.calls) == 4
    assert proc.stats["stored"] == 1


def test_rejected_event_goes_to_dead_letter(event, tmp_path):
    dl = tmp_path / "dead.jsonl"
    proc, _ = make([FakeResponse(422, {"error": "validation_failed", "details": ["x"]})], dead_letter=dl)
    assert proc.handle(f"{PREFIX}/xiao-01/events", payload(event)) is Disposition.ACK
    record = json.loads(dl.read_text().splitlines()[0])
    assert record["topic"].endswith("/events") and "422" in record["reason"]


def test_invalid_message_never_posted(event, tmp_path):
    proc, session = make([], dead_letter=tmp_path / "dead.jsonl")
    assert proc.handle(f"{PREFIX}/xiao-02/events", payload(event)) is Disposition.ACK  # topic mismatch
    assert proc.handle(f"{PREFIX}/xiao-01/events", b"{oops") is Disposition.ACK
    assert proc.handle(f"{PREFIX}/xiao-01/events", payload({**event, "confidence": 2})) is Disposition.ACK
    assert session.calls == []
    assert proc.stats["rejected"] == 3


def test_shutdown_during_retry_leaves_message_unacked(event):
    stop = threading.Event()
    stop.set()  # stop.wait() returns True immediately -> deliver() gives up
    proc, _ = make([FakeResponse(500)], stop=stop)
    assert proc.handle(f"{PREFIX}/xiao-01/events", payload(event)) is Disposition.NO_ACK


def test_telemetry_dropped_after_bounded_retries():
    proc, session = make([FakeResponse(503)] * 3)
    tele = {"device_id": "xiao-01", "seats": []}
    assert proc.handle(f"{PREFIX}/xiao-01/telemetry", payload(tele)) is Disposition.ACK
    assert len(session.calls) == 3
    assert session.calls[0]["url"].endswith("/device-status")
    assert session.calls[0]["json"]["kind"] == "telemetry"
    assert proc.stats["dropped"] == 1


def test_deliver_backoff_sequence():
    waits = []
    session = FakeSession([FakeResponse(500), FakeResponse(500), FakeResponse(201, {})])
    client = EdgeFunctionClient("https://x/functions/v1", TOKEN, 5, session)
    result = deliver(client, "events", {}, threading.Event(), base_delay=1, max_delay=60,
                     wait=lambda d: waits.append(d) or False)
    assert isinstance(result, PostResult) and result.outcome is Outcome.STORED
    assert waits == [1, 2]
