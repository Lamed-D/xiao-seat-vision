#pragma once

#include <stdint.h>

#include "detection.h"
#include "esp_camera.h"
#include "seat_state.h"

// Edge Impulse inference wrapper.
//
// Supported model types (decided at compile time from the exported library):
//  - FOMO / object detection (preferred, multi-seat): `detections` holds the person
//    centroids/boxes in full-frame normalized coordinates.
//  - Image classification ("empty"/"occupied"): `state`/`confidence` describe the
//    whole frame (single-seat fallback).
//
// Built without XSV_EI_MODEL_HEADER (see model_config.h) every call returns
// InferenceStatus::ModelNotIntegrated — the firmware never fabricates results.
enum class InferenceStatus : uint8_t {
  Ok = 0,
  ModelNotIntegrated,
  BadFrame,
  OutOfMemory,
  DecodeFailed,
  ResizeFailed,
  ClassifierError,
  UnknownLabel,
};

struct InferenceResult {
  InferenceStatus status = InferenceStatus::ModelNotIntegrated;
  bool objectDetection = false;
  // Classification models only
  SeatState state = SeatState::Unknown;
  float confidence = 0.0f;
  // Object detection (FOMO) models only
  Detection detections[kMaxDetections] = {};
  uint8_t detectionCount = 0;
  // Timing
  uint32_t inferenceMs = 0;   // Edge Impulse DSP + classification time
  uint32_t preprocessMs = 0;  // JPEG decode + crop/resize time
};

struct ModelInfo {
  const char* name;
  int projectId;
  int deployVersion;
  const char* type;  // "fomo" | "classification" | "none"
  int inputWidth;
  int inputHeight;
  // Part of the camera frame the model actually sees (normalized), after centre crop.
  float viewX;
  float viewW;
  float viewY;
  float viewH;
};

namespace inference {

bool begin();
bool modelIntegrated();
const ModelInfo& modelInfo();
InferenceResult classify(const camera_fb_t* fb);
const char* statusToString(InferenceStatus s);

}  // namespace inference
