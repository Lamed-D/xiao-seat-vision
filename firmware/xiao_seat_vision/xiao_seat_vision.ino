// xiao-seat-vision — multi-seat occupancy detection on XIAO ESP32S3 Sense
//
// Arduino IDE 2.x sketch. Board: "XIAO_ESP32S3", Tools > PSRAM: "OPI PSRAM".
// Core: esp32 by Espressif Systems 3.3.12 (see README).
//
// loop() is cooperative and millis()-scheduled:
//   Wi-Fi reconnect (non-blocking) -> MQTT queue drain (esp-mqtt runs in its own task)
//   -> serial commands -> optional preview stream -> capture/receive + inference
//   -> per-seat state machines -> events / snapshots / telemetry.
// Only this loop task touches the camera driver (stream + test input use copies).
// No long delay() calls; the only blocking work is one frame capture + inference.

#include <Arduino.h>
#include <esp_system.h>

#include "app_config.h"
#include "camera_manager.h"
#include "inference_manager.h"
#include "mqtt_manager.h"
#include "network_manager.h"
#include "occupancy_manager.h"
#include "report_manager.h"
#include "seat_mapper.h"
#include "seats_config.h"
#include "stream_manager.h"
#include "test_input.h"

namespace {

constexpr uint32_t kMemoryReportMs = 60000;
constexpr uint32_t kNoModelLogMs = 30000;
constexpr size_t kMaxSeats = 32;

OccupancyManager g_seatState[kMaxSeats];
SeatReport g_seatReport[kMaxSeats];
bool g_seatsValid = false;

uint32_t g_bootId = 0;
uint32_t g_eventSeq = 0;
uint32_t g_snapshotSeq = 0;
uint32_t g_lastInferenceMs = 0;
uint32_t g_lastMemoryMs = 0;
uint32_t g_lastNoModelLogMs = 0;
uint32_t g_lastValidResultMs = 0;
uint32_t g_lastTelemetryMs = 0;
uint32_t g_lastSnapshotMs = 0;
bool g_configValid = false;
uint32_t g_modeGeneration = 0;

// Perf bookkeeping for telemetry
InferenceResult g_lastResult;
const char* g_lastStatus = "none";
uint32_t g_okCycles = 0;
uint32_t g_fpsWindowStartMs = 0;
float g_inferenceFps = 0.0f;

bool isValidDeviceId(const char* id) {
  size_t n = strlen(id);
  if (n == 0 || n > 32) return false;
  for (size_t i = 0; i < n; ++i) {
    char c = id[i];
    if (!(isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_')) return false;
  }
  return true;
}

void printMemory() {
  Serial.printf("[mem] heap: total=%lu free=%lu min_free=%lu max_alloc=%lu\n",
                static_cast<unsigned long>(ESP.getHeapSize()), static_cast<unsigned long>(ESP.getFreeHeap()),
                static_cast<unsigned long>(ESP.getMinFreeHeap()), static_cast<unsigned long>(ESP.getMaxAllocHeap()));
  if (psramFound()) {
    Serial.printf("[mem] psram: total=%lu free=%lu min_free=%lu max_alloc=%lu\n",
                  static_cast<unsigned long>(ESP.getPsramSize()), static_cast<unsigned long>(ESP.getFreePsram()),
                  static_cast<unsigned long>(ESP.getMinFreePsram()), static_cast<unsigned long>(ESP.getMaxAllocPsram()));
  } else {
    Serial.println("[mem] psram: NOT FOUND (Arduino IDE: Tools > PSRAM > OPI PSRAM)");
  }
}

void refreshSeatReports() {
  for (size_t i = 0; i < kSeatCount; ++i) {
    g_seatReport[i].roi = &kSeats[i];
    g_seatReport[i].stable = g_seatState[i].stableState();
    g_seatReport[i].stableConfidence = g_seatState[i].stableConfidence();
    g_seatReport[i].candidate = g_seatState[i].candidateState();
    g_seatReport[i].streak = g_seatState[i].streak();
  }
}

TelemetryInput makeTelemetryInput(char* ts, size_t tsLen) {
  refreshSeatReports();
  net::isoTimestamp(ts, tsLen);
  TelemetryInput t;
  t.deviceId = XSV_DEVICE_ID;
  t.timestamp = ts;
  t.inputMode = testinput::modeName();
  t.streaming = stream::enabled();
  t.inferenceFps = g_inferenceFps;
  t.lastInferenceMs = g_lastResult.inferenceMs;
  t.lastPreprocessMs = g_lastResult.preprocessMs;
  t.lastStatus = g_lastStatus;
  t.lastResult = &g_lastResult;
  t.seats = g_seatReport;
  t.seatCount = g_seatsValid ? kSeatCount : 0;
  return t;
}

void printSeats() {
  for (size_t i = 0; i < kSeatCount; ++i) {
    Serial.printf("[seat] %-10s stable=%-8s conf=%.2f candidate=%s x%u\n", kSeats[i].id,
                  seatStateToString(g_seatState[i].stableState()),
                  static_cast<double>(g_seatState[i].stableConfidence()),
                  seatStateToString(g_seatState[i].candidateState()), g_seatState[i].streak());
  }
}

void printStatus() {
  Serial.printf("[status] device=%s config=%s uptime=%lus\n", XSV_DEVICE_ID, XSV_CONFIG_SOURCE,
                static_cast<unsigned long>(millis() / 1000));
  Serial.printf("[status] camera=%s sensor=%s model=%s (%s) input=%s fps=%.2f\n",
                camera::isReady() ? "ready" : "NOT ready", camera::sensorName(),
                inference::modelIntegrated() ? "integrated" : "NOT integrated",
                inference::modelInfo().type, testinput::modeName(), static_cast<double>(g_inferenceFps));
  printSeats();
  net::printStatus();
  mqtt::printStatus();
  stream::printStatus();
  printMemory();
}

// Prints one JPEG frame as base64 between markers; tools/capture_snapshot.py saves it.
void printSnapshot() {
  camera::FrameGuard fb(camera::capture());
  if (!fb) {
    Serial.println("[snapshot] capture failed");
    return;
  }
  static const char kB64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  const uint8_t* p = fb.get()->buf;
  const size_t len = fb.get()->len;
  Serial.printf("-----BEGIN JPEG %u %u %u-----\n", static_cast<unsigned>(len),
                static_cast<unsigned>(fb.get()->width), static_cast<unsigned>(fb.get()->height));
  char line[77];
  size_t col = 0;
  for (size_t i = 0; i < len; i += 3) {
    uint32_t v = static_cast<uint32_t>(p[i]) << 16;
    if (i + 1 < len) v |= static_cast<uint32_t>(p[i + 1]) << 8;
    if (i + 2 < len) v |= p[i + 2];
    line[col++] = kB64[(v >> 18) & 0x3F];
    line[col++] = kB64[(v >> 12) & 0x3F];
    line[col++] = i + 1 < len ? kB64[(v >> 6) & 0x3F] : '=';
    line[col++] = i + 2 < len ? kB64[v & 0x3F] : '=';
    if (col == 76) {
      line[col] = '\0';
      Serial.println(line);
      col = 0;
    }
  }
  if (col > 0) {
    line[col] = '\0';
    Serial.println(line);
  }
  Serial.println("-----END JPEG-----");
}

void handleSerial() {
  while (Serial.available() > 0) {
    switch (Serial.read()) {
      case 's':
        printStatus();
        break;
      case 'c':
        printSnapshot();
        break;
      case 'v':
        stream::setEnabled(!stream::enabled(), "serial command");
        break;
      case 'h':
      case '?':
        Serial.println("[help] s=status  c=capture JPEG snapshot (base64)  v=toggle stream  h=help");
        break;
      default:
        break;
    }
  }
}

void publishSeatEvent(size_t seat, uint32_t inferenceMs, bool isTest, const char* ts) {
  SeatEvent ev = {};
  ++g_eventSeq;
  // <device>-<boot id>-<seq>: unique across reboots, so idempotency by event_id
  // never swallows a real event after a restart resets the sequence counter.
  snprintf(ev.eventId, sizeof(ev.eventId), "%s-%08lx-%06lu", XSV_DEVICE_ID,
           static_cast<unsigned long>(g_bootId), static_cast<unsigned long>(g_eventSeq));
  strncpy(ev.seatId, kSeats[seat].id, sizeof(ev.seatId) - 1);
  strncpy(ev.timestamp, ts, sizeof(ev.timestamp) - 1);
  ev.state = g_seatState[seat].stableState();
  ev.confidence = g_seatState[seat].stableConfidence();
  ev.inferenceMs = inferenceMs;
  ev.isTest = isTest;
  Serial.printf("[state] %s -> %s (conf %.3f) event_id=%s source=%s\n", ev.seatId,
                seatStateToString(ev.state), static_cast<double>(ev.confidence), ev.eventId,
                isTest ? "test_video" : "camera");
  mqtt::publishEvent(ev);
}

// Runs the per-seat state machines for one successful inference result.
// `fb` is still owned by the caller (valid for the snapshot copy).
void processResult(const camera_fb_t* fb, const InferenceResult& r, bool isTest) {
  SeatObservation obs[kMaxSeats];
  if (r.objectDetection) {
    mapDetectionsToSeats(kSeats, kSeatCount, r.detections, r.detectionCount,
                         XSV_PERSON_MIN_CONFIDENCE, obs);
  } else {
    // Classification model: one label for the whole frame -> first seat only.
    for (size_t i = 0; i < kSeatCount; ++i) obs[i] = {SeatState::Unknown, 0.0f, -1};
    obs[0] = {r.state, r.confidence, -1};
  }

  char ts[25];
  net::isoTimestamp(ts, sizeof(ts));
  bool changed = false;
  for (size_t i = 0; i < kSeatCount; ++i) {
    g_seatReport[i].last = obs[i];
    if (g_seatState[i].update(obs[i].state, obs[i].confidence)) {
      publishSeatEvent(i, r.inferenceMs, isTest, ts);
      changed = true;
    }
  }

  const uint32_t now = millis();
  const bool periodicDue =
      XSV_SNAPSHOT_INTERVAL_MS > 0 && now - g_lastSnapshotMs >= XSV_SNAPSHOT_INTERVAL_MS;
  if ((changed || periodicDue) &&
      (g_lastSnapshotMs == 0 || now - g_lastSnapshotMs >= XSV_SNAPSHOT_MIN_GAP_MS)) {
    char ts2[25];
    TelemetryInput t = makeTelemetryInput(ts2, sizeof(ts2));
    if (report::publishSnapshot(fb, t, isTest, changed ? "state_change" : "periodic",
                                ++g_snapshotSeq, g_bootId)) {
      g_lastSnapshotMs = now;
    }
  }
}

void handleResult(const camera_fb_t* fb, const InferenceResult& r, bool isTest) {
  testinput::reportResult(inference::statusToString(r.status), seatStateToString(r.state),
                          r.confidence, r.inferenceMs);
  g_lastStatus = inference::statusToString(r.status);

  if (r.status == InferenceStatus::ModelNotIntegrated) {
    if (g_lastNoModelLogMs == 0 || millis() - g_lastNoModelLogMs >= kNoModelLogMs) {
      g_lastNoModelLogMs = millis();
      Serial.printf("[infer] model not integrated - %s frame %ux%u (%u bytes) OK, "
                    "no inference, seats stay unknown, no events\n",
                    isTest ? "test" : "camera", static_cast<unsigned>(fb->width),
                    static_cast<unsigned>(fb->height), static_cast<unsigned>(fb->len));
    }
    return;
  }
  if (r.status != InferenceStatus::Ok) {
    Serial.printf("[infer] failed: %s\n", inference::statusToString(r.status));
    return;  // staleness check in loop() turns seats UNKNOWN if this persists
  }

  g_lastResult = r;
  g_lastValidResultMs = millis();
  ++g_okCycles;
  if (r.objectDetection) {
    Serial.printf("[infer] %s%u person(s) inference=%lums preprocess=%lums\n", isTest ? "[test] " : "",
                  r.detectionCount, static_cast<unsigned long>(r.inferenceMs),
                  static_cast<unsigned long>(r.preprocessMs));
  } else {
    Serial.printf("[infer] %s%s conf=%.3f inference=%lums preprocess=%lums\n", isTest ? "[test] " : "",
                  seatStateToString(r.state), static_cast<double>(r.confidence),
                  static_cast<unsigned long>(r.inferenceMs), static_cast<unsigned long>(r.preprocessMs));
  }
  processResult(fb, r, isTest);
}

// Classifies one frame from either the camera or the TEST_VIDEO slot.
// Both sources go through the identical inference::classify() path.
void runInferenceCycle() {
  const bool isTest = testinput::mode() == testinput::InputMode::TestVideo;
  if (isTest) {
    camera_fb_t testFb;
    if (!testinput::takeFrame(&testFb)) return;  // no new test frame yet
    stream::offerFrame(&testFb);
    InferenceResult r = inference::classify(&testFb);
    handleResult(&testFb, r, true);
    return;
  }

  camera::FrameGuard fb(camera::capture());
  if (!fb) {
    Serial.println("[camera] capture failed");
    return;
  }
  stream::offerFrame(fb.get());  // copies only if stream ON + client attached
  InferenceResult r = inference::classify(fb.get());
  handleResult(fb.get(), r, false);
}  // FrameGuard returns the camera frame buffer here

void checkStaleness() {
  if (g_lastValidResultMs == 0 || millis() - g_lastValidResultMs < XSV_STALE_MS) return;
  char ts[25];
  net::isoTimestamp(ts, sizeof(ts));
  const bool isTest = testinput::mode() == testinput::InputMode::TestVideo;
  bool any = false;
  for (size_t i = 0; i < kSeatCount; ++i) {
    if (g_seatState[i].forceUnknown()) {
      if (!any) Serial.printf("[state] no valid inference for %lus - seats -> unknown\n",
                              static_cast<unsigned long>(XSV_STALE_MS / 1000));
      any = true;
      publishSeatEvent(i, 0, isTest, ts);
    }
  }
  g_lastValidResultMs = millis();  // re-arm; forceUnknown() is idempotent anyway
}

void updateFps(uint32_t now) {
  if (g_fpsWindowStartMs == 0) g_fpsWindowStartMs = now;
  const uint32_t elapsed = now - g_fpsWindowStartMs;
  if (elapsed >= 10000) {
    g_inferenceFps = static_cast<float>(g_okCycles) * 1000.0f / static_cast<float>(elapsed);
    g_okCycles = 0;
    g_fpsWindowStartMs = now;
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  const uint32_t waitStart = millis();
  while (!Serial && millis() - waitStart < 3000) {
    delay(10);  // USB CDC: give the host a moment to open the port (bounded)
  }

  Serial.println();
  Serial.println("=== xiao-seat-vision firmware ===");
  Serial.printf("[boot] device=%s config=%s chip=%s rev%d cpu=%luMHz flash=%luB core=%s\n",
                XSV_DEVICE_ID, XSV_CONFIG_SOURCE, ESP.getChipModel(), ESP.getChipRevision(),
                static_cast<unsigned long>(ESP.getCpuFreqMHz()),
                static_cast<unsigned long>(ESP.getFlashChipSize()), ESP_ARDUINO_VERSION_STR);
  printMemory();

  g_configValid = isValidDeviceId(XSV_DEVICE_ID);
  if (!g_configValid) {
    Serial.println("[boot] ERROR: XSV_DEVICE_ID must match ^[A-Za-z0-9_-]{1,32}$ - networking disabled");
  }

  const char* why = "";
  g_seatsValid = kSeatCount <= kMaxSeats && validateSeats(kSeats, kSeatCount, &why);
  if (!g_seatsValid) {
    Serial.printf("[boot] ERROR: invalid seats_config.h: %s - seat tracking disabled\n", why);
  }
  for (size_t i = 0; i < kSeatCount && i < kMaxSeats; ++i) {
    g_seatState[i] = OccupancyManager(XSV_STABLE_COUNT, XSV_MIN_CONFIDENCE);
    g_seatReport[i].last = {SeatState::Unknown, 0.0f, -1};
  }

  camera::begin();
  inference::begin();

  const ModelInfo& m = inference::modelInfo();
  Serial.printf("[boot] %u seat(s) configured\n", static_cast<unsigned>(kSeatCount));
  for (size_t i = 0; i < kSeatCount; ++i) {
    const SeatRoi& s = kSeats[i];
    const bool outside = inference::modelIntegrated() &&
                         (s.x < m.viewX || s.x + s.w > m.viewX + m.viewW || s.y < m.viewY ||
                          s.y + s.h > m.viewY + m.viewH);
    Serial.printf("[boot]   %s roi=(%.2f,%.2f %.2fx%.2f)%s\n", s.id, static_cast<double>(s.x),
                  static_cast<double>(s.y), static_cast<double>(s.w), static_cast<double>(s.h),
                  outside ? "  WARNING: partly outside the model's field of view" : "");
  }
  if (inference::modelIntegrated() && strcmp(m.type, "classification") == 0 && kSeatCount > 1) {
    Serial.println("[boot] WARNING: classification model -> only the first seat is tracked; use FOMO for multi-seat");
  }

  report::begin();
  if (g_configValid) {
    net::begin(XSV_WIFI_SSID, XSV_WIFI_PASSWORD, XSV_DEVICE_ID);
    mqtt::Config mc = {XSV_MQTT_URI,     XSV_DEVICE_ID,          XSV_MQTT_PASSWORD,
                       XSV_MQTT_CA_CERT, XSV_MQTT_TOPIC_PREFIX, XSV_MQTT_RECONNECT_MS};
    mqtt::begin(mc);
    if (net::configured()) {
      stream::begin(XSV_WEB_TOKEN, XSV_STREAM_MAX_FPS);
      testinput::begin();
    }
  }

  // Hardware RNG is a true RNG once the radio is on (after WiFi.mode()).
  g_bootId = esp_random();
  printMemory();
  Serial.println("[boot] ready - send 'h' over serial for commands");
}

void loop() {
  const uint32_t now = millis();

  net::loop();
  mqtt::loop(net::connected());
  handleSerial();
  stream::loop();

  // Input mode switched (live <-> test): never mix test frames into a real streak.
  if (testinput::modeGeneration() != g_modeGeneration) {
    g_modeGeneration = testinput::modeGeneration();
    for (size_t i = 0; i < kSeatCount; ++i) g_seatState[i].reset();
    g_lastValidResultMs = 0;
    Serial.printf("[state] input=%s - all seat states reset\n", testinput::modeName());
  }

  const uint32_t interval =
      stream::enabled() ? XSV_INFERENCE_INTERVAL_STREAMING_MS : XSV_INFERENCE_INTERVAL_MS;
  const bool inputReady =
      testinput::mode() == testinput::InputMode::TestVideo || camera::isReady();
  if (g_seatsValid && inputReady && now - g_lastInferenceMs >= interval) {
    g_lastInferenceMs = now;
    runInferenceCycle();
  }
  if (g_seatsValid) checkStaleness();
  updateFps(now);

  if (now - g_lastTelemetryMs >= XSV_TELEMETRY_INTERVAL_MS) {
    g_lastTelemetryMs = now;
    char ts[25];
    report::publishTelemetry(makeTelemetryInput(ts, sizeof(ts)));
  }

  if (now - g_lastMemoryMs >= kMemoryReportMs) {
    g_lastMemoryMs = now;
    printMemory();
  }

  delay(5);  // short yield for the idle task / Wi-Fi stack
}
