#include "occupancy_manager.h"

OccupancyManager::OccupancyManager(uint8_t requiredConsecutive, float minConfidence)
    : required_(requiredConsecutive == 0 ? 1 : requiredConsecutive), minConfidence_(minConfidence) {}

void OccupancyManager::breakStreak() {
  candidate_ = SeatState::Unknown;
  streak_ = 0;
  confidenceSum_ = 0.0f;
}

bool OccupancyManager::update(SeatState observed, float confidence) {
  if (observed == SeatState::Unknown) {
    confidence = 0.0f;
  } else if (!(confidence >= minConfidence_) || confidence > 1.0f) {  // also catches NaN
    breakStreak();
    return false;
  }

  if (observed != candidate_ || streak_ == 0) {
    candidate_ = observed;
    streak_ = 0;
    confidenceSum_ = 0.0f;
  }

  if (streak_ < required_) {
    ++streak_;
    confidenceSum_ += confidence;
  }

  if (streak_ >= required_ && candidate_ != stable_) {
    stable_ = candidate_;
    stableConfidence_ = confidenceSum_ / static_cast<float>(streak_);
    return true;
  }
  return false;
}

bool OccupancyManager::forceUnknown() {
  breakStreak();
  if (stable_ == SeatState::Unknown) return false;
  stable_ = SeatState::Unknown;
  stableConfidence_ = 0.0f;
  return true;
}

void OccupancyManager::reset() {
  breakStreak();
  stable_ = SeatState::Unknown;
  stableConfidence_ = 0.0f;
}
