#pragma once

// Selects the real config (config.h, git-ignored) when present, otherwise the
// committed placeholder template so the firmware always compiles (CI, fresh clones).
#if __has_include("config.h")
#include "config.h"
#define XSV_CONFIG_SOURCE "config.h"
#else
#include "config.example.h"
#define XSV_CONFIG_SOURCE "config.example.h (placeholders - network disabled)"
#endif

#ifndef XSV_INFERENCE_INTERVAL_MS
#define XSV_INFERENCE_INTERVAL_MS 1000
#endif

#ifndef XSV_STABLE_COUNT
#define XSV_STABLE_COUNT 3
#endif

#ifndef XSV_MIN_CONFIDENCE
#define XSV_MIN_CONFIDENCE 0.60f
#endif

#ifndef XSV_MQTT_RECONNECT_MS
#define XSV_MQTT_RECONNECT_MS 5000
#endif

#ifndef XSV_MQTT_TOPIC_PREFIX
#define XSV_MQTT_TOPIC_PREFIX "xiao-seat-vision"
#endif

#ifndef XSV_WEB_TOKEN
#define XSV_WEB_TOKEN ""
#endif

#ifndef XSV_STREAM_MAX_FPS
#define XSV_STREAM_MAX_FPS 3
#endif

// Inference is slowed down while the preview stream is ON to keep CPU for AI + MQTT.
#ifndef XSV_INFERENCE_INTERVAL_STREAMING_MS
#define XSV_INFERENCE_INTERVAL_STREAMING_MS 2000
#endif

// ---- Multi-seat (FOMO) ----
// Person detections below this confidence are ignored by the seat mapper.
#ifndef XSV_PERSON_MIN_CONFIDENCE
#define XSV_PERSON_MIN_CONFIDENCE 0.50f
#endif

// No successful inference for this long -> every seat is forced to UNKNOWN.
#ifndef XSV_STALE_MS
#define XSV_STALE_MS 15000
#endif

// Periodic state/perf report (telemetry topic), separate from change events.
#ifndef XSV_TELEMETRY_INTERVAL_MS
#define XSV_TELEMETRY_INTERVAL_MS 30000
#endif

// JPEG snapshot with detections: periodic interval (0 = only on state change) and
// minimum gap between two snapshots.
#ifndef XSV_SNAPSHOT_INTERVAL_MS
#define XSV_SNAPSHOT_INTERVAL_MS 60000
#endif
#ifndef XSV_SNAPSHOT_MIN_GAP_MS
#define XSV_SNAPSHOT_MIN_GAP_MS 10000
#endif
