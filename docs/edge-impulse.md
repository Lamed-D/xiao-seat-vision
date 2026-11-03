# Edge Impulse model

## Status
**Not integrated yet.** `model_config.h` leaves `XSV_EI_MODEL_HEADER` undefined, so
`inference::classify()` returns `ModelNotIntegrated`, seats stay `unknown` and no events are sent.

## Dataset
- Capture on the real mount with `firmware/tools/capture_snapshot.py` (QVGA, same sensor/lens/lighting).
- Cover: empty seats, 1..N people, people standing between seats, bags/jackets on seats, day/night.
- Optional: extract frames from MP4s with the video feeder logic (`video_feeder/feeder.py`).
- Label **`person`** bounding boxes (FOMO trains on box centroids).

## Impulse
| Block | Setting |
|---|---|
| Image data | 96×96 (fast) or 160×160 (more seats), **Resize mode: Fit shortest axis** (matches the firmware's centre crop) |
| Processing | Image, Grayscale (smaller/faster) or RGB |
| Learning | **Object detection → FOMO (MobileNetV2 0.35)** |

Image classification (`empty`/`occupied`) is still supported but only tracks **one** seat (whole frame).

## Deploy
1. Deployment → **Arduino library**, **Quantized (int8)**, EON Compiler on → download zip.
2. Arduino IDE: *Sketch → Include Library → Add .ZIP Library…*
3. Open the zip's `src/` folder name, e.g. `xiao-seat-vision_inferencing.h`, and in
   `firmware/xiao_seat_vision/model_config.h` set:
   ```cpp
   #define XSV_EI_MODEL_HEADER "xiao-seat-vision_inferencing.h"
   ```
4. Compile/upload. Boot log shows `type=fomo`, input size, labels and the model's field of view.
5. CI: commit the zip to `firmware/ei-model/` → the `firmware` job installs it and compiles the model build too.

## Compatibility notes (verify on your export)
- Developed against esp32 core **3.3.12** (ESP-IDF 5.5), which bundles `esp-nn`. The model build has **not**
  been compiled yet (no library available). If you hit duplicate-symbol/ESP-NN errors, first try exporting
  with the latest Edge Impulse SDK; report the exact compiler output — do not downgrade the core without
  deciding it explicitly.
- Label name must be `person` (or define `XSV_PERSON_LABEL` before including, e.g. in `config.h`).
- Expected cost (estimate, to be measured): FOMO 96×96 int8 on ESP32-S3 ≈ tens to ~200 ms; tensor arena in
  internal RAM/PSRAM. Telemetry reports `inference_ms`, FPS and heap/PSRAM to confirm.
