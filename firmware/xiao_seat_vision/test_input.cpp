#include "test_input.h"

#include <Arduino.h>
#include <string.h>

#include <atomic>

#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "stream_manager.h"

namespace {

constexpr size_t kMaxFrameBytes = 64 * 1024;
constexpr uint16_t kMaxWidth = 640;
constexpr uint16_t kMaxHeight = 480;

std::atomic<uint8_t> s_mode{static_cast<uint8_t>(testinput::InputMode::LiveCamera)};
std::atomic<uint32_t> s_generation{0};

SemaphoreHandle_t s_mutex = nullptr;
// Pending slot (written by httpd task, read by loop task) — guarded by s_mutex.
uint8_t* s_pending = nullptr;
size_t s_pendingLen = 0;
uint16_t s_pendingW = 0;
uint16_t s_pendingH = 0;
bool s_pendingFresh = false;
uint32_t s_received = 0;
uint32_t s_dropped = 0;
char s_lastResult[160] = "{\"status\":\"none\"}";

// Loop-owned copy handed out by takeFrame().
uint8_t* s_loopBuf = nullptr;

// Reads the real image size from the JPEG SOF marker; never trust client-sent sizes
// because the decoder output buffer is sized from them.
bool jpegSize(const uint8_t* d, size_t len, uint16_t* w, uint16_t* h) {
  if (len < 4 || d[0] != 0xFF || d[1] != 0xD8) return false;
  size_t i = 2;
  while (i + 9 < len) {
    if (d[i] != 0xFF) return false;
    uint8_t marker = d[i + 1];
    if (marker == 0xFF) {  // fill byte
      ++i;
      continue;
    }
    size_t segLen = (static_cast<size_t>(d[i + 2]) << 8) | d[i + 3];
    bool isSof = marker >= 0xC0 && marker <= 0xCF && marker != 0xC4 && marker != 0xC8 &&
                 marker != 0xCC;
    if (isSof) {
      *h = static_cast<uint16_t>((d[i + 5] << 8) | d[i + 6]);
      *w = static_cast<uint16_t>((d[i + 7] << 8) | d[i + 8]);
      return *w > 0 && *h > 0;
    }
    if (marker == 0xDA || segLen < 2) return false;  // start of scan before SOF
    i += 2 + segLen;
  }
  return false;
}

esp_err_t sendJson(httpd_req_t* req, const char* status, const char* body) {
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_sendstr(req, body);
}

esp_err_t handleMode(httpd_req_t* req) {
  if (!stream::authorize(req)) return ESP_OK;
  char query[128];
  char value[16];
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK &&
      httpd_query_key_value(query, "mode", value, sizeof(value)) == ESP_OK) {
    testinput::InputMode m;
    if (strcmp(value, "live_camera") == 0) {
      m = testinput::InputMode::LiveCamera;
    } else if (strcmp(value, "test_video") == 0) {
      m = testinput::InputMode::TestVideo;
    } else {
      return sendJson(req, "400 Bad Request", "{\"error\":\"mode must be live_camera|test_video\"}");
    }
    if (s_mode.exchange(static_cast<uint8_t>(m)) != static_cast<uint8_t>(m)) {
      s_generation.fetch_add(1);
      Serial.printf("[input] mode -> %s\n", testinput::modeName());
    }
  }
  char body[64];
  snprintf(body, sizeof(body), "{\"mode\":\"%s\"}", testinput::modeName());
  return sendJson(req, "200 OK", body);
}

esp_err_t handleFrame(httpd_req_t* req) {
  if (!stream::authorize(req)) return ESP_OK;
  if (testinput::mode() != testinput::InputMode::TestVideo) {
    return sendJson(req, "409 Conflict", "{\"error\":\"device is in live_camera mode\"}");
  }
  if (req->content_len == 0 || req->content_len > kMaxFrameBytes) {
    return sendJson(req, "413 Payload Too Large", "{\"error\":\"jpeg must be 1..65536 bytes\"}");
  }

  const size_t len = req->content_len;
  uint8_t* tmp = static_cast<uint8_t*>(heap_caps_malloc(len, MALLOC_CAP_SPIRAM));
  if (tmp == nullptr) return sendJson(req, "503 Service Unavailable", "{\"error\":\"out of memory\"}");
  size_t got = 0;
  while (got < len) {
    int r = httpd_req_recv(req, reinterpret_cast<char*>(tmp + got), len - got);
    if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
    if (r <= 0) {
      heap_caps_free(tmp);
      return ESP_FAIL;  // connection closed
    }
    got += static_cast<size_t>(r);
  }

  uint16_t w = 0;
  uint16_t h = 0;
  if (!jpegSize(tmp, len, &w, &h) || w > kMaxWidth || h > kMaxHeight) {
    heap_caps_free(tmp);
    return sendJson(req, "422 Unprocessable Entity",
                    "{\"error\":\"not a baseline JPEG or larger than 640x480\"}");
  }

  char body[256];
  if (xSemaphoreTake(s_mutex, pdMS_TO_TICKS(500)) != pdTRUE) {
    heap_caps_free(tmp);
    return sendJson(req, "503 Service Unavailable", "{\"error\":\"busy\"}");
  }
  if (s_pendingFresh) ++s_dropped;  // previous frame was never processed
  memcpy(s_pending, tmp, len);
  s_pendingLen = len;
  s_pendingW = w;
  s_pendingH = h;
  s_pendingFresh = true;
  ++s_received;
  snprintf(body, sizeof(body), "{\"accepted\":true,\"received\":%lu,\"dropped\":%lu,\"last_result\":%s}",
           static_cast<unsigned long>(s_received), static_cast<unsigned long>(s_dropped), s_lastResult);
  xSemaphoreGive(s_mutex);
  heap_caps_free(tmp);
  return sendJson(req, "202 Accepted", body);
}

}  // namespace

namespace testinput {

bool begin() {
  httpd_handle_t server = stream::controlServer();
  if (server == nullptr) {
    Serial.println("[input] TEST_VIDEO unavailable (web server not running) - LIVE_CAMERA only");
    return false;
  }
  s_mutex = xSemaphoreCreateMutex();
  s_pending = static_cast<uint8_t*>(heap_caps_malloc(kMaxFrameBytes, MALLOC_CAP_SPIRAM));
  s_loopBuf = static_cast<uint8_t*>(heap_caps_malloc(kMaxFrameBytes, MALLOC_CAP_SPIRAM));
  if (s_mutex == nullptr || s_pending == nullptr || s_loopBuf == nullptr) {
    Serial.println("[input] TEST_VIDEO unavailable (allocation failed)");
    return false;
  }
  httpd_uri_t modeUri = {"/api/mode", HTTP_POST, handleMode, nullptr};
  httpd_uri_t frameUri = {"/api/test-frame", HTTP_POST, handleFrame, nullptr};
  httpd_register_uri_handler(server, &modeUri);
  httpd_register_uri_handler(server, &frameUri);
  Serial.println("[input] mode=live_camera (TEST_VIDEO available via POST /api/mode)");
  return true;
}

InputMode mode() { return static_cast<InputMode>(s_mode.load()); }

const char* modeName() { return mode() == InputMode::TestVideo ? "test_video" : "live_camera"; }

uint32_t modeGeneration() { return s_generation.load(); }

bool takeFrame(camera_fb_t* fb) {
  if (s_mutex == nullptr || xSemaphoreTake(s_mutex, pdMS_TO_TICKS(50)) != pdTRUE) return false;
  bool fresh = s_pendingFresh;
  if (fresh) {
    memcpy(s_loopBuf, s_pending, s_pendingLen);
    memset(fb, 0, sizeof(*fb));
    fb->buf = s_loopBuf;
    fb->len = s_pendingLen;
    fb->width = s_pendingW;
    fb->height = s_pendingH;
    fb->format = PIXFORMAT_JPEG;
    s_pendingFresh = false;
  }
  xSemaphoreGive(s_mutex);
  return fresh;
}

void reportResult(const char* status, const char* state, float confidence, uint32_t inferenceMs) {
  if (s_mutex == nullptr || xSemaphoreTake(s_mutex, pdMS_TO_TICKS(50)) != pdTRUE) return;
  snprintf(s_lastResult, sizeof(s_lastResult),
           "{\"status\":\"%s\",\"state\":\"%s\",\"confidence\":%.4f,\"inference_ms\":%lu}", status,
           state, static_cast<double>(confidence), static_cast<unsigned long>(inferenceMs));
  xSemaphoreGive(s_mutex);
}

}  // namespace testinput
