import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feeder import Annotations, ChangeDetector, PlayerConfig, VideoSource  # noqa: E402


@pytest.fixture
def video(tmp_path):
    """2 s synthetic 10 fps video (20 frames)."""
    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    for i in range(20):
        img = np.full((120, 160, 3), i * 10, dtype=np.uint8)
        writer.write(img)
    writer.release()
    return path


def test_samples_at_requested_fps_and_resizes(video):
    src = VideoSource(PlayerConfig(str(video), fps=2, width=96, height=96, loop=False))
    frames = list(src.frames())
    src.close()
    assert len(frames) == 4  # 2 s at 2 fps
    assert [round(f.video_time_s, 1) for f in frames] == [0.0, 0.5, 1.0, 1.5]
    img = cv2.imdecode(np.frombuffer(frames[0].jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape == (96, 96, 3)
    assert frames[0].jpeg[:2] == b"\xff\xd8"


def test_loop_restarts(video):
    src = VideoSource(PlayerConfig(str(video), fps=2, loop=True))
    it = src.frames()
    times = [next(it).video_time_s for _ in range(6)]
    src.close()
    assert times[4] == 0.0  # wrapped around


def test_invalid_config():
    for cfg in (PlayerConfig("x", fps=100), PlayerConfig("x", width=1280, height=720)):
        with pytest.raises(ValueError):
            cfg.validate()
    with pytest.raises(FileNotFoundError):
        VideoSource(PlayerConfig("/nonexistent.mp4"))


def test_annotations_and_change_detection():
    ann = Annotations.parse_csv(
        "start_s,end_s,seat_id,state\n0,5,seat_01,occupied\n3,8,seat_02,occupied\n"
    )
    det = ChangeDetector(ann)
    assert det.changes(0.0) == [("seat_01", "occupied"), ("seat_02", "empty")]
    assert det.changes(1.0) == []
    assert det.changes(3.0) == [("seat_02", "occupied")]
    assert det.changes(5.0) == [("seat_01", "empty")]
    with pytest.raises(ValueError):
        Annotations.parse_csv("start_s,end_s,seat_id,state\n5,1,seat_01,occupied\n")
    with pytest.raises(ValueError):
        Annotations.parse_csv("a,b\n1,2\n")
