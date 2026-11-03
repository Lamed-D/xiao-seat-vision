"""Frame extraction, scheduling and annotation logic (no network; unit-tested)."""

from __future__ import annotations

import csv
import io
import threading
from dataclasses import dataclass, field
from typing import Iterator

import cv2

STATES = ("empty", "occupied", "unknown")


@dataclass
class PlayerConfig:
    path: str
    fps: float = 3.0
    width: int = 320
    height: int = 240
    loop: bool = True
    jpeg_quality: int = 80

    def validate(self) -> None:
        if not 0.2 <= self.fps <= 15:
            raise ValueError("fps must be between 0.2 and 15")
        if not (16 <= self.width <= 640 and 16 <= self.height <= 480):
            raise ValueError("resolution must be within 16x16 .. 640x480 (ESP32 limit)")
        if not 30 <= self.jpeg_quality <= 95:
            raise ValueError("jpeg_quality must be 30..95")


@dataclass
class Frame:
    index: int  # output frame number since play started
    video_time_s: float  # position in the source video
    jpeg: bytes
    width: int
    height: int


class VideoSource:
    """Reads a video file and yields JPEG frames sampled at `fps` (video time)."""

    def __init__(self, cfg: PlayerConfig) -> None:
        cfg.validate()
        self.cfg = cfg
        self._cap = cv2.VideoCapture(cfg.path)
        if not self._cap.isOpened():
            raise FileNotFoundError(f"cannot open video {cfg.path!r}")
        src_fps = self._cap.get(cv2.CAP_PROP_FPS)
        self.source_fps = src_fps if src_fps and src_fps > 0 else 30.0
        self.frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    def close(self) -> None:
        self._cap.release()

    def frames(self, stop: threading.Event | None = None) -> Iterator[Frame]:
        """Sample by timestamp so the output rate is independent of the source fps."""
        step_s = 1.0 / self.cfg.fps
        out_index = 0
        next_t = 0.0
        src_index = 0
        loops = 0
        while stop is None or not stop.is_set():
            ok = self._cap.grab()
            if not ok:
                if not self.cfg.loop or src_index == 0:
                    return
                loops += 1
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                src_index = 0
                next_t = 0.0
                continue
            t = src_index / self.source_fps
            src_index += 1
            if t + 1e-9 < next_t:
                continue  # skip: we only need one frame per output tick
            ok, img = self._cap.retrieve()
            if not ok:
                continue
            next_t += step_s
            img = cv2.resize(img, (self.cfg.width, self.cfg.height), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.cfg.jpeg_quality])
            if not ok:
                continue
            yield Frame(out_index, t, buf.tobytes(), self.cfg.width, self.cfg.height)
            out_index += 1


# --------------------------------------------------------------- annotations
@dataclass(frozen=True)
class Segment:
    start_s: float
    end_s: float
    seat_id: str
    state: str


@dataclass
class Annotations:
    """Ground-truth labels for the ESP32-less pipeline test (NOT AI output)."""

    segments: list[Segment] = field(default_factory=list)

    @staticmethod
    def parse_csv(text: str) -> "Annotations":
        """CSV columns: start_s,end_s,seat_id,state  (header row required)."""
        reader = csv.DictReader(io.StringIO(text))
        required = {"start_s", "end_s", "seat_id", "state"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"labels CSV needs columns {sorted(required)}")
        segs = []
        for n, row in enumerate(reader, start=2):
            start, end = float(row["start_s"]), float(row["end_s"])
            state = row["state"].strip()
            if end <= start or state not in STATES:
                raise ValueError(f"labels line {n}: bad range or state")
            segs.append(Segment(start, end, row["seat_id"].strip(), state))
        return Annotations(segs)

    def seats(self) -> list[str]:
        return sorted({s.seat_id for s in self.segments})

    def state_at(self, seat_id: str, t: float) -> str:
        for s in self.segments:
            if s.seat_id == seat_id and s.start_s <= t < s.end_s:
                return s.state
        return "empty"


class ChangeDetector:
    """Emits (seat_id, state) only when a seat's annotated state changes."""

    def __init__(self, annotations: Annotations) -> None:
        self._ann = annotations
        self._last: dict[str, str] = {}

    def reset(self) -> None:
        self._last.clear()

    def changes(self, t: float) -> list[tuple[str, str]]:
        out = []
        for seat in self._ann.seats():
            state = self._ann.state_at(seat, t)
            if self._last.get(seat) != state:
                self._last[seat] = state
                out.append((seat, state))
        return out
