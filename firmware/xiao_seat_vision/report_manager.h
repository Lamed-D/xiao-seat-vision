#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_camera.h"
#include "inference_manager.h"
#include "seat_mapper.h"
#include "seat_state.h"

// Builds and publishes the periodic telemetry report and JPEG snapshots.
// Both are best-effort (QoS 0) and kept separate from the QoS 1 change events.
//
//   <prefix>/<device_id>/telemetry : seat states + perf + memory + model + ROIs
//   <prefix>/<device_id>/snapshot  : base64 JPEG + detections + seat states
struct SeatReport {
  const SeatRoi* roi;
  SeatState stable;
  float stableConfidence;
  SeatState candidate;
  uint8_t streak;
  SeatObservation last;
};

struct TelemetryInput {
  const char* deviceId;
  const char* timestamp;  // "" if clock not synced
  const char* inputMode;
  bool streaming;
  float inferenceFps;
  uint32_t lastInferenceMs;
  uint32_t lastPreprocessMs;
  const char* lastStatus;
  const InferenceResult* lastResult;  // may be nullptr
  const SeatReport* seats;
  size_t seatCount;
};

namespace report {

bool begin();
bool publishTelemetry(const TelemetryInput& t);
// Copies/encodes the JPEG immediately; the caller can release the frame afterwards.
bool publishSnapshot(const camera_fb_t* fb, const TelemetryInput& t, bool isTest,
                     const char* reason, uint32_t seq, uint32_t bootId);

}  // namespace report
