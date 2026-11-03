#include "inference_manager.h"

#include <Arduino.h>
#include <string.h>

#include "app_config.h"  // config.h may define XSV_EI_MODEL_HEADER (local, git-ignored)
#include "model_config.h"

#ifndef XSV_PERSON_LABEL
#define XSV_PERSON_LABEL "person"
#endif

namespace {
// Centre-crop geometry used by Edge Impulse's crop_and_interpolate_rgb888():
// the source is cropped to the destination aspect ratio, then scaled.
__attribute__((unused)) void cropView(int srcW, int srcH, int dstW, int dstH, float* vx, float* vw, float* vy, float* vh) {
  const float srcAspect = static_cast<float>(srcW) / srcH;
  const float dstAspect = static_cast<float>(dstW) / dstH;
  *vx = 0.0f;
  *vy = 0.0f;
  *vw = 1.0f;
  *vh = 1.0f;
  if (srcAspect > dstAspect) {
    *vw = dstAspect / srcAspect;
    *vx = (1.0f - *vw) * 0.5f;
  } else if (srcAspect < dstAspect) {
    *vh = srcAspect / dstAspect;
    *vy = (1.0f - *vh) * 0.5f;
  }
}
}  // namespace

#ifdef XSV_EI_MODEL_HEADER
// ---------------------------------------------------------------------------
// Edge Impulse model integrated (XSV_EI_MODEL_HEADER defined in config.h or model_config.h)
// ---------------------------------------------------------------------------
#include XSV_EI_MODEL_HEADER

#include "edge-impulse-sdk/dsp/image/image.hpp"
#include "esp_heap_caps.h"
#include "img_converters.h"

#if EI_CLASSIFIER_SENSOR != EI_CLASSIFIER_SENSOR_CAMERA
#error "The Edge Impulse library is not an image (camera) model"
#endif

namespace {

ModelInfo s_info = {};

// RGB888 working buffer (in PSRAM) used by the signal callback below.
uint8_t* s_rgb = nullptr;

// Edge Impulse reads pixels as packed 0xRRGGBB floats.
// fmt2rgb888() emits BGR byte order, hence the swap (same as the official
// Edge Impulse esp32_camera example).
int getSignalData(size_t offset, size_t length, float* out) {
  size_t px = offset * 3;
  for (size_t i = 0; i < length; ++i) {
    out[i] = static_cast<float>((s_rgb[px + 2] << 16) | (s_rgb[px + 1] << 8) | s_rgb[px]);
    px += 3;
  }
  return 0;
}

struct PsramBuffer {
  uint8_t* ptr;
  explicit PsramBuffer(size_t bytes)
      : ptr(static_cast<uint8_t*>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT))) {}
  ~PsramBuffer() { heap_caps_free(ptr); }
  PsramBuffer(const PsramBuffer&) = delete;
  PsramBuffer& operator=(const PsramBuffer&) = delete;
};

__attribute__((unused)) SeatState labelToState(const char* label) {  // classification models only
  if (strcmp(label, "empty") == 0) return SeatState::Empty;
  if (strcmp(label, "occupied") == 0) return SeatState::Occupied;
  return SeatState::Unknown;
}

}  // namespace

namespace inference {

bool begin() {
  s_info.name = EI_CLASSIFIER_PROJECT_NAME;
  s_info.projectId = EI_CLASSIFIER_PROJECT_ID;
  s_info.deployVersion = EI_CLASSIFIER_PROJECT_DEPLOY_VERSION;
#if EI_CLASSIFIER_OBJECT_DETECTION == 1
  s_info.type = "fomo";
#else
  s_info.type = "classification";
#endif
  s_info.inputWidth = EI_CLASSIFIER_INPUT_WIDTH;
  s_info.inputHeight = EI_CLASSIFIER_INPUT_HEIGHT;
  cropView(320, 240, EI_CLASSIFIER_INPUT_WIDTH, EI_CLASSIFIER_INPUT_HEIGHT, &s_info.viewX,
           &s_info.viewW, &s_info.viewY, &s_info.viewH);

  Serial.printf("[infer] Edge Impulse model: project=\"%s\" id=%d deploy_v%d type=%s\n",
                s_info.name, s_info.projectId, s_info.deployVersion, s_info.type);
  Serial.printf("[infer] input %dx%d, %d labels:", EI_CLASSIFIER_INPUT_WIDTH,
                EI_CLASSIFIER_INPUT_HEIGHT, EI_CLASSIFIER_LABEL_COUNT);
  for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; ++i) {
    Serial.printf(" %s", ei_classifier_inferencing_categories[i]);
  }
  Serial.println();
  Serial.printf("[infer] model sees x=[%.3f..%.3f] y=[%.3f..%.3f] of a QVGA frame\n",
                static_cast<double>(s_info.viewX), static_cast<double>(s_info.viewX + s_info.viewW),
                static_cast<double>(s_info.viewY), static_cast<double>(s_info.viewY + s_info.viewH));
  return true;
}

bool modelIntegrated() { return true; }

const ModelInfo& modelInfo() { return s_info; }

InferenceResult classify(const camera_fb_t* fb) {
  InferenceResult r;
  if (fb == nullptr || fb->buf == nullptr || fb->len == 0 || fb->width == 0 || fb->height == 0) {
    r.status = InferenceStatus::BadFrame;
    return r;
  }

  const uint32_t t0 = millis();
  // Buffer large enough for the decoded frame; the resize below works in place.
  PsramBuffer rgb(static_cast<size_t>(fb->width) * fb->height * 3);
  if (rgb.ptr == nullptr) {
    r.status = InferenceStatus::OutOfMemory;
    return r;
  }
  if (!fmt2rgb888(fb->buf, fb->len, fb->format, rgb.ptr)) {
    r.status = InferenceStatus::DecodeFailed;
    return r;
  }
  if (fb->width != EI_CLASSIFIER_INPUT_WIDTH || fb->height != EI_CLASSIFIER_INPUT_HEIGHT) {
    int rc = ei::image::processing::crop_and_interpolate_rgb888(
        rgb.ptr, fb->width, fb->height, rgb.ptr, EI_CLASSIFIER_INPUT_WIDTH,
        EI_CLASSIFIER_INPUT_HEIGHT);
    if (rc != 0) {
      r.status = InferenceStatus::ResizeFailed;
      return r;
    }
  }
  r.preprocessMs = millis() - t0;

  signal_t signal;
  signal.total_length = EI_CLASSIFIER_INPUT_WIDTH * EI_CLASSIFIER_INPUT_HEIGHT;
  signal.get_data = &getSignalData;
  s_rgb = rgb.ptr;

  ei_impulse_result_t result = {};
  EI_IMPULSE_ERROR err = run_classifier(&signal, &result, false);
  s_rgb = nullptr;
  if (err != EI_IMPULSE_OK) {
    Serial.printf("[infer] run_classifier error %d\n", static_cast<int>(err));
    r.status = InferenceStatus::ClassifierError;
    return r;
  }
  r.inferenceMs = static_cast<uint32_t>(result.timing.dsp + result.timing.classification +
                                        result.timing.anomaly);

#if EI_CLASSIFIER_OBJECT_DETECTION == 1
  r.objectDetection = true;
  float vx, vw, vy, vh;
  cropView(fb->width, fb->height, EI_CLASSIFIER_INPUT_WIDTH, EI_CLASSIFIER_INPUT_HEIGHT, &vx, &vw,
           &vy, &vh);
  for (uint32_t i = 0; i < result.bounding_boxes_count && r.detectionCount < kMaxDetections; ++i) {
    const ei_impulse_result_bounding_box_t& bb = result.bounding_boxes[i];
    if (bb.value <= 0.0f || bb.label == nullptr || strcmp(bb.label, XSV_PERSON_LABEL) != 0) continue;
    Detection& d = r.detections[r.detectionCount++];
    d.x = vx + (static_cast<float>(bb.x) / EI_CLASSIFIER_INPUT_WIDTH) * vw;
    d.y = vy + (static_cast<float>(bb.y) / EI_CLASSIFIER_INPUT_HEIGHT) * vh;
    d.w = (static_cast<float>(bb.width) / EI_CLASSIFIER_INPUT_WIDTH) * vw;
    d.h = (static_cast<float>(bb.height) / EI_CLASSIFIER_INPUT_HEIGHT) * vh;
    d.confidence = bb.value;
  }
  r.status = InferenceStatus::Ok;
#else
  size_t best = 0;
  for (size_t i = 1; i < EI_CLASSIFIER_LABEL_COUNT; ++i) {
    if (result.classification[i].value > result.classification[best].value) best = i;
  }
  r.state = labelToState(result.classification[best].label);
  r.confidence = result.classification[best].value;
  r.status = r.state == SeatState::Unknown ? InferenceStatus::UnknownLabel : InferenceStatus::Ok;
#endif
  return r;  // PsramBuffer destructor frees the RGB buffer
}

}  // namespace inference

#else
// ---------------------------------------------------------------------------
// No model integrated (default). Explicitly NOT implemented: no fake output.
// ---------------------------------------------------------------------------
namespace {
ModelInfo s_info = {"none", 0, 0, "none", 0, 0, 0.0f, 1.0f, 0.0f, 1.0f};
}

namespace inference {

bool begin() {
  Serial.println("[infer] Edge Impulse model NOT integrated (XSV_EI_MODEL_HEADER not set in config.h/model_config.h)");
  Serial.println("[infer] -> no classification is performed and no seat events are published");
  return false;
}

bool modelIntegrated() { return false; }

const ModelInfo& modelInfo() { return s_info; }

InferenceResult classify(const camera_fb_t*) {
  InferenceResult r;
  r.status = InferenceStatus::ModelNotIntegrated;
  return r;
}

}  // namespace inference
#endif

namespace inference {

const char* statusToString(InferenceStatus s) {
  switch (s) {
    case InferenceStatus::Ok:
      return "ok";
    case InferenceStatus::ModelNotIntegrated:
      return "model_not_integrated";
    case InferenceStatus::BadFrame:
      return "bad_frame";
    case InferenceStatus::OutOfMemory:
      return "out_of_memory";
    case InferenceStatus::DecodeFailed:
      return "decode_failed";
    case InferenceStatus::ResizeFailed:
      return "resize_failed";
    case InferenceStatus::ClassifierError:
      return "classifier_error";
    case InferenceStatus::UnknownLabel:
      return "unknown_label";
  }
  return "?";
}

}  // namespace inference
