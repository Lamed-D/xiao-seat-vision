#pragma once

#include <stddef.h>
#include <stdint.h>

#include "detection.h"
#include "seat_state.h"

// Seat region of interest in normalized full-frame coordinates (0..1).
struct SeatRoi {
  const char* id;     // e.g. "seat_01" (^[A-Za-z0-9_-]{1,32}$)
  const char* label;  // display name (UTF-8)
  float x;
  float y;
  float w;
  float h;
};

struct SeatObservation {
  SeatState state;   // Empty / Occupied / Unknown (ambiguous)
  float confidence;  // Occupied: max detection confidence; Empty: 1.0; Unknown: 0
  int8_t detection;  // index of the strongest assigned detection, -1 if none
};

// Matching policy (pure C++, unit-tested on the host):
//  1. Detections below `minConfidence` are ignored.
//  2. A detection belongs to the seat whose ROI contains its centroid.
//  3. If the centroid lies in several ROIs (overlap), the seat whose ROI centre is
//     closest (distance normalized by ROI size) wins only if it is clearly closer
//     (best * kAmbiguityRatio < second). Otherwise the detection is ambiguous: it is
//     assigned to nobody and every overlapping seat without its own clear
//     detection is reported Unknown for this frame.
//  4. A detection is assigned to at most one seat (no double counting).
//  5. A seat with >=1 assigned detection is Occupied; else Unknown if touched by an
//     ambiguous detection; else Empty.
constexpr float kAmbiguityRatio = 1.25f;

void mapDetectionsToSeats(const SeatRoi* seats, size_t seatCount, const Detection* detections,
                          size_t detectionCount, float minConfidence, SeatObservation* out);

// Validates ROI geometry; returns false and fills `why` for the first problem found.
bool validateSeats(const SeatRoi* seats, size_t seatCount, const char** why);
