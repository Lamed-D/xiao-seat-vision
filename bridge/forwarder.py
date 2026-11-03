"""HTTPS client for the Supabase Edge Functions + retry policy."""

from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

import requests

log = logging.getLogger("bridge.forwarder")

ENDPOINTS = {
    "events": "log-detection",
    "status": "device-status",
    "telemetry": "device-status",
    "snapshot": "ingest-snapshot",
}


class Outcome(enum.Enum):
    STORED = "stored"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"  # permanent: the message itself is bad
    RETRY = "retry"  # transient or configuration problem: try again later


@dataclass(frozen=True)
class PostResult:
    outcome: Outcome
    status: int | None
    detail: str = ""


# 4xx codes that mean "this payload will never be accepted".
PERMANENT_STATUSES = {400, 413, 415, 422}
# 401/403/404/405: wrong token / URL / deployment -> not the message's fault.
# Keep it on the broker and retry, so fixing the config loses nothing.
CONFIG_STATUSES = {401, 403, 404, 405}


def classify(status: int, body: dict[str, Any] | None) -> Outcome:
    if status == 201:
        return Outcome.STORED
    if status == 200:
        return Outcome.DUPLICATE if (body or {}).get("status") == "duplicate" else Outcome.STORED
    if status in PERMANENT_STATUSES:
        return Outcome.REJECTED
    return Outcome.RETRY  # 401/403/404/405/408/429/5xx/other


class EdgeFunctionClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_s: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_s
        self._session = session or requests.Session()

    def post(self, kind: str, body: dict[str, Any]) -> PostResult:
        url = f"{self._base}/{ENDPOINTS[kind]}"
        try:
            # TLS verification stays on (requests default, verify=True).
            resp = self._session.post(
                url,
                json=body,
                headers={"x-bridge-token": self._token},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            return PostResult(Outcome.RETRY, None, f"network error: {type(exc).__name__}")
        try:
            data = resp.json()
        except ValueError:
            data = None
        outcome = classify(resp.status_code, data if isinstance(data, dict) else None)
        detail = ""
        if outcome in (Outcome.REJECTED, Outcome.RETRY) and isinstance(data, dict):
            detail = str(data.get("error", ""))[:200]
            if data.get("details"):
                detail += f" {data['details']}"[:300]
        if resp.status_code in CONFIG_STATUSES:
            detail = f"configuration problem (HTTP {resp.status_code}) {detail}".strip()
        return PostResult(outcome, resp.status_code, detail)


def deliver(
    client: EdgeFunctionClient,
    kind: str,
    body: dict[str, Any],
    stop: threading.Event,
    max_attempts: int | None = None,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    wait: Callable[[float], bool] | None = None,
) -> PostResult | None:
    """POST with exponential backoff. Returns None if `stop` was set while retrying.

    max_attempts=None retries until success/rejection (used for events, so nothing
    is lost while Supabase is down; the message stays un-acked on the broker).
    """
    wait = wait or stop.wait
    delay = base_delay
    attempt = 0
    while True:
        attempt += 1
        result = client.post(kind, body)
        if result.outcome is not Outcome.RETRY:
            return result
        if max_attempts is not None and attempt >= max_attempts:
            return result
        log.warning(
            "%s delivery failed (attempt %d, HTTP %s, %s) - retrying in %.0fs",
            kind,
            attempt,
            result.status,
            result.detail or "no detail",
            delay,
        )
        if wait(delay):
            return None  # shutting down
        delay = min(delay * 2, max_delay)
