#include "camera_manager.h"

#include <Arduino.h>

// Pin map for the XIAO ESP32S3 Sense camera connector, taken from Seeed Studio's
// official camera_pins.h (CAMERA_MODEL_XIAO_ESP32S3) on the XIAO ESP32S3 Sense wiki.
// Re-check against the wiki if you use a different expansion board revision.
namespace {
constexpr int PWDN_GPIO_NUM = -1;
constexpr int RESET_GPIO_NUM = -1;
constexpr int XCLK_GPIO_NUM = 10;
constexpr int SIOD_GPIO_NUM = 40;
constexpr int SIOC_GPIO_NUM = 39;
constexpr int Y9_GPIO_NUM = 48;
constexpr int Y8_GPIO_NUM = 11;
constexpr int Y7_GPIO_NUM = 12;
constexpr int Y6_GPIO_NUM = 14;
constexpr int Y5_GPIO_NUM = 16;
constexpr int Y4_GPIO_NUM = 18;
constexpr int Y3_GPIO_NUM = 17;
constexpr int Y2_GPIO_NUM = 15;
constexpr int VSYNC_GPIO_NUM = 38;
constexpr int HREF_GPIO_NUM = 47;
constexpr int PCLK_GPIO_NUM = 13;

bool s_ready = false;
uint16_t s_pid = 0;
}  // namespace

namespace camera {

bool begin() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;  // supported by OV2640 and OV3660
  config.jpeg_quality = 12;

  if (psramFound()) {
    // Two buffers + GRAB_LATEST: each capture returns a fresh frame instead of
    // one that was sitting in the queue since the previous inference cycle.
    config.frame_size = FRAMESIZE_QVGA;  // 320x240
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;
    // The driver sizes JPEG buffers from the resolution, not the content. A busy
    // scene at high quality can exceed it ("cam_hal: FB-OVF", frame dropped).
    // 15 (0 = best .. 63 = smallest) leaves headroom at QVGA; plenty for a 96-160 px model.
    config.jpeg_quality = 15;
  } else {
    Serial.println("[camera] WARNING: PSRAM not found - falling back to 1 small DRAM buffer");
    Serial.println("[camera]          Arduino IDE: Tools > PSRAM > OPI PSRAM");
    config.frame_size = FRAMESIZE_QQVGA;  // 160x120
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    config.jpeg_quality = 30;  // tiny DRAM buffer: compress harder to avoid FB-OVF
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[camera] init failed: 0x%x (%s)\n", err, esp_err_to_name(err));
    Serial.println("[camera] check that the camera board is seated on the B2B connector");
    s_ready = false;
    return false;
  }

  sensor_t* sensor = esp_camera_sensor_get();
  s_pid = sensor != nullptr ? sensor->id.PID : 0;
  Serial.printf("[camera] ready: sensor=%s (PID 0x%04x)\n", sensorName(), s_pid);

  // Let auto exposure / white balance settle; discard the first frames.
  for (int i = 0; i < 2; ++i) {
    camera_fb_t* fb = esp_camera_fb_get();
    if (fb != nullptr) esp_camera_fb_return(fb);
  }

  s_ready = true;
  return true;
}

bool isReady() { return s_ready; }

uint16_t sensorPid() { return s_pid; }

const char* sensorName() {
  switch (s_pid) {
    case OV2640_PID:
      return "OV2640";
    case OV3660_PID:
      return "OV3660";
    case OV5640_PID:
      return "OV5640";
    case 0:
      return "none";
    default:
      return "unrecognized";
  }
}

camera_fb_t* capture() {
  if (!s_ready) return nullptr;
  return esp_camera_fb_get();
}

void release(camera_fb_t* fb) {
  if (fb != nullptr) esp_camera_fb_return(fb);
}

}  // namespace camera
