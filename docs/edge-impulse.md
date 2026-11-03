# Edge Impulse model

## Status
- Repository default: **no model** (`model_config.h` leaves `XSV_EI_MODEL_HEADER` undefined) → CI builds
  the model-less firmware; seats stay `unknown`, no events.
- Verified 2026-11-03 on the real board with a **mock** model (Penn-Fudan pedestrians): FOMO runs in
  ~122 ms, events/snapshots reach Supabase. Accuracy is not meaningful until it is retrained with photos
  from the real camera position.

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
5. The zip and all camera photos/datasets/videos stay local (`.gitignore`). The CI model build only runs
   if someone deliberately force-adds a zip; by default CI builds the model-less firmware.

## Automated pipeline (`scripts/edge_impulse.py`, needs an **Admin** API key in `.env`)
```bash
python -I scripts/prepare_mock_dataset.py            # or your own photos + bounding_boxes.labels
python scripts/edge_impulse.py upload datasets/mock-person
python scripts/edge_impulse.py train                 # 96x96 fit-short, grayscale, FOMO MobileNetV2 0.35, int8
python scripts/edge_impulse.py deploy                # -> firmware/ei-model/xiao-seat-vision-person.zip
```
`upload` re-applies bounding boxes that the ingestion API occasionally drops (seen on 19/170 images).
An `ingestion_deployment` key can upload/download but cannot train (HTTP 403) — create an **Admin** key.

## Enabling the model locally without breaking CI
Put this in `config.h` (git-ignored) instead of editing `model_config.h`:
```cpp
#define XSV_EI_MODEL_HEADER "xiao-seat-vision_inferencing.h"
```
and install the zip (Arduino IDE: Sketch → Include Library → Add .ZIP Library…).

## ESP-NN tensor arena issue (verified on hardware, esp32 core 3.3.12)
| Export | ESP-NN | Result on XIAO ESP32S3 |
|---|---|---|
| EON compiler | on (default) | `CORRUPT HEAP` in `tflite_learn_*_reset → ei_free`, reboot loop |
| TFLite | on | `AllocateTensors() failed`, `run_classifier error -3` |
| EON | **off** (`EI_CLASSIFIER_TFLITE_ENABLE_ESP_NN 0`) | works, **3.2 s** per inference |
| **TFLite + arena ×2** | on | **works, 121–125 ms**, 0 crashes in 180 inferences ← used |

Cause: the arena size computed by Edge Impulse (185,036 B for this model) does not include the ESP-NN
scratch buffers. `deploy` therefore exports the plain TFLite engine and doubles
`tflite_learn_*_arena_size` inside the zip (370,072 B, allocated in PSRAM). Core and library ESP-NN
objects were checked in the link map: only the library's ESP-NN is linked (no mixing).

- Label name must be `person` (or define `XSV_PERSON_LABEL`, e.g. in `config.h`).
