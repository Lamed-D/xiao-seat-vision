"""xiao-seat-vision MQTT -> Supabase bridge.

Subscribes to <prefix>/+/events|status|telemetry|snapshot (QoS 1), validates each
message and forwards it over HTTPS to the Supabase Edge Functions.

Delivery guarantees (see docs/architecture.md):
  * persistent MQTT session (clean_session=False, fixed client id) + manual PUBACK:
    a message is acknowledged only after the Edge Function stored it (or rejected
    it permanently). If the bridge stops, the broker keeps/redelivers it.
  * event_id / snapshot_id make redeliveries idempotent (DB UNIQUE constraints).
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import signal
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from forwarder import EdgeFunctionClient
from processor import Disposition, MessageProcessor

log = logging.getLogger("bridge")
HEALTH_FILE = Path(os.environ.get("BRIDGE_HEALTH_FILE", "/tmp/xsv-bridge-health"))
HEALTH_MAX_AGE_S = 60


@dataclass(frozen=True)
class Config:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_tls: bool
    mqtt_ca_cert: str | None
    mqtt_client_id: str
    topic_prefix: str
    functions_url: str
    bridge_token: str
    http_timeout_s: float
    dead_letter_path: Path

    @staticmethod
    def from_env() -> "Config":
        def need(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise SystemExit(f"missing required environment variable {name}")
            return value

        functions_url = need("SUPABASE_FUNCTIONS_URL")
        parsed = urlparse(functions_url)
        local = parsed.hostname in ("localhost", "127.0.0.1", "host.docker.internal")
        # Integration tests only (mock functions container on the Docker network).
        test_http = os.environ.get("BRIDGE_TEST_ALLOW_HTTP") == "1"
        if parsed.scheme != "https" and not (parsed.scheme == "http" and (local or test_http)):
            raise SystemExit("SUPABASE_FUNCTIONS_URL must be https:// (http only for a local supabase)")
        if parsed.scheme == "http":
            logging.getLogger("bridge").warning("plain HTTP functions URL - test/local use only")

        token = need("BRIDGE_API_TOKEN")
        if len(token) < 32:
            raise SystemExit("BRIDGE_API_TOKEN must be at least 32 characters")

        return Config(
            mqtt_host=need("MQTT_HOST"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "8883")),
            mqtt_username=need("MQTT_USERNAME"),
            mqtt_password=need("MQTT_PASSWORD"),
            mqtt_tls=os.environ.get("MQTT_TLS", "true").lower() in ("1", "true", "yes"),
            mqtt_ca_cert=os.environ.get("MQTT_CA_CERT") or None,
            mqtt_client_id=os.environ.get("MQTT_CLIENT_ID", "xiao-seat-vision-bridge"),
            topic_prefix=os.environ.get("MQTT_TOPIC_PREFIX", "xiao-seat-vision"),
            functions_url=functions_url,
            bridge_token=token,
            http_timeout_s=float(os.environ.get("HTTP_TIMEOUT_S", "10")),
            dead_letter_path=Path(os.environ.get("DEAD_LETTER_PATH", "data/dead_letter.jsonl")),
        )


class Bridge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.stop = threading.Event()
        self.queue: queue.Queue[tuple[int, mqtt.MQTTMessage]] = queue.Queue()
        self.connected = threading.Event()
        self._generation = 0  # bumped on every (re)connect; stale mids are never acked
        self._lock = threading.Lock()

        self.processor = MessageProcessor(
            EdgeFunctionClient(cfg.functions_url, cfg.bridge_token, cfg.http_timeout_s),
            cfg.topic_prefix,
            self.stop,
            cfg.dead_letter_path,
        )

        self.client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=cfg.mqtt_client_id,
            clean_session=False,  # persistent session: broker queues QoS 1 while we're away
            protocol=mqtt.MQTTv311,
            manual_ack=True,  # PUBACK only after the Edge Function stored the message
        )
        self.client.username_pw_set(cfg.mqtt_username, cfg.mqtt_password)
        if cfg.mqtt_tls:
            # Certificate + hostname verification stay enabled (never tls_insecure_set).
            self.client.tls_set(
                ca_certs=cfg.mqtt_ca_cert,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS_CLIENT,
            )
        else:
            log.warning("MQTT TLS disabled - only acceptable on the internal Docker network")
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # ---- MQTT callbacks (network thread) ---------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("MQTT connect refused: %s (check MQTT_USERNAME/PASSWORD and ACL)", reason_code)
            return
        with self._lock:
            self._generation += 1
        self.connected.set()
        log.info(
            "MQTT connected to %s:%s (session_present=%s)",
            self.cfg.mqtt_host,
            self.cfg.mqtt_port,
            flags.session_present,
        )
        p = self.cfg.topic_prefix
        client.subscribe([(f"{p}/+/{kind}", 1) for kind in ("events", "status", "telemetry", "snapshot")])

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected.clear()
        if not self.stop.is_set():
            log.warning("MQTT disconnected (%s) - paho reconnects automatically", reason_code)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        # Never do HTTP here: it would block keepalives. Hand off to the worker.
        with self._lock:
            gen = self._generation
        self.queue.put((gen, msg))

    # ---- worker ------------------------------------------------------------
    def worker(self) -> None:
        while not self.stop.is_set():
            try:
                gen, msg = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue
            with self._lock:
                stale = gen != self._generation
            if stale:
                # Delivered on an older connection: the broker redelivers it on the
                # new one, and its message id may already be reused. Skip it.
                continue
            disposition = self.processor.handle(msg.topic, msg.payload)
            if disposition is Disposition.ACK and msg.qos > 0:
                with self._lock:
                    still_same = gen == self._generation
                if still_same:
                    self.client.ack(msg.mid, msg.qos)

    def health_loop(self) -> None:
        while not self.stop.wait(10):
            try:
                HEALTH_FILE.write_text(f"{int(time.time())} {int(self.connected.is_set())}")
            except OSError:
                pass

    def run(self) -> None:
        threads = [
            threading.Thread(target=self.worker, name="worker", daemon=True),
            threading.Thread(target=self.health_loop, name="health", daemon=True),
        ]
        for t in threads:
            t.start()
        log.info(
            "bridge starting: mqtt=%s:%s tls=%s prefix=%s functions=%s",
            self.cfg.mqtt_host,
            self.cfg.mqtt_port,
            self.cfg.mqtt_tls,
            self.cfg.topic_prefix,
            self.cfg.functions_url,  # URL only; tokens/passwords are never logged
        )
        self.client.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=30)
        self.client.loop_start()
        self.stop.wait()

        log.info("shutting down: finishing current message, unacked ones stay on the broker")
        for t in threads:
            t.join(timeout=15)
        self.client.disconnect()
        self.client.loop_stop()
        log.info("bridge stopped (stats: %s)", self.processor.stats)


def healthcheck() -> int:
    """Docker HEALTHCHECK: heartbeat file fresh and MQTT connected."""
    try:
        ts, connected = HEALTH_FILE.read_text().split()
    except (OSError, ValueError):
        return 1
    return 0 if connected == "1" and time.time() - int(ts) < HEALTH_MAX_AGE_S else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--healthcheck", action="store_true", help="exit 0 if healthy")
    args = parser.parse_args()
    if args.healthcheck:
        sys.exit(healthcheck())

    try:
        from dotenv import load_dotenv  # optional for local runs

        load_dotenv()
    except ImportError:
        pass

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bridge = Bridge(Config.from_env())

    def _stop(signum, _frame):
        log.info("signal %s received", signum)
        bridge.stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    bridge.run()


if __name__ == "__main__":
    main()
