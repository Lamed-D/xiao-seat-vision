#pragma once

#include "seat_mapper.h"

// Seat layout: one entry per seat, normalized full-frame coordinates (0..1) of the
// camera image (QVGA 320x240). Edit to match your camera view; the count is not fixed
// (1..32). The device reports these ROIs in its telemetry so the dashboard draws
// exactly what the firmware uses.
//
// Field of view caveat: the Edge Impulse pre-processing centre-crops the 4:3 frame to
// the model's (usually square) input. With a square model only x in ~[0.125, 0.875]
// is seen by the model; ROIs outside that band are reported at boot.
//
// Override the whole table from config.h by defining XSV_SEATS_OVERRIDE and
// providing `kSeats` there.
#ifndef XSV_SEATS_OVERRIDE
static const SeatRoi kSeats[] = {
    {"seat_01", "Seat 1", 0.13f, 0.25f, 0.24f, 0.60f},
    {"seat_02", "Seat 2", 0.38f, 0.25f, 0.24f, 0.60f},
    {"seat_03", "Seat 3", 0.63f, 0.25f, 0.24f, 0.60f},
};
#endif

static const size_t kSeatCount = sizeof(kSeats) / sizeof(kSeats[0]);
