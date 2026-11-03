#include "report_manager.h"

#include <Arduino.h>
#include <stdarg.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "mqtt_manager.h"

namespace {

constexpr size_t kTelemetryCapacity = 8 * 1024;
constexpr size_t kSnapshotMaxJpeg = 64 * 1024;
constexpr size_t kSnapshotCapacity = (kSnapshotMaxJpeg / 3 + 1) * 4 + 8 * 1024;

char* s_telemetry = nullptr;
char* s_snapshot = nullptr;

// Minimal append-only JSON writer into a fixed buffer.
class Writer {
 public:
  Writer(char* buf, size_t cap) : buf_(buf), cap_(cap) { buf_[0] = '\0'; }
  void raw(const char* fmt, ...) __attribute__((format(printf, 2, 3))) {
    if (overflow_) return;
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf_ + len_, cap_ - len_, fmt, ap);
    va_end(ap);
    if (n < 0 || static_cast<size_t>(n) >= cap_ - len_) {
      overflow_ = true;
      return;
    }
    len_ += static_cast<size_t>(n);
  }
  void str(const char* s) {  // JSON string with escaping (labels are user-defined UTF-8)
    raw("\"");
    for (const char* p = s; p != nullptr && *p != '\0' && !overflow_; ++p) {
      unsigned char c = static_cast<unsigned char>(*p);
      if (c == '"' || c == '\\') {
        raw("\\%c", c);
      } else if (c < 0x20) {
        raw("\\u%04x", c);
      } else {
        raw("%c", c);
      }
    }
    raw("\"");
  }
  void base64(const uint8_t* p, size_t len) {
    static const char kB64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    if (overflow_ || cap_ - len_ < (len / 3 + 1) * 4 + 1) {
      overflow_ = true;
      return;
    }
    for (size_t i = 0; i < len; i += 3) {
      uint32_t v = static_cast<uint32_t>(p[i]) << 16;
      if (i + 1 < len) v |= static_cast<uint32_t>(p[i + 1]) << 8;
      if (i + 2 < len) v |= p[i + 2];
      buf_[len_++] = kB64[(v >> 18) & 0x3F];
      buf_[len_++] = kB64[(v >> 12) & 0x3F];
      buf_[len_++] = i + 1 < len ? kB64[(v >> 6) & 0x3F] : '=';
      buf_[len_++] = i + 2 < len ? kB64[v & 0x3F] : '=';
    }
    buf_[len_] = '\0';
  }
  size_t length() const { return overflow_ ? 0 : len_; }

 private:
  char* buf_;
  size_t cap_;
  size_t len_ = 0;
  bool overflow_ = false;
};

void writeSeats(Writer& w, const TelemetryInput& t, bool detail) {
  w.raw("\"seats\":[");
  for (size_t i = 0; i < t.seatCount; ++i) {
    const SeatReport& s = t.seats[i];
    w.raw("%s{\"seat_id\":", i ? "," : "");
    w.str(s.roi->id);
    w.raw(",\"label\":");
    w.str(s.roi->label);
    w.raw(",\"state\":\"%s\",\"confidence\":%.3f", seatStateToString(s.stable),
          static_cast<double>(s.stableConfidence));
    w.raw(",\"roi\":{\"x\":%.4f,\"y\":%.4f,\"width\":%.4f,\"height\":%.4f}",
          static_cast<double>(s.roi->x), static_cast<double>(s.roi->y),
          static_cast<double>(s.roi->w), static_cast<double>(s.roi->h));
    if (detail) {
      w.raw(",\"candidate\":\"%s\",\"streak\":%u,\"last_observation\":\"%s\",\"last_confidence\":%.3f,"
            "\"last_detection\":%d",
            seatStateToString(s.candidate), s.streak, seatStateToString(s.last.state),
            static_cast<double>(s.last.confidence), s.last.detection);
    }
    w.raw("}");
  }
  w.raw("]");
}

void writeDetections(Writer& w, const InferenceResult* r) {
  w.raw("\"detections\":[");
  if (r != nullptr) {
    for (uint8_t i = 0; i < r->detectionCount; ++i) {
      const Detection& d = r->detections[i];
      w.raw("%s{\"x\":%.4f,\"y\":%.4f,\"width\":%.4f,\"height\":%.4f,\"cx\":%.4f,\"cy\":%.4f,"
            "\"confidence\":%.3f}",
            i ? "," : "", static_cast<double>(d.x), static_cast<double>(d.y),
            static_cast<double>(d.w), static_cast<double>(d.h), static_cast<double>(d.cx()),
            static_cast<double>(d.cy()), static_cast<double>(d.confidence));
    }
  }
  w.raw("]");
}

}  // namespace

namespace report {

bool begin() {
  s_telemetry = static_cast<char*>(heap_caps_malloc(kTelemetryCapacity, MALLOC_CAP_SPIRAM));
  s_snapshot = static_cast<char*>(heap_caps_malloc(kSnapshotCapacity, MALLOC_CAP_SPIRAM));
  if (s_telemetry == nullptr || s_snapshot == nullptr) {
    Serial.println("[report] PSRAM allocation failed - telemetry/snapshots disabled");
    heap_caps_free(s_telemetry);
    heap_caps_free(s_snapshot);
    s_telemetry = s_snapshot = nullptr;
    return false;
  }
  return true;
}

bool publishTelemetry(const TelemetryInput& t) {
  if (s_telemetry == nullptr || !mqtt::connected()) return false;
  const ModelInfo& m = inference::modelInfo();
  Writer w(s_telemetry, kTelemetryCapacity);
  w.raw("{\"device_id\":");
  w.str(t.deviceId);
  if (t.timestamp[0]) {
    w.raw(",\"timestamp\":");
    w.str(t.timestamp);
  }
  w.raw(",\"uptime_s\":%lu,\"input_mode\":\"%s\",\"streaming\":%s",
        static_cast<unsigned long>(millis() / 1000), t.inputMode, t.streaming ? "true" : "false");
  w.raw(",\"model\":{\"name\":");
  w.str(m.name);
  w.raw(",\"project_id\":%d,\"version\":%d,\"type\":\"%s\",\"input\":[%d,%d],"
        "\"view\":{\"x\":%.4f,\"y\":%.4f,\"width\":%.4f,\"height\":%.4f}}",
        m.projectId, m.deployVersion, m.type, m.inputWidth, m.inputHeight,
        static_cast<double>(m.viewX), static_cast<double>(m.viewY), static_cast<double>(m.viewW),
        static_cast<double>(m.viewH));
  w.raw(",\"perf\":{\"inference_fps\":%.2f,\"inference_ms\":%lu,\"preprocess_ms\":%lu,"
        "\"last_status\":\"%s\"}",
        static_cast<double>(t.inferenceFps), static_cast<unsigned long>(t.lastInferenceMs),
        static_cast<unsigned long>(t.lastPreprocessMs), t.lastStatus);
  w.raw(",\"memory\":{\"heap_free\":%lu,\"heap_min_free\":%lu,\"psram_free\":%lu,"
        "\"psram_min_free\":%lu}",
        static_cast<unsigned long>(ESP.getFreeHeap()), static_cast<unsigned long>(ESP.getMinFreeHeap()),
        static_cast<unsigned long>(ESP.getFreePsram()), static_cast<unsigned long>(ESP.getMinFreePsram()));
  w.raw(",");
  writeSeats(w, t, true);
  w.raw(",");
  writeDetections(w, t.lastResult);
  w.raw("}");
  size_t len = w.length();
  if (len == 0) {
    Serial.println("[report] telemetry too large - not sent");
    return false;
  }
  return mqtt::publishRaw("telemetry", s_telemetry, len);
}

bool publishSnapshot(const camera_fb_t* fb, const TelemetryInput& t, bool isTest,
                     const char* reason, uint32_t seq, uint32_t bootId) {
  if (s_snapshot == nullptr || !mqtt::connected()) return false;
  if (fb == nullptr || fb->format != PIXFORMAT_JPEG || fb->len == 0 || fb->len > kSnapshotMaxJpeg) {
    return false;
  }
  Writer w(s_snapshot, kSnapshotCapacity);
  w.raw("{\"snapshot_id\":\"%s-%08lx-s%06lu\",\"device_id\":", t.deviceId,
        static_cast<unsigned long>(bootId), static_cast<unsigned long>(seq));
  w.str(t.deviceId);
  if (t.timestamp[0]) {
    w.raw(",\"timestamp\":");
    w.str(t.timestamp);
  }
  w.raw(",\"reason\":\"%s\",\"is_test\":%s,\"source\":\"%s\",\"width\":%u,\"height\":%u,",
        reason, isTest ? "true" : "false", isTest ? "test_video" : "camera",
        static_cast<unsigned>(fb->width), static_cast<unsigned>(fb->height));
  writeSeats(w, t, false);
  w.raw(",");
  writeDetections(w, t.lastResult);
  w.raw(",\"image_b64\":\"");
  w.base64(fb->buf, fb->len);
  w.raw("\"}");
  size_t len = w.length();
  if (len == 0) return false;
  bool ok = mqtt::publishRaw("snapshot", s_snapshot, len);
  Serial.printf("[report] snapshot (%s) %u bytes JPEG -> %s\n", reason,
                static_cast<unsigned>(fb->len), ok ? "queued" : "dropped");
  return ok;
}

}  // namespace report
