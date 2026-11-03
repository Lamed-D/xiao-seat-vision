"""xiao-seat-vision test-video feeder (virtual camera).

Plays an MP4 from ./test-videos, samples frames at a fixed FPS and either
  TARGET=esp32 : POSTs JPEG frames to the ESP32 (/api/test-frame). The device runs
                 the SAME Edge Impulse pipeline as for camera frames -> validates
                 on-device AI. Requires the real board.
  TARGET=mqtt  : no ESP32. Publishes test events (is_test=true, source=test_video)
                 derived from a ground-truth labels CSV (not AI!) plus JPEG snapshots,
                 to validate MQTT -> bridge -> Supabase -> dashboard.

Control API (bind to localhost only):
  GET  /status
  POST /play  /pause  /stop
  POST /config?fps=3&loop=1&width=320&height=240&video=demo.mp4
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import paho.mqtt.client as mqtt
import requests
from paho.mqtt.enums import CallbackAPIVersion

from feeder import Annotations, ChangeDetector, PlayerConfig, VideoSource

log = logging.getLogger("feeder")
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", "/videos"))


def env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes")


class Esp32Sink:
    """Synchronous POST: if the ESP32 is slower than the feed, the next frame is
    simply sent later (frames are skipped, never queued)."""

    def __init__(self) -> None:
        self.base = os.environ["ESP32_URL"].rstrip("/")  # e.g. http://192.168.0.42
        self.token = os.environ["ESP32_WEB_TOKEN"]
        self.session = requests.Session()
        self.last_result: dict | None = None

    def start(self) -> None:
        r = self.session.post(f"{self.base}/api/mode", params={"mode": "test_video", "token": self.token}, timeout=5)
        r.raise_for_status()
        log.info("ESP32 switched to %s", r.json().get("mode"))

    def stop(self) -> None:
        try:
            self.session.post(f"{self.base}/api/mode", params={"mode": "live_camera", "token": self.token}, timeout=5)
            log.info("ESP32 switched back to live_camera")
        except requests.RequestException as exc:
            log.warning("could not switch ESP32 back to live_camera: %s", exc)

    def send(self, frame, _t: float) -> None:
        r = self.session.post(
            f"{self.base}/api/test-frame",
            params={"token": self.token},
            data=frame.jpeg,
            headers={"content-type": "image/jpeg"},
            timeout=10,
        )
        if r.status_code == 202:
            self.last_result = r.json()
            log.info("frame %d accepted, device dropped=%s last_result=%s", frame.index,
                     self.last_result.get("dropped"), self.last_result.get("last_result"))
        else:
            log.warning("frame %d rejected: HTTP %s %s", frame.index, r.status_code, r.text[:200])


class MqttSink:
    """ESP32-less pipeline test: publishes annotated test events + snapshots."""

    def __init__(self, annotations: Annotations) -> None:
        self.device = os.environ.get("TEST_DEVICE_ID", "xiao-test-01")
        self.prefix = os.environ.get("MQTT_TOPIC_PREFIX", "xiao-seat-vision")
        self.detector = ChangeDetector(annotations)
        self.boot = secrets.token_hex(4)
        self.seq = 0
        self.snapshot_every_s = float(os.environ.get("TEST_SNAPSHOT_INTERVAL_S", "30"))
        self._last_snap = -1e9
        self.client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=f"{self.device}-feeder")
        self.client.username_pw_set(self.device, os.environ["TEST_DEVICE_PASSWORD"])
        if env_bool("MQTT_TLS", True):
            self.client.tls_set(ca_certs=os.environ.get("MQTT_CA_CERT") or None,
                                cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        self.client.will_set(f"{self.prefix}/{self.device}/status",
                             json.dumps({"device_id": self.device, "status": "offline"}), qos=1, retain=True)
        self.client.connect(os.environ.get("MQTT_HOST", "mosquitto"), int(os.environ.get("MQTT_PORT", "8883")), 30)
        self.client.loop_start()

    def _pub(self, kind: str, obj: dict, qos: int, retain: bool = False) -> None:
        info = self.client.publish(f"{self.prefix}/{self.device}/{kind}", json.dumps(obj), qos=qos, retain=retain)
        if qos > 0:
            info.wait_for_publish(timeout=10)

    def start(self) -> None:
        self.detector.reset()
        self._pub("status", {"device_id": self.device, "status": "online"}, 1, retain=True)

    def stop(self) -> None:
        self._pub("status", {"device_id": self.device, "status": "offline"}, 1, retain=True)

    def send(self, frame, t: float) -> None:
        changes = self.detector.changes(t)
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for seat_id, state in changes:
            self.seq += 1
            event = {
                "event_id": f"{self.device}-{self.boot}-{self.seq:06d}",
                "device_id": self.device,
                "seat_id": seat_id,
                "state": state,
                # Annotation, not a model output: fixed values, clearly flagged as test data.
                "confidence": 1.0 if state != "unknown" else 0.0,
                "inference_ms": 0,
                "is_test": True,
                "source": "test_video",
                "timestamp": ts,
            }
            self._pub("events", event, 1)
            log.info("test event %s %s -> %s (video t=%.1fs)", event["event_id"], seat_id, state, t)
        if changes or t - self._last_snap >= self.snapshot_every_s:
            self._last_snap = t
            self.seq += 1
            self._pub("snapshot", {
                "snapshot_id": f"{self.device}-{self.boot}-s{self.seq:06d}",
                "device_id": self.device,
                "timestamp": ts,
                "reason": "state_change" if changes else "periodic",
                "is_test": True,
                "source": "test_video",
                "width": frame.width,
                "height": frame.height,
                "seats": [{"seat_id": s, "state": self.detector._last.get(s, "empty")}
                          for s in self.detector._ann.seats()],
                "detections": [],
                "image_b64": base64.b64encode(frame.jpeg).decode(),
            }, 0)


class Player:
    def __init__(self) -> None:
        self.cfg = PlayerConfig(
            path=str(VIDEO_DIR / os.environ.get("VIDEO_FILE", "demo.mp4")),
            fps=float(os.environ.get("FEED_FPS", "3")),
            width=int(os.environ.get("FEED_WIDTH", "320")),
            height=int(os.environ.get("FEED_HEIGHT", "240")),
            loop=env_bool("FEED_LOOP", True),
        )
        self.target = os.environ.get("TARGET", "esp32")
        self.state = "stopped"
        self.frames_sent = 0
        self.video_time = 0.0
        self.error = ""
        self._run = threading.Event()  # set = playing
        self._stop = threading.Event()  # set = stop current playback
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._sink = None

    def _make_sink(self):
        if self.target == "esp32":
            return Esp32Sink()
        labels = Path(os.environ.get("LABELS_FILE", str(Path(self.cfg.path).with_suffix(".labels.csv"))))
        return MqttSink(Annotations.parse_csv(labels.read_text(encoding="utf-8")))

    def play(self) -> None:
        with self._lock:
            if self.state == "paused":
                self.state = "playing"
                self._run.set()
                return
            if self.state == "playing":
                return
            self.cfg.validate()
            self._stop.clear()
            self._run.set()
            self.state = "playing"
            self.error = ""
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self.state == "playing":
                self.state = "paused"
                self._run.clear()

    def stop(self) -> None:
        self._stop.set()
        self._run.set()  # wake a paused loop so it can exit
        if self._thread:
            self._thread.join(timeout=15)
        with self._lock:
            self.state = "stopped"

    def _loop(self) -> None:
        source = None
        try:
            self._sink = self._make_sink()
            self._sink.start()
            source = VideoSource(self.cfg)
            period = 1.0 / self.cfg.fps
            next_tick = time.monotonic()
            for frame in source.frames(self._stop):
                self._run.wait()
                if self._stop.is_set():
                    break
                now = time.monotonic()
                if now < next_tick:
                    if self._stop.wait(next_tick - now):
                        break
                # If sending took longer than one period, don't try to catch up:
                # the frames in between are skipped (no backlog builds up).
                next_tick = max(next_tick + period, time.monotonic())
                self._sink.send(frame, frame.video_time_s)
                self.frames_sent += 1
                self.video_time = frame.video_time_s
        except Exception as exc:  # report through /status
            self.error = f"{type(exc).__name__}: {exc}"
            log.error("playback failed: %s", self.error)
        finally:
            if source:
                source.close()
            if self._sink:
                self._sink.stop()
            with self._lock:
                self.state = "stopped"

    def status(self) -> dict:
        return {
            "state": self.state,
            "target": self.target,
            "video": Path(self.cfg.path).name,
            "fps": self.cfg.fps,
            "resolution": [self.cfg.width, self.cfg.height],
            "loop": self.cfg.loop,
            "frames_sent": self.frames_sent,
            "video_time_s": round(self.video_time, 2),
            "error": self.error,
        }

    def configure(self, q: dict[str, list[str]]) -> None:
        if self.state != "stopped":
            raise ValueError("stop playback before changing the configuration")
        if "fps" in q:
            self.cfg.fps = float(q["fps"][0])
        if "loop" in q:
            self.cfg.loop = q["loop"][0] in ("1", "true")
        if "width" in q:
            self.cfg.width = int(q["width"][0])
        if "height" in q:
            self.cfg.height = int(q["height"][0])
        if "video" in q:
            name = Path(q["video"][0]).name  # no path traversal
            self.cfg.path = str(VIDEO_DIR / name)
        self.cfg.validate()


PLAYER = Player()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path in ("/status", "/health"):
            return self._send(200, PLAYER.status())
        self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        url = urlparse(self.path)
        try:
            if url.path == "/play":
                PLAYER.play()
            elif url.path == "/pause":
                PLAYER.pause()
            elif url.path == "/stop":
                PLAYER.stop()
            elif url.path == "/config":
                PLAYER.configure(parse_qs(url.query))
            else:
                return self._send(404, {"error": "not found"})
        except (ValueError, KeyError) as exc:
            return self._send(400, {"error": str(exc)})
        self._send(200, PLAYER.status())

    def log_message(self, fmt, *args):  # quieter access log
        log.debug(fmt, *args)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("CONTROL_PORT", "8090"))
    if env_bool("AUTOPLAY", False):
        PLAYER.play()
    log.info("video feeder control API on :%d (target=%s, video=%s)", port, PLAYER.target, PLAYER.cfg.path)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
