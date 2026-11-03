#include "seat_mapper.h"

#include <string.h>

namespace {

bool contains(const SeatRoi& r, float px, float py) {
  return px >= r.x && px <= r.x + r.w && py >= r.y && py <= r.y + r.h;
}

float normDistance(const SeatRoi& r, float px, float py) {
  float dx = (px - (r.x + r.w * 0.5f)) / r.w;
  float dy = (py - (r.y + r.h * 0.5f)) / r.h;
  return dx * dx + dy * dy;  // squared; only compared
}

bool validId(const char* id) {
  if (id == nullptr) return false;
  size_t n = strlen(id);
  if (n == 0 || n > 32) return false;
  for (size_t i = 0; i < n; ++i) {
    char c = id[i];
    bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
              c == '-' || c == '_';
    if (!ok) return false;
  }
  return true;
}

}  // namespace

void mapDetectionsToSeats(const SeatRoi* seats, size_t seatCount, const Detection* detections,
                          size_t detectionCount, float minConfidence, SeatObservation* out) {
  constexpr size_t kMaxSeats = 32;
  bool ambiguous[kMaxSeats] = {};

  for (size_t s = 0; s < seatCount; ++s) {
    out[s].state = SeatState::Empty;
    out[s].confidence = 1.0f;
    out[s].detection = -1;
  }

  for (size_t d = 0; d < detectionCount; ++d) {
    const Detection& det = detections[d];
    if (!(det.confidence >= minConfidence)) continue;
    const float px = det.cx();
    const float py = det.cy();

    int best = -1;
    int second = -1;
    float bestDist = 0.0f;
    float secondDist = 0.0f;
    for (size_t s = 0; s < seatCount && s < kMaxSeats; ++s) {
      if (!contains(seats[s], px, py)) continue;
      float dist = normDistance(seats[s], px, py);
      if (best < 0 || dist < bestDist) {
        second = best;
        secondDist = bestDist;
        best = static_cast<int>(s);
        bestDist = dist;
      } else if (second < 0 || dist < secondDist) {
        second = static_cast<int>(s);
        secondDist = dist;
      }
    }
    if (best < 0) continue;  // not on any seat

    // Squared distances, so compare against the squared ratio.
    const bool clearWinner =
        second < 0 || bestDist * kAmbiguityRatio * kAmbiguityRatio < secondDist;
    if (!clearWinner) {
      for (size_t s = 0; s < seatCount && s < kMaxSeats; ++s) {
        if (contains(seats[s], px, py)) ambiguous[s] = true;
      }
      continue;
    }

    SeatObservation& o = out[best];
    if (o.state != SeatState::Occupied || det.confidence > o.confidence) {
      o.state = SeatState::Occupied;
      o.confidence = det.confidence;
      o.detection = static_cast<int8_t>(d);
    }
  }

  for (size_t s = 0; s < seatCount && s < kMaxSeats; ++s) {
    if (out[s].state != SeatState::Occupied && ambiguous[s]) {
      out[s].state = SeatState::Unknown;
      out[s].confidence = 0.0f;
    }
  }
}

bool validateSeats(const SeatRoi* seats, size_t seatCount, const char** why) {
  if (seatCount == 0) {
    *why = "no seats configured";
    return false;
  }
  if (seatCount > 32) {
    *why = "more than 32 seats";
    return false;
  }
  for (size_t i = 0; i < seatCount; ++i) {
    const SeatRoi& r = seats[i];
    if (!validId(r.id)) {
      *why = "seat id must match ^[A-Za-z0-9_-]{1,32}$";
      return false;
    }
    if (!(r.w > 0.0f) || !(r.h > 0.0f) || r.x < 0.0f || r.y < 0.0f || r.x + r.w > 1.0001f ||
        r.y + r.h > 1.0001f) {
      *why = "ROI must lie inside 0..1 with positive width/height";
      return false;
    }
    for (size_t j = 0; j < i; ++j) {
      if (strcmp(seats[j].id, r.id) == 0) {
        *why = "duplicate seat id";
        return false;
      }
    }
  }
  return true;
}
