#pragma once

#include <stdint.h>

// Seat occupancy states. String values match the Supabase `seat_events.state`
// CHECK constraint and the Edge Impulse class labels ("empty", "occupied").
enum class SeatState : uint8_t {
  Unknown = 0,
  Empty,
  Occupied,
};

inline const char* seatStateToString(SeatState s) {
  switch (s) {
    case SeatState::Empty:
      return "empty";
    case SeatState::Occupied:
      return "occupied";
    default:
      return "unknown";
  }
}
