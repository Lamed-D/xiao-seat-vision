#include "network_manager.h"

#include <Arduino.h>
#include <WiFi.h>
#include <time.h>

namespace {
constexpr uint32_t kRetryMinMs = 10000;  // one attempt usually completes well within 10 s
constexpr uint32_t kRetryMaxMs = 60000;

const char* s_ssid = nullptr;
const char* s_password = nullptr;
bool s_configured = false;
bool s_wasConnected = false;
bool s_sntpStarted = false;
uint32_t s_lastAttemptMs = 0;
uint32_t s_retryMs = kRetryMinMs;

void startAttempt() {
  Serial.printf("[wifi] connecting to \"%s\" (next retry in %lus)\n", s_ssid,
                static_cast<unsigned long>(s_retryMs / 1000));
  WiFi.disconnect();
  WiFi.begin(s_ssid, s_password);  // returns immediately
  s_lastAttemptMs = millis();
}
}  // namespace

namespace net {

bool begin(const char* ssid, const char* password, const char* hostname) {
  s_configured = ssid != nullptr && ssid[0] != '\0';
  if (!s_configured) {
    Serial.println("[wifi] not configured (XSV_WIFI_SSID empty) - networking disabled");
    return false;
  }
  s_ssid = ssid;
  s_password = password;
  WiFi.persistent(false);       // don't write credentials to NVS on every begin()
  WiFi.mode(WIFI_STA);
  WiFi.setHostname(hostname);
  WiFi.setAutoReconnect(false);  // reconnect is driven by loop() with backoff
  // USB powered: disable modem sleep. With power save on, the local web API (dashboard
  // test mode, preview stream) answered after 1-8 s instead of < 1 s.
  WiFi.setSleep(false);
  startAttempt();
  return true;
}

void loop() {
  if (!s_configured) return;

  const bool up = WiFi.status() == WL_CONNECTED;
  if (up) {
    if (!s_wasConnected) {
      s_wasConnected = true;
      s_retryMs = kRetryMinMs;
      Serial.printf("[wifi] connected ip=%s rssi=%d dBm\n", WiFi.localIP().toString().c_str(),
                    WiFi.RSSI());
      if (!s_sntpStarted) {
        s_sntpStarted = true;
        configTzTime("UTC0", "pool.ntp.org", "time.google.com");  // non-blocking SNTP
      }
    }
    return;
  }

  if (s_wasConnected) {
    s_wasConnected = false;
    Serial.println("[wifi] connection lost");
    s_lastAttemptMs = millis() - s_retryMs;  // retry right away once
  }

  if (millis() - s_lastAttemptMs >= s_retryMs) {
    startAttempt();
    s_retryMs = s_retryMs * 2 > kRetryMaxMs ? kRetryMaxMs : s_retryMs * 2;
  }
}

bool configured() { return s_configured; }

bool connected() { return s_configured && WiFi.status() == WL_CONNECTED; }

void printStatus() {
  if (!s_configured) {
    Serial.println("[wifi] not configured");
    return;
  }
  if (connected()) {
    Serial.printf("[wifi] connected ip=%s rssi=%d dBm\n", WiFi.localIP().toString().c_str(),
                  WiFi.RSSI());
  } else {
    Serial.printf("[wifi] disconnected (status=%d)\n", static_cast<int>(WiFi.status()));
  }
}

void isoTimestamp(char* out, size_t len) {
  out[0] = '\0';
  time_t now = time(nullptr);
  if (now < 1700000000) return;  // SNTP not synced yet
  struct tm tmv;
  gmtime_r(&now, &tmv);
  strftime(out, len, "%Y-%m-%dT%H:%M:%SZ", &tmv);
}

}  // namespace net
