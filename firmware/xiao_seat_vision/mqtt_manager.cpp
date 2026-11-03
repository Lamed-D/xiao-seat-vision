#include "mqtt_manager.h"

#include <Arduino.h>
#include <string.h>

#include <atomic>

#include "esp_idf_version.h"
#include "mqtt_client.h"

#if ESP_IDF_VERSION_MAJOR < 5
#error "xiao-seat-vision targets Arduino-ESP32 core 3.3.x (ESP-IDF 5.x). See README for the pinned version."
#endif

namespace {

constexpr size_t kQueueCapacity = 16;
constexpr uint32_t kAckTimeoutMs = 15000;
constexpr uint32_t kRetryMinMs = 2000;
constexpr uint32_t kRetryMaxMs = 60000;

mqtt::Config s_cfg = {};
bool s_enabled = false;
bool s_started = false;
esp_mqtt_client_handle_t s_client = nullptr;

char s_eventsTopic[96];
char s_rawTopic[128];
char s_statusTopic[96];
char s_offlinePayload[96];
char s_onlinePayload[96];

// Written by the esp-mqtt task, read by the Arduino loop task.
std::atomic<bool> s_connected{false};
std::atomic<int> s_lastAckedMsgId{-1};
std::atomic<uint32_t> s_disconnectCount{0};

// Event queue: touched only from the Arduino loop task.
SeatEvent s_queue[kQueueCapacity];
size_t s_head = 0;
size_t s_count = 0;
int s_inflightMsgId = -1;
uint32_t s_inflightSinceMs = 0;
uint32_t s_nextAttemptMs = 0;
uint32_t s_retryMs = kRetryMinMs;
uint32_t s_seenDisconnects = 0;

void onMqttEvent(void*, esp_event_base_t, int32_t eventId, void* eventData) {
  auto* event = static_cast<esp_mqtt_event_handle_t>(eventData);
  switch (static_cast<esp_mqtt_event_id_t>(eventId)) {
    case MQTT_EVENT_CONNECTED:
      s_connected = true;
      Serial.printf("[mqtt] connected (session_present=%d)\n", event->session_present);
      // Retained online status; the LWT replaces it with "offline" on abnormal loss.
      esp_mqtt_client_enqueue(s_client, s_statusTopic, s_onlinePayload, 0, 1, 1, true);
      break;
    case MQTT_EVENT_DISCONNECTED:
      s_connected = false;
      s_disconnectCount.fetch_add(1);
      Serial.printf("[mqtt] disconnected - esp-mqtt retries every %lu ms\n",
                    static_cast<unsigned long>(s_cfg.reconnectMs));
      break;
    case MQTT_EVENT_PUBLISHED:
      s_lastAckedMsgId = event->msg_id;
      break;
    case MQTT_EVENT_ERROR:
      if (event->error_handle == nullptr) break;
      if (event->error_handle->error_type == MQTT_ERROR_TYPE_CONNECTION_REFUSED) {
        Serial.printf("[mqtt] connection refused by broker (code %d) - check username/password/ACL\n",
                      static_cast<int>(event->error_handle->connect_return_code));
      } else if (event->error_handle->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
        Serial.printf("[mqtt] transport error: esp_err=0x%x tls_err=0x%x cert_flags=0x%x errno=%d\n",
                      event->error_handle->esp_tls_last_esp_err,
                      event->error_handle->esp_tls_stack_err,
                      event->error_handle->esp_tls_cert_verify_flags,
                      event->error_handle->esp_transport_sock_errno);
        if (event->error_handle->esp_tls_cert_verify_flags != 0) {
          Serial.println("[mqtt] certificate verification failed - check XSV_MQTT_CA_CERT and that the URI host matches the cert CN/SAN");
        }
      }
      break;
    default:
      break;
  }
}

void popFront() {
  s_head = (s_head + 1) % kQueueCapacity;
  --s_count;
}

size_t buildEventJson(const SeatEvent& ev, char* out, size_t outLen) {
  // device_id / event_id are restricted to [A-Za-z0-9_-], so no JSON escaping needed.
  int n = snprintf(out, outLen,
                   "{\"event_id\":\"%s\",\"device_id\":\"%s\",\"seat_id\":\"%s\",\"state\":\"%s\","
                   "\"confidence\":%.4f,\"inference_ms\":%lu,\"is_test\":%s,\"source\":\"%s\"%s%s%s}",
                   ev.eventId, s_cfg.deviceId, ev.seatId, seatStateToString(ev.state),
                   static_cast<double>(ev.confidence), static_cast<unsigned long>(ev.inferenceMs),
                   ev.isTest ? "true" : "false", ev.isTest ? "test_video" : "camera",
                   ev.timestamp[0] ? ",\"timestamp\":\"" : "", ev.timestamp, ev.timestamp[0] ? "\"" : "");
  return (n > 0 && static_cast<size_t>(n) < outLen) ? static_cast<size_t>(n) : 0;
}

void backoff() {
  s_nextAttemptMs = millis() + s_retryMs;
  s_retryMs = s_retryMs * 2 > kRetryMaxMs ? kRetryMaxMs : s_retryMs * 2;
}

}  // namespace

namespace mqtt {

bool begin(const Config& cfg) {
  s_cfg = cfg;
  s_enabled = false;

  if (cfg.uri == nullptr || cfg.uri[0] == '\0') {
    Serial.println("[mqtt] not configured (XSV_MQTT_URI empty) - events will not be sent");
    return false;
  }
  if (strncmp(cfg.uri, "mqtts://", 8) != 0) {
    Serial.println("[mqtt] refusing non-TLS URI: XSV_MQTT_URI must start with mqtts://");
    return false;
  }
  if (cfg.caCertPem == nullptr || strstr(cfg.caCertPem, "BEGIN CERTIFICATE") == nullptr) {
    Serial.println("[mqtt] XSV_MQTT_CA_CERT missing - TLS verification cannot be done, MQTT disabled");
    return false;
  }
  if (cfg.password == nullptr || cfg.password[0] == '\0') {
    Serial.println("[mqtt] XSV_MQTT_PASSWORD empty - broker requires authentication, MQTT disabled");
    return false;
  }

  snprintf(s_eventsTopic, sizeof(s_eventsTopic), "%s/%s/events", cfg.topicPrefix, cfg.deviceId);
  snprintf(s_statusTopic, sizeof(s_statusTopic), "%s/%s/status", cfg.topicPrefix, cfg.deviceId);
  snprintf(s_onlinePayload, sizeof(s_onlinePayload), "{\"device_id\":\"%s\",\"status\":\"online\"}",
           cfg.deviceId);
  snprintf(s_offlinePayload, sizeof(s_offlinePayload),
           "{\"device_id\":\"%s\",\"status\":\"offline\"}", cfg.deviceId);

  // ESP-IDF 5.x (Arduino-ESP32 3.x) nested config layout.
  esp_mqtt_client_config_t mc = {};
  mc.broker.address.uri = cfg.uri;
  mc.broker.verification.certificate = cfg.caCertPem;  // server cert is always verified
  mc.credentials.client_id = cfg.deviceId;
  mc.credentials.username = cfg.deviceId;
  mc.credentials.authentication.password = cfg.password;
  mc.session.keepalive = 30;
  mc.session.last_will.topic = s_statusTopic;
  mc.session.last_will.msg = s_offlinePayload;
  mc.session.last_will.qos = 1;
  mc.session.last_will.retain = 1;
  mc.network.reconnect_timeout_ms = static_cast<int>(cfg.reconnectMs);
  mc.network.timeout_ms = 10000;

  s_client = esp_mqtt_client_init(&mc);
  if (s_client == nullptr) {
    Serial.println("[mqtt] esp_mqtt_client_init failed");
    return false;
  }
  esp_mqtt_client_register_event(s_client, static_cast<esp_mqtt_event_id_t>(ESP_EVENT_ANY_ID),
                                 onMqttEvent, nullptr);
  s_enabled = true;
  Serial.printf("[mqtt] configured: %s user=%s topics=%s, %s\n", cfg.uri, cfg.deviceId,
                s_eventsTopic, s_statusTopic);
  return true;
}

void loop(bool wifiConnected) {
  if (!s_enabled) return;

  if (!s_started) {
    if (!wifiConnected) return;
    esp_err_t err = esp_mqtt_client_start(s_client);
    if (err != ESP_OK) {
      Serial.printf("[mqtt] start failed: %s\n", esp_err_to_name(err));
      return;
    }
    s_started = true;
    Serial.println("[mqtt] client started (connecting in background)");
  }

  // A disconnect invalidates the in-flight publish; resend it (event_id keeps it idempotent).
  const uint32_t disconnects = s_disconnectCount.load();
  if (disconnects != s_seenDisconnects) {
    s_seenDisconnects = disconnects;
    s_inflightMsgId = -1;
  }

  if (s_inflightMsgId >= 0) {
    if (s_lastAckedMsgId.load() == s_inflightMsgId) {
      Serial.printf("[mqtt] event %s acknowledged (PUBACK)\n", s_queue[s_head].eventId);
      popFront();
      s_inflightMsgId = -1;
      s_retryMs = kRetryMinMs;
    } else if (millis() - s_inflightSinceMs > kAckTimeoutMs) {
      Serial.printf("[mqtt] no PUBACK for %s within %lus - will resend\n", s_queue[s_head].eventId,
                    static_cast<unsigned long>(kAckTimeoutMs / 1000));
      s_inflightMsgId = -1;
      backoff();
    }
    return;
  }

  if (s_count == 0 || !s_connected.load()) return;
  if (static_cast<int32_t>(millis() - s_nextAttemptMs) < 0) return;

  char json[512];
  const SeatEvent& ev = s_queue[s_head];
  size_t len = buildEventJson(ev, json, sizeof(json));
  if (len == 0) {
    Serial.printf("[mqtt] event %s could not be encoded - dropped\n", ev.eventId);
    popFront();
    return;
  }
  // QoS 1, retain = false. enqueue() is non-blocking; the esp-mqtt task sends it.
  int msgId = esp_mqtt_client_enqueue(s_client, s_eventsTopic, json, static_cast<int>(len), 1, 0, true);
  if (msgId < 0) {
    Serial.println("[mqtt] enqueue failed - backing off");
    backoff();
    return;
  }
  s_inflightMsgId = msgId;
  s_inflightSinceMs = millis();
  Serial.printf("[mqtt] publish %s -> %s (msg_id=%d)\n", s_eventsTopic, json, msgId);
}

bool publishEvent(const SeatEvent& ev) {
  if (!s_enabled) {
    Serial.printf("[mqtt] disabled - event %s not sent\n", ev.eventId);
    return false;
  }
  if (s_count == kQueueCapacity) {
    if (s_inflightMsgId >= 0) s_inflightMsgId = -1;  // the dropped one may be in flight
    Serial.printf("[mqtt] queue full - dropping oldest event %s\n", s_queue[s_head].eventId);
    popFront();
  }
  s_queue[(s_head + s_count) % kQueueCapacity] = ev;
  ++s_count;
  return true;
}

bool publishRaw(const char* subtopic, const char* payload, size_t len) {
  if (!s_enabled || !s_connected.load()) return false;
  snprintf(s_rawTopic, sizeof(s_rawTopic), "%s/%s/%s", s_cfg.topicPrefix, s_cfg.deviceId, subtopic);
  return esp_mqtt_client_enqueue(s_client, s_rawTopic, payload, static_cast<int>(len), 0, 0, true) >= 0;
}

bool enabled() { return s_enabled; }

bool connected() { return s_enabled && s_connected.load(); }

size_t pendingEvents() { return s_count; }

void printStatus() {
  if (!s_enabled) {
    Serial.println("[mqtt] disabled (see boot log for the reason)");
    return;
  }
  Serial.printf("[mqtt] %s, pending events=%u, in-flight msg_id=%d, disconnects=%lu\n",
                s_connected.load() ? "connected" : (s_started ? "connecting" : "waiting for wifi"),
                static_cast<unsigned>(s_count), s_inflightMsgId,
                static_cast<unsigned long>(s_disconnectCount.load()));
}

}  // namespace mqtt
