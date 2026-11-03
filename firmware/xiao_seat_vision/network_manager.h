#pragma once

// Wi-Fi station management with non-blocking reconnect and exponential backoff.
#include <stddef.h>

// Transport-agnostic: MQTT (mqtt_manager) sits on top of it.
namespace net {

bool begin(const char* ssid, const char* password, const char* hostname);
void loop();  // call every loop(); never blocks
bool configured();
bool connected();
void printStatus();

// ISO-8601 UTC time ("2026-11-03T10:00:00Z") once SNTP has synced; "" before that.
void isoTimestamp(char* out, size_t len);

}  // namespace net
