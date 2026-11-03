#pragma once

// Edge Impulse model integration switch.
//
// Default: commented out -> firmware builds WITHOUT a model and reports
// "model not integrated" (no classification, no seat events).
//
// After installing the Edge Impulse Arduino library
// (Arduino IDE: Sketch > Include Library > Add .ZIP Library...),
// uncomment the line below and set it to the header name found in the
// library's src/ folder, e.g. src/xiao-seat-vision_inferencing.h.
//
// Alternatively define it in config.h (git-ignored) to enable the model only on this PC
// while the repository default stays "no model" (keeps the CI no-model build green).
// CI can also inject it: --build-property compiler.cpp.extra_flags=-DXSV_EI_MODEL_HEADER=...
#ifndef XSV_EI_MODEL_HEADER
// #define XSV_EI_MODEL_HEADER "xiao-seat-vision_inferencing.h"
#endif
