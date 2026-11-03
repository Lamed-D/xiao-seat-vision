#pragma once

#include <stddef.h>
#include <stdint.h>

// Person detection in FULL-FRAME normalized coordinates (0..1).
// Pure data type (no Arduino dependency) shared by inference and seat mapping.
struct Detection {
  float x;  // top-left
  float y;
  float w;
  float h;
  float confidence;

  float cx() const { return x + w * 0.5f; }
  float cy() const { return y + h * 0.5f; }
};

constexpr size_t kMaxDetections = 16;
