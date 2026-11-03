#pragma once

#include <stddef.h>
#include <stdint.h>

#include "seat_state.h"

// Seat state change event, published as JSON to <prefix>/<device_id>/events.
struct SeatEvent {
  char eventId[64];
  char seatId[33];
  char timestamp[25];  // ISO-8601 UTC from SNTP, "" if the clock is not synced yet
  SeatState state;
  float confidence;
  uint32_t inferenceMs;
  bool isTest;  // true when the frame came from TEST_VIDEO (source="test_video")
};

// MQTT over TLS using the ESP-IDF esp-mqtt client bundled with Arduino-ESP32.
// esp-mqtt runs in its own FreeRTOS task (connect, TLS handshake, reconnect),
// so the camera/inference loop is never blocked by networking.
//
//  - events    : QoS 1, retain=false, kept in a local queue until PUBACK
//  - telemetry : QoS 0 periodic report (seat states, perf, memory)
//  - snapshot  : QoS 0 JPEG (base64 JSON) with detections
//  - status : retained "online" on connect, retained "offline" via LWT
namespace mqtt {

struct Config {
  const char* uri;          // must start with mqtts://
  const char* deviceId;     // also MQTT username and client id
  const char* password;
  const char* caCertPem;    // CA that signed the broker certificate
  const char* topicPrefix;  // e.g. "xiao-seat-vision"
  uint32_t reconnectMs;
};

bool begin(const Config& cfg);
// Call every loop(). Starts the client once Wi-Fi is up and drains the event queue.
void loop(bool wifiConnected);
// Queue an event (drops the oldest if the queue is full). Returns false if MQTT is disabled.
bool publishEvent(const SeatEvent& ev);

// Best-effort (QoS 0, not retained, not queued) publish to <prefix>/<device_id>/<subtopic>.
// Used for periodic telemetry and snapshots; dropped while disconnected.
bool publishRaw(const char* subtopic, const char* payload, size_t len);

bool enabled();
bool connected();
size_t pendingEvents();
void printStatus();

}  // namespace mqtt
