#pragma once

#include <stdint.h>

#include "esp_camera.h"

// Camera module for the XIAO ESP32S3 Sense camera connector.
// Captures JPEG QVGA frames into PSRAM. The sensor model is detected at runtime
// (OV2640 / OV3660 / ...) rather than assumed.
namespace camera {

bool begin();
bool isReady();

// Detected sensor PID (e.g. 0x26 = OV2640, 0x3660 = OV3660), 0 if unknown.
uint16_t sensorPid();
const char* sensorName();

// Returns a frame buffer that MUST be given back with release() — prefer FrameGuard.
camera_fb_t* capture();
void release(camera_fb_t* fb);

// RAII owner: returns the frame buffer to the driver when it goes out of scope.
class FrameGuard {
 public:
  explicit FrameGuard(camera_fb_t* fb) : fb_(fb) {}
  ~FrameGuard() { reset(); }
  FrameGuard(const FrameGuard&) = delete;
  FrameGuard& operator=(const FrameGuard&) = delete;

  camera_fb_t* get() const { return fb_; }
  explicit operator bool() const { return fb_ != nullptr; }
  void reset() {
    if (fb_ != nullptr) {
      release(fb_);
      fb_ = nullptr;
    }
  }

 private:
  camera_fb_t* fb_;
};

}  // namespace camera
