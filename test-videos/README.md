# test-videos

Put MP4 files for the `video-feeder` (virtual camera) here. Videos are git-ignored
(size, privacy); only this README and `*.labels.csv` are committed.

Labels (only for `TARGET=mqtt`, the ESP32-less pipeline test) — ground truth, not AI:

```csv
start_s,end_s,seat_id,state
0,12.5,seat_01,occupied
8,30,seat_02,occupied
```

Seats without a matching segment are `empty`. See [docs/test-video.md](../docs/test-video.md).
