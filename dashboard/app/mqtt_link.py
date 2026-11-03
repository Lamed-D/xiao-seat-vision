"""MQTT link used by the dashboard's developer test mode.

Talks to the device through the broker, i.e. over the connection the device opened itself.
This works on networks where the PC cannot open connections to the board (AP client
isolation / flaky inbound Wi-Fi), which is what the HTTP path suffered from.

  publish  <prefix>/<device>/cmd          {"cmd":"mode"|"stream", ...}   QoS 1
  publish  <prefix>/<device>/test-frame   JPEG                          QoS 0
  subscribe <prefix>/<device>/stream       JPEG preview frames (2 fps while enabled)
  subscribe <prefix>/<device>/test-result  device inference result per test frame
  subscribe <prefix>/<device>/status       online/offline (retained, LWT)
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

log = logging.getLogger("dashboard.mqtt")


@dataclass
class LinkState:
    connected: bool = False
    device_status: str = "unknown"
    frame: bytes | None = None
    frame_at: float = 0.0
    frame_seq: int = 0
    results: deque = field(default_factory=lambda: deque(maxlen=50))


class MqttLink:
    def __init__(self, host: str, port: int, username: str, password: str, ca_cert: str | None,
                 prefix: str, device_id: str, tls: bool = True) -> None:
        self.prefix = prefix
        self.device_id = device_id
        self.state = LinkState()
        self._lock = threading.Lock()
        self._client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2,
                                   client_id=f"xiao-seat-vision-dashboard-{int(time.time())}")
        self._client.username_pw_set(username, password)
        if tls:  # verification stays on
            self._client.tls_set(ca_certs=ca_cert or None, cert_reqs=ssl.CERT_REQUIRED,
                                 tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self._client.reconnect_delay_set(1, 30)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._host, self._port = host, port

    def topic(self, kind: str) -> str:
        return f"{self.prefix}/{self.device_id}/{kind}"

    # ---- lifecycle
    def start(self) -> None:
        self._client.connect_async(self._host, self._port, keepalive=30)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()

    # ---- callbacks (network thread)
    def _on_connect(self, client, _u, _flags, reason_code, _props) -> None:
        if reason_code.is_failure:
            log.error("MQTT connect refused: %s", reason_code)
            return
        self.state.connected = True
        client.subscribe([(self.topic("stream"), 0), (self.topic("test-result"), 0),
                          (self.topic("status"), 1)])
        log.info("MQTT connected (device %s)", self.device_id)

    def _on_disconnect(self, *_args) -> None:
        self.state.connected = False

    def _on_message(self, _client, _u, msg: mqtt.MQTTMessage) -> None:
        kind = msg.topic.rsplit("/", 1)[-1]
        with self._lock:
            if kind == "stream" and msg.payload[:2] == b"\xff\xd8":
                self.state.frame = bytes(msg.payload)
                self.state.frame_at = time.time()
                self.state.frame_seq += 1
            elif kind == "test-result":
                try:
                    r = json.loads(msg.payload)
                    r["received_at"] = time.time()
                    self.state.results.append(r)
                except ValueError:
                    pass
            elif kind == "status":
                try:
                    self.state.device_status = json.loads(msg.payload).get("status", "unknown")
                except ValueError:
                    pass

    # ---- API used by the routes
    @property
    def connected(self) -> bool:
        return self.state.connected

    def send_command(self, payload: dict[str, Any]) -> bool:
        # compact JSON: the firmware matches tokens like "cmd":"stream"
        info = self._client.publish(self.topic("cmd"), json.dumps(payload, separators=(",", ":")), qos=1)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def send_frame(self, jpeg: bytes) -> bool:
        info = self._client.publish(self.topic("test-frame"), jpeg, qos=0)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

    def latest_frame(self) -> tuple[bytes | None, int, float]:
        with self._lock:
            return self.state.frame, self.state.frame_seq, self.state.frame_at

    def results_since(self, t: float) -> list[dict]:
        with self._lock:
            return [r for r in self.state.results if r.get("received_at", 0) >= t]
