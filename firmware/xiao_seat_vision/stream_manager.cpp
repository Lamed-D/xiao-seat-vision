#include "stream_manager.h"

#include <Arduino.h>
#include <string.h>

#include <atomic>

#include "camera_manager.h"
#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

namespace {

constexpr size_t kFrameCapacity = 64 * 1024;          // QVGA JPEG is typically 5-20 KB
constexpr uint32_t kIdleAutoOffMs = 60000;            // ON but no client -> OFF
constexpr size_t kMinFreePsram = 512 * 1024;          // memory guard
constexpr uint8_t kMaxCaptureFailures = 3;            // camera guard
constexpr uint32_t kClientFrameWaitMs = 3000;         // no new frame -> drop client

const char* s_token = nullptr;
uint32_t s_framePeriodMs = 333;
httpd_handle_t s_ctrlServer = nullptr;
httpd_handle_t s_streamServer = nullptr;

std::atomic<bool> s_enabled{false};
std::atomic<bool> s_clientActive{false};
std::atomic<uint32_t> s_frameSeq{0};
std::atomic<uint32_t> s_framesSent{0};

// Latest JPEG for the stream (PSRAM), protected by s_frameMutex.
SemaphoreHandle_t s_frameMutex = nullptr;
uint8_t* s_frame = nullptr;
size_t s_frameLen = 0;

// Loop-task-only state.
uint32_t s_lastFrameMs = 0;
uint32_t s_idleSinceMs = 0;
uint8_t s_captureFailures = 0;

bool tokenOk(httpd_req_t* req) {
  char query[128];
  char value[64];
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK) return false;
  if (httpd_query_key_value(query, "token", value, sizeof(value)) != ESP_OK) return false;
  size_t a = strlen(value);
  size_t b = strlen(s_token);
  uint8_t diff = static_cast<uint8_t>(a != b);
  for (size_t i = 0; i < a && i < b; ++i) diff |= static_cast<uint8_t>(value[i] ^ s_token[i]);
  return diff == 0;
}

esp_err_t reject(httpd_req_t* req) {
  httpd_resp_set_status(req, "401 Unauthorized");
  httpd_resp_set_type(req, "text/plain");
  return httpd_resp_sendstr(req, "missing or invalid token");
}

const char kIndexHtml[] = R"HTML(<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>xiao-seat-vision</title>
<style>body{font-family:sans-serif;margin:16px;max-width:680px}button{padding:8px 16px;margin-right:8px}
#v{width:100%;max-width:640px;background:#222;min-height:120px;display:block;margin-top:12px}pre{background:#f4f4f4;padding:8px}</style>
</head><body>
<h2>xiao-seat-vision</h2>
<p>AI inference runs regardless of the stream. Stream: <b id="s">?</b></p>
<button onclick="setStream(1)">Stream ON</button><button onclick="setStream(0)">Stream OFF</button>
<img id="v" alt="stream off">
<pre id="st"></pre>
<script>
const tok=new URLSearchParams(location.search).get('token')||'';
const q='token='+encodeURIComponent(tok);
const img=document.getElementById('v');
async function refresh(){
  try{const r=await fetch('/api/status?'+q);const j=await r.json();
    document.getElementById('s').textContent=j.streaming?'ON':'OFF';
    document.getElementById('st').textContent=JSON.stringify(j,null,1);
    if(!j.streaming&&img.src){img.removeAttribute('src');}
    if(j.streaming&&!img.src){img.src='http://'+location.hostname+':81/stream?'+q+'&t='+Date.now();}
  }catch(e){document.getElementById('st').textContent='status error: '+e;}
}
async function setStream(on){
  if(!on){img.removeAttribute('src');}
  await fetch('/api/stream?enable='+on+'&'+q,{method:'POST'});refresh();
}
refresh();setInterval(refresh,3000);
</script></body></html>)HTML";

esp_err_t handleIndex(httpd_req_t* req) {
  if (!tokenOk(req)) return reject(req);
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, kIndexHtml, HTTPD_RESP_USE_STRLEN);
}

esp_err_t handleStatus(httpd_req_t* req) {
  if (!tokenOk(req)) return reject(req);
  char json[256];
  snprintf(json, sizeof(json),
           "{\"streaming\":%s,\"client\":%s,\"frames_sent\":%lu,\"max_fps\":%lu,"
           "\"free_heap\":%lu,\"free_psram\":%lu,\"uptime_s\":%lu}",
           s_enabled.load() ? "true" : "false", s_clientActive.load() ? "true" : "false",
           static_cast<unsigned long>(s_framesSent.load()),
           static_cast<unsigned long>(1000 / s_framePeriodMs),
           static_cast<unsigned long>(ESP.getFreeHeap()),
           static_cast<unsigned long>(ESP.getFreePsram()),
           static_cast<unsigned long>(millis() / 1000));
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  // Lets the dashboard (another origin) read the status after a simple GET/POST.
  // The token is still required; CORS is not access control.
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  return httpd_resp_sendstr(req, json);
}

esp_err_t handleToggle(httpd_req_t* req) {
  if (!tokenOk(req)) return reject(req);
  char query[128];
  char value[8];
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) != ESP_OK ||
      httpd_query_key_value(query, "enable", value, sizeof(value)) != ESP_OK) {
    httpd_resp_set_status(req, "400 Bad Request");
    return httpd_resp_sendstr(req, "enable=0|1 required");
  }
  stream::setEnabled(value[0] == '1', "web request");
  return handleStatus(req);
}

esp_err_t handleStream(httpd_req_t* req) {
  if (!tokenOk(req)) return reject(req);
  if (!s_enabled.load()) {
    httpd_resp_set_status(req, "409 Conflict");
    return httpd_resp_sendstr(req, "streaming is OFF");
  }
  bool expected = false;
  if (!s_clientActive.compare_exchange_strong(expected, true)) {
    httpd_resp_set_status(req, "503 Service Unavailable");
    return httpd_resp_sendstr(req, "another client is streaming");
  }

  // Per-client copy so the shared buffer is locked only for a memcpy.
  uint8_t* local = static_cast<uint8_t*>(heap_caps_malloc(kFrameCapacity, MALLOC_CAP_SPIRAM));
  if (local == nullptr) {
    s_clientActive = false;
    httpd_resp_set_status(req, "503 Service Unavailable");
    return httpd_resp_sendstr(req, "out of memory");
  }

  Serial.println("[stream] client attached");
  httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=xsvframe");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");

  uint32_t lastSeq = s_frameSeq.load();
  uint32_t waitStart = millis();
  esp_err_t res = ESP_OK;
  while (s_enabled.load()) {
    if (s_frameSeq.load() == lastSeq) {
      if (millis() - waitStart > kClientFrameWaitMs) break;  // camera stalled
      vTaskDelay(pdMS_TO_TICKS(20));
      continue;
    }
    size_t len = 0;
    if (xSemaphoreTake(s_frameMutex, pdMS_TO_TICKS(200)) == pdTRUE) {
      len = s_frameLen;
      memcpy(local, s_frame, len);
      lastSeq = s_frameSeq.load();
      xSemaphoreGive(s_frameMutex);
    }
    waitStart = millis();
    if (len == 0) continue;

    char header[96];
    int hlen = snprintf(header, sizeof(header),
                        "\r\n--xsvframe\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                        static_cast<unsigned>(len));
    res = httpd_resp_send_chunk(req, header, hlen);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, reinterpret_cast<const char*>(local), len);
    if (res != ESP_OK) break;  // client went away
    s_framesSent.fetch_add(1);
  }

  if (res == ESP_OK) httpd_resp_send_chunk(req, nullptr, 0);
  heap_caps_free(local);
  s_clientActive = false;
  Serial.println("[stream] client detached");
  return ESP_OK;
}

bool startServers() {
  httpd_config_t ctrl = HTTPD_DEFAULT_CONFIG();
  ctrl.server_port = 80;
  ctrl.ctrl_port = 32768;
  ctrl.max_uri_handlers = 8;
  ctrl.lru_purge_enable = true;
  if (httpd_start(&s_ctrlServer, &ctrl) != ESP_OK) return false;

  httpd_uri_t index = {"/", HTTP_GET, handleIndex, nullptr};
  httpd_uri_t status = {"/api/status", HTTP_GET, handleStatus, nullptr};
  httpd_uri_t toggle = {"/api/stream", HTTP_POST, handleToggle, nullptr};
  httpd_register_uri_handler(s_ctrlServer, &index);
  httpd_register_uri_handler(s_ctrlServer, &status);
  httpd_register_uri_handler(s_ctrlServer, &toggle);

  httpd_config_t st = HTTPD_DEFAULT_CONFIG();
  st.server_port = 81;
  st.ctrl_port = 32769;
  st.max_uri_handlers = 1;
  st.max_open_sockets = 2;
  st.lru_purge_enable = true;
  st.send_wait_timeout = 3;  // seconds; a stalled client is dropped
  if (httpd_start(&s_streamServer, &st) != ESP_OK) {
    httpd_stop(s_ctrlServer);
    s_ctrlServer = nullptr;
    return false;
  }
  httpd_uri_t streamUri = {"/stream", HTTP_GET, handleStream, nullptr};
  httpd_register_uri_handler(s_streamServer, &streamUri);
  return true;
}

void publishFrame(const camera_fb_t* fb) {
  if (fb == nullptr || fb->format != PIXFORMAT_JPEG || fb->len == 0) return;
  if (fb->len > kFrameCapacity) return;
  if (xSemaphoreTake(s_frameMutex, pdMS_TO_TICKS(50)) != pdTRUE) return;
  memcpy(s_frame, fb->buf, fb->len);
  s_frameLen = fb->len;
  xSemaphoreGive(s_frameMutex);
  s_frameSeq.fetch_add(1);
  s_lastFrameMs = millis();
}

}  // namespace

namespace stream {

bool begin(const char* token, uint8_t maxFps) {
  if (token == nullptr || strlen(token) < 8) {
    Serial.println("[stream] disabled: XSV_WEB_TOKEN empty/short (min 8 chars) - web server not started");
    return false;
  }
  if (!psramFound()) {
    Serial.println("[stream] disabled: requires PSRAM");
    return false;
  }
  s_token = token;
  s_framePeriodMs = 1000 / (maxFps == 0 ? 1 : (maxFps > 10 ? 10 : maxFps));
  s_frameMutex = xSemaphoreCreateMutex();
  s_frame = static_cast<uint8_t*>(heap_caps_malloc(kFrameCapacity, MALLOC_CAP_SPIRAM));
  if (s_frameMutex == nullptr || s_frame == nullptr) {
    Serial.println("[stream] disabled: allocation failed");
    return false;
  }
  if (!startServers()) {
    Serial.println("[stream] disabled: httpd_start failed");
    return false;
  }
  Serial.printf("[stream] web server ready (stream OFF by default): http://<device-ip>/?token=...  max %u fps\n",
                static_cast<unsigned>(1000 / s_framePeriodMs));
  return true;
}

void setEnabled(bool on, const char* reason) {
  if (s_ctrlServer == nullptr) return;
  if (s_enabled.exchange(on) != on) {
    Serial.printf("[stream] %s (%s)\n", on ? "ON" : "OFF", reason);
    s_idleSinceMs = millis();
    s_captureFailures = 0;
  }
}

void offerFrame(const camera_fb_t* fb) {
  if (!s_enabled.load() || !s_clientActive.load()) return;
  publishFrame(fb);
}

void loop() {
  if (s_ctrlServer == nullptr || !s_enabled.load()) return;
  const uint32_t now = millis();

  // Memory guard: stop the stream first so inference keeps its memory.
  if (ESP.getFreePsram() < kMinFreePsram) {
    setEnabled(false, "low PSRAM");
    return;
  }

  if (!s_clientActive.load()) {
    if (now - s_idleSinceMs > kIdleAutoOffMs) setEnabled(false, "no client for 60 s");
    return;
  }
  s_idleSinceMs = now;

  if (now - s_lastFrameMs < s_framePeriodMs) return;
  camera::FrameGuard fb(camera::capture());
  if (!fb) {
    if (++s_captureFailures >= kMaxCaptureFailures) setEnabled(false, "camera capture failures");
    s_lastFrameMs = now;
    return;
  }
  s_captureFailures = 0;
  publishFrame(fb.get());
}  // FrameGuard returns the frame buffer here

bool available() { return s_ctrlServer != nullptr; }
httpd_handle_t controlServer() { return s_ctrlServer; }
bool authorize(httpd_req_t* req) {
  if (tokenOk(req)) return true;
  reject(req);
  return false;
}
bool enabled() { return s_enabled.load(); }
bool clientActive() { return s_clientActive.load(); }

void printStatus() {
  if (!available()) {
    Serial.println("[stream] unavailable (see boot log)");
    return;
  }
  Serial.printf("[stream] %s client=%s frames_sent=%lu\n", s_enabled.load() ? "ON" : "OFF",
                s_clientActive.load() ? "yes" : "no", static_cast<unsigned long>(s_framesSent.load()));
}

}  // namespace stream
