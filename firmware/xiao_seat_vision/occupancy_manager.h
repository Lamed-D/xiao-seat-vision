#pragma once

#include <stdint.h>

#include "seat_state.h"

// Debounces per-frame observations of ONE seat into a stable state.
// One instance per seat, so seats never influence each other.
// Pure C++ (no Arduino dependency) so it is unit-tested on the host (firmware/tests).
//
// Rules:
//  - Empty/Occupied observations below `minConfidence` (or NaN / > 1) break the streak.
//  - Unknown observations (ambiguous matches) are a regular candidate state and
//    ignore the confidence threshold.
//  - `requiredConsecutive` identical observations confirm a state.
//  - update() returns true only when the confirmed state changes
//    (including the very first Empty/Occupied confirmation after boot).
//  - forceUnknown() is used for stale data (camera / inference errors); it returns
//    true if the stable state changed.
class OccupancyManager {
 public:
  explicit OccupancyManager(uint8_t requiredConsecutive = 3, float minConfidence = 0.6f);

  bool update(SeatState observed, float confidence);
  bool forceUnknown();
  void reset();

  SeatState stableState() const { return stable_; }
  SeatState candidateState() const { return candidate_; }
  uint8_t streak() const { return streak_; }
  // Mean confidence of the streak that confirmed the current stable state.
  float stableConfidence() const { return stableConfidence_; }

 private:
  void breakStreak();

  uint8_t required_;
  float minConfidence_;
  SeatState stable_ = SeatState::Unknown;
  SeatState candidate_ = SeatState::Unknown;
  uint8_t streak_ = 0;
  float confidenceSum_ = 0.0f;
  float stableConfidence_ = 0.0f;
};
