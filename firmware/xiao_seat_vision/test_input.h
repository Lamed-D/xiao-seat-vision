#pragma once

#include <stdint.h>

#include "esp_camera.h"

// Input source selection: LIVE_CAMERA (default) or TEST_VIDEO.
//
// In TEST_VIDEO mode the docker `video-feeder` service decodes an MP4 on the PC
// and POSTs JPEG frames to  POST /api/test-frame?token=...  (port 80).
// Frames go through exactly the same inference::classify() path as camera frames.
//
// Back-pressure: a single pending slot. A newer frame overwrites an unprocessed
// older one (counted as "dropped"), so frames never pile up on the device.
//
//   POST /api/mode?mode=live_camera|test_video&token=...
//   POST /api/test-frame?token=...   body: image/jpeg (<= 64 KB, <= 640x480)
namespace testinput {

enum class InputMode : uint8_t { LiveCamera = 0, TestVideo };

bool begin();  // registers handlers on stream::controlServer()
InputMode mode();
const char* modeName();
// Incremented on every mode switch (main resets the occupancy state on change).
uint32_t modeGeneration();

// Loop task only. If a new test frame is pending, copies it into a loop-owned
// buffer and points *fb at it (valid until the next call). Returns false if none.
bool takeFrame(camera_fb_t* fb);

// Loop task: publish the latest classification so /api/test-frame can return it.
void reportResult(const char* status, const char* state, float confidence, uint32_t inferenceMs);

}  // namespace testinput
