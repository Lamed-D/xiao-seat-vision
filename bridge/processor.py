"""Turns one MQTT message into (at most) one Edge Function call and decides on the ack."""

from __future__ import annotations

import enum
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from forwarder import EdgeFunctionClient, Outcome, deliver
from validation import (
    MAX_SNAPSHOT_BYTES,
    ValidationError,
    parse_json,
    parse_topic,
    validate_event,
    validate_snapshot,
    validate_status,
    validate_telemetry,
)

log = logging.getLogger("bridge.processor")

# Retry budget per kind. None = until stored (events/status are the source of truth).
MAX_ATTEMPTS = {"events": None, "status": None, "telemetry": 3, "snapshot": 5}


class Disposition(enum.Enum):
    ACK = "ack"  # done (stored, duplicate, rejected or intentionally dropped)
    NO_ACK = "no_ack"  # shutting down: leave it on the broker for redelivery


class RecentIds:
    """Small LRU of ids already stored, so QoS 1 redeliveries skip the HTTP call.
    The database UNIQUE constraint remains the real idempotency guarantee."""

    def __init__(self, capacity: int = 2048) -> None:
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._capacity = capacity

    def __contains__(self, key: str) -> bool:
        if key in self._ids:
            self._ids.move_to_end(key)
            return True
        return False

    def add(self, key: str) -> None:
        self._ids[key] = None
        self._ids.move_to_end(key)
        while len(self._ids) > self._capacity:
            self._ids.popitem(last=False)


class MessageProcessor:
    def __init__(
        self,
        client: EdgeFunctionClient,
        topic_prefix: str,
        stop: threading.Event,
        dead_letter_path: Path | None = None,
        retry_base_delay: float = 1.0,
    ) -> None:
        self._client = client
        self._prefix = topic_prefix
        self._stop = stop
        self._dead_letter = dead_letter_path
        self._base_delay = retry_base_delay
        self.recent = RecentIds()
        self.stats = {"stored": 0, "duplicate": 0, "rejected": 0, "dropped": 0}

    def handle(self, topic: str, payload: bytes) -> Disposition:
        try:
            device_id, kind = parse_topic(topic, self._prefix)
            max_bytes = MAX_SNAPSHOT_BYTES if kind == "snapshot" else None
            obj = parse_json(payload, max_bytes) if max_bytes else parse_json(payload)
            body, dedup_key = self._validate(kind, obj, device_id)
        except ValidationError as exc:
            self._reject(topic, payload, str(exc))
            return Disposition.ACK

        if dedup_key and dedup_key in self.recent:
            log.info("%s %s already stored - skipping redelivery", kind, dedup_key)
            self.stats["duplicate"] += 1
            return Disposition.ACK

        result = deliver(
            self._client,
            kind,
            body,
            self._stop,
            max_attempts=MAX_ATTEMPTS[kind],
            base_delay=self._base_delay,
        )
        if result is None:
            return Disposition.NO_ACK

        if result.outcome in (Outcome.STORED, Outcome.DUPLICATE):
            key = "stored" if result.outcome is Outcome.STORED else "duplicate"
            self.stats[key] += 1
            if dedup_key:
                self.recent.add(dedup_key)
            log.info("%s from %s %s (%s)", kind, device_id, key, dedup_key or "-")
        elif result.outcome is Outcome.REJECTED:
            self._reject(topic, payload, f"HTTP {result.status}: {result.detail}")
        else:  # retry budget exhausted (telemetry / snapshot only)
            self.stats["dropped"] += 1
            log.warning("%s from %s dropped after retries: %s", kind, device_id, result.detail)
        return Disposition.ACK

    @staticmethod
    def _validate(kind: str, obj: dict[str, Any], device_id: str) -> tuple[dict[str, Any], str]:
        if kind == "events":
            ev = validate_event(obj, device_id)
            return ev, f"event:{ev['event_id']}"
        if kind == "status":
            return validate_status(obj, device_id), ""
        if kind == "telemetry":
            return validate_telemetry(obj, device_id), ""
        snap = validate_snapshot(obj, device_id)
        return snap, f"snapshot:{snap['snapshot_id']}"

    def _reject(self, topic: str, payload: bytes, reason: str) -> None:
        self.stats["rejected"] += 1
        log.error("rejected message on %s: %s", topic, reason)
        if self._dead_letter is None:
            return
        try:
            self._dead_letter.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "topic": topic,
                "reason": reason,
                "payload": payload[:2048].decode("utf-8", errors="replace"),
            }
            with self._dead_letter.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.error("could not write dead-letter file: %s", exc)
