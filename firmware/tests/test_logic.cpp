// Host-side unit tests for the pure-C++ firmware logic (no Arduino needed).
// Build + run: see firmware/tests/run.sh (used by CI).
#include <cmath>
#include <cstdio>
#include <cstring>

#include "chunk_assembler.h"
#include "occupancy_manager.h"
#include "seat_mapper.h"

static int g_failures = 0;
static int g_checks = 0;

#define CHECK(cond)                                                     \
  do {                                                                  \
    ++g_checks;                                                         \
    if (!(cond)) {                                                      \
      ++g_failures;                                                     \
      std::printf("  FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);     \
    }                                                                   \
  } while (0)

static Detection det(float cx, float cy, float conf, float w = 0.05f, float h = 0.05f) {
  return Detection{cx - w / 2, cy - h / 2, w, h, conf};
}

static const SeatRoi kThree[] = {
    {"seat_01", "Seat 1", 0.05f, 0.25f, 0.25f, 0.60f},
    {"seat_02", "Seat 2", 0.37f, 0.25f, 0.25f, 0.60f},
    {"seat_03", "Seat 3", 0.69f, 0.25f, 0.25f, 0.60f},
};

// ---------------- OccupancyManager ----------------
static void test_occupancy_confirms_after_three() {
  OccupancyManager m(3, 0.6f);
  CHECK(!m.update(SeatState::Empty, 0.9f));
  CHECK(!m.update(SeatState::Empty, 0.9f));
  CHECK(m.update(SeatState::Empty, 0.9f));
  CHECK(m.stableState() == SeatState::Empty);
  CHECK(!m.update(SeatState::Empty, 0.9f));  // no repeated event
}

static void test_occupancy_interrupted_streak() {
  OccupancyManager m(3, 0.6f);
  m.update(SeatState::Occupied, 0.9f);
  m.update(SeatState::Occupied, 0.9f);
  CHECK(!m.update(SeatState::Empty, 0.9f));
  CHECK(!m.update(SeatState::Occupied, 0.9f));
  CHECK(m.stableState() == SeatState::Unknown);
}

static void test_occupancy_low_confidence_breaks() {
  OccupancyManager m(3, 0.6f);
  m.update(SeatState::Occupied, 0.9f);
  m.update(SeatState::Occupied, 0.9f);
  CHECK(!m.update(SeatState::Occupied, 0.3f));
  CHECK(!m.update(SeatState::Occupied, NAN));
  CHECK(m.streak() == 0);
  CHECK(m.stableState() == SeatState::Unknown);
}

static void test_occupancy_mean_confidence() {
  OccupancyManager m(3, 0.5f);
  m.update(SeatState::Occupied, 0.6f);
  m.update(SeatState::Occupied, 0.8f);
  CHECK(m.update(SeatState::Occupied, 1.0f));
  CHECK(std::fabs(m.stableConfidence() - 0.8f) < 1e-5f);
}

static void test_occupancy_unknown_confirmable_and_force() {
  OccupancyManager m(2, 0.6f);
  m.update(SeatState::Occupied, 0.9f);
  CHECK(m.update(SeatState::Occupied, 0.9f));
  m.update(SeatState::Unknown, 0.0f);
  CHECK(m.update(SeatState::Unknown, 0.0f));  // ambiguous twice -> unknown
  CHECK(m.stableState() == SeatState::Unknown);
  m.update(SeatState::Empty, 0.9f);
  CHECK(m.update(SeatState::Empty, 0.9f));
  CHECK(m.forceUnknown());
  CHECK(!m.forceUnknown());  // idempotent
}

// ---------------- Seat mapper ----------------
static void test_mapper_independent_seats() {
  Detection d[] = {det(0.17f, 0.5f, 0.9f), det(0.80f, 0.5f, 0.7f)};
  SeatObservation o[3];
  mapDetectionsToSeats(kThree, 3, d, 2, 0.5f, o);
  CHECK(o[0].state == SeatState::Occupied && std::fabs(o[0].confidence - 0.9f) < 1e-6f);
  CHECK(o[1].state == SeatState::Empty);
  CHECK(o[2].state == SeatState::Occupied && o[2].detection == 1);
}

static void test_mapper_below_threshold_and_outside() {
  Detection d[] = {det(0.17f, 0.5f, 0.3f), det(0.5f, 0.05f, 0.95f)};
  SeatObservation o[3];
  mapDetectionsToSeats(kThree, 3, d, 2, 0.5f, o);
  for (int i = 0; i < 3; ++i) CHECK(o[i].state == SeatState::Empty);
}

static void test_mapper_overlap_clear_winner_no_double_count() {
  const SeatRoi overlap[] = {
      {"a", "A", 0.10f, 0.20f, 0.40f, 0.60f},  // centre x=0.30
      {"b", "B", 0.35f, 0.20f, 0.40f, 0.60f},  // centre x=0.55
  };
  Detection d[] = {det(0.37f, 0.5f, 0.9f)};  // inside both, much closer to A
  SeatObservation o[2];
  mapDetectionsToSeats(overlap, 2, d, 1, 0.5f, o);
  CHECK(o[0].state == SeatState::Occupied);
  CHECK(o[1].state == SeatState::Empty);  // not double-assigned
}

static void test_mapper_overlap_ambiguous_unknown() {
  const SeatRoi overlap[] = {
      {"a", "A", 0.10f, 0.20f, 0.40f, 0.60f},
      {"b", "B", 0.35f, 0.20f, 0.40f, 0.60f},
  };
  Detection d[] = {det(0.425f, 0.5f, 0.9f)};  // equidistant
  SeatObservation o[2];
  mapDetectionsToSeats(overlap, 2, d, 1, 0.5f, o);
  CHECK(o[0].state == SeatState::Unknown);
  CHECK(o[1].state == SeatState::Unknown);
}

static void test_mapper_ambiguous_does_not_override_clear_detection() {
  const SeatRoi overlap[] = {
      {"a", "A", 0.10f, 0.20f, 0.40f, 0.60f},
      {"b", "B", 0.35f, 0.20f, 0.40f, 0.60f},
  };
  Detection d[] = {det(0.425f, 0.5f, 0.9f), det(0.70f, 0.5f, 0.8f)};
  SeatObservation o[2];
  mapDetectionsToSeats(overlap, 2, d, 2, 0.5f, o);
  CHECK(o[0].state == SeatState::Unknown);
  CHECK(o[1].state == SeatState::Occupied);
}

static void test_person_moves_between_seats() {
  OccupancyManager s[3] = {OccupancyManager(3, 0.5f), OccupancyManager(3, 0.5f),
                           OccupancyManager(3, 0.5f)};
  SeatObservation o[3];
  Detection inSeat1[] = {det(0.17f, 0.5f, 0.9f)};
  Detection inSeat2[] = {det(0.50f, 0.5f, 0.9f)};
  for (int f = 0; f < 3; ++f) {
    mapDetectionsToSeats(kThree, 3, inSeat1, 1, 0.5f, o);
    for (int i = 0; i < 3; ++i) s[i].update(o[i].state, o[i].confidence);
  }
  CHECK(s[0].stableState() == SeatState::Occupied);
  CHECK(s[1].stableState() == SeatState::Empty);
  int changes = 0;
  for (int f = 0; f < 3; ++f) {
    mapDetectionsToSeats(kThree, 3, inSeat2, 1, 0.5f, o);
    for (int i = 0; i < 3; ++i) changes += s[i].update(o[i].state, o[i].confidence) ? 1 : 0;
  }
  CHECK(s[0].stableState() == SeatState::Empty);
  CHECK(s[1].stableState() == SeatState::Occupied);
  CHECK(s[2].stableState() == SeatState::Empty);
  CHECK(changes == 2);  // seat_03 untouched
}

static void test_validate_seats() {
  const char* why = "";
  CHECK(validateSeats(kThree, 3, &why));
  const SeatRoi bad[] = {{"x", "X", 0.9f, 0.1f, 0.2f, 0.2f}};
  CHECK(!validateSeats(bad, 1, &why));
  const SeatRoi dup[] = {{"x", "X", 0.1f, 0.1f, 0.2f, 0.2f}, {"x", "Y", 0.5f, 0.1f, 0.2f, 0.2f}};
  CHECK(!validateSeats(dup, 2, &why));
  const SeatRoi badId[] = {{"seat 1", "X", 0.1f, 0.1f, 0.2f, 0.2f}};
  CHECK(!validateSeats(badId, 1, &why));
}

// ---------------- ChunkAssembler (MQTT downlink reassembly) ----------------
static void test_chunks_single_and_multi() {
  uint8_t buf[16];
  ChunkAssembler a(buf, sizeof(buf));
  const uint8_t msg[] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
  CHECK(a.add("t/cmd", 5, msg, 10, 0, 10));  // whole message in one chunk
  CHECK(std::strcmp(a.topic(), "t/cmd") == 0 && a.size() == 10 && a.data()[9] == 10);
  CHECK(!a.add("t/frame", 7, msg, 4, 0, 10));  // 3 chunks, topic only on the first
  CHECK(!a.add(nullptr, 0, msg + 4, 4, 4, 10));
  CHECK(a.add(nullptr, 0, msg + 8, 2, 8, 10));
  CHECK(std::strcmp(a.topic(), "t/frame") == 0 && std::memcmp(a.data(), msg, 10) == 0);
  CHECK(a.dropped() == 0);
}

static void test_chunks_too_big_and_out_of_order() {
  uint8_t buf[8];
  ChunkAssembler a(buf, sizeof(buf));
  const uint8_t msg[12] = {};
  CHECK(!a.add("t", 1, msg, 4, 0, 12));        // larger than the buffer -> dropped
  CHECK(!a.add(nullptr, 0, msg, 4, 4, 12));    // its continuation is ignored
  CHECK(!a.add("t", 1, msg, 4, 0, 8));
  CHECK(!a.add(nullptr, 0, msg, 2, 6, 8));     // gap -> dropped
  CHECK(!a.add(nullptr, 0, msg, 2, 6, 8));
  CHECK(a.add("t", 1, msg, 0, 0, 0));          // empty payload is a valid message
  CHECK(a.dropped() == 2);
}

int main() {
  test_occupancy_confirms_after_three();
  test_occupancy_interrupted_streak();
  test_occupancy_low_confidence_breaks();
  test_occupancy_mean_confidence();
  test_occupancy_unknown_confirmable_and_force();
  test_mapper_independent_seats();
  test_mapper_below_threshold_and_outside();
  test_mapper_overlap_clear_winner_no_double_count();
  test_mapper_overlap_ambiguous_unknown();
  test_mapper_ambiguous_does_not_override_clear_detection();
  test_person_moves_between_seats();
  test_validate_seats();
  test_chunks_single_and_multi();
  test_chunks_too_big_and_out_of_order();
  std::printf("%d checks, %d failures\n", g_checks, g_failures);
  return g_failures == 0 ? 0 : 1;
}
