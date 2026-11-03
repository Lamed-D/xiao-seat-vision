#pragma once

// xiao-seat-vision device configuration TEMPLATE.
//
// 1. Copy this file to `config.h` in this sketch folder (git-ignored).
// 2. Fill in real values there. Never commit config.h.
//
// When config.h does not exist the firmware still compiles with these empty
// placeholders; Wi-Fi/MQTT then stay disabled and the serial log says so.

// Device identity. Must match ^[A-Za-z0-9_-]{1,32}$.
// It is also the MQTT username (the broker ACL uses %u = username).
#define XSV_DEVICE_ID "xiao-01"

// Wi-Fi (2.4 GHz only on ESP32-S3)
#define XSV_WIFI_SSID ""
#define XSV_WIFI_PASSWORD ""

// MQTT over TLS. Only mqtts:// is accepted; certificate verification is always on.
// Example: "mqtts://broker.example.com:8883" or "mqtts://192.168.0.10:8883"
// The host must match the CN/SAN in the broker certificate.
#define XSV_MQTT_URI ""
#define XSV_MQTT_PASSWORD ""

// Topic prefix: topics become <prefix>/<device_id>/events and /status
#define XSV_MQTT_TOPIC_PREFIX "xiao-seat-vision"

// PEM of the CA that signed the broker certificate (dev: mqtt/certs/ca.crt,
// prod: the public root CA of your broker certificate, e.g. ISRG Root X1).
// Use a raw string literal:
//   #define XSV_MQTT_CA_CERT R"PEM(
//   -----BEGIN CERTIFICATE-----
//   ...
//   -----END CERTIFICATE-----
//   )PEM"
#define XSV_MQTT_CA_CERT ""

// Local web UI (stream ON/OFF, TEST_VIDEO input). Empty = web server NOT started.
// Use a random string (min 8 chars); it is sent as ?token= over plain HTTP on the LAN,
// so only use it on a trusted network.
#define XSV_WEB_TOKEN ""

// ---- Optional tuning (defaults live in app_config.h) ----
// #define XSV_INFERENCE_INTERVAL_MS 1000
// #define XSV_STABLE_COUNT 3
// #define XSV_MIN_CONFIDENCE 0.60f
// #define XSV_MQTT_RECONNECT_MS 5000
// #define XSV_STREAM_MAX_FPS 3
// #define XSV_INFERENCE_INTERVAL_STREAMING_MS 2000
