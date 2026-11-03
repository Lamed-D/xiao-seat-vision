#pragma once

#include <stddef.h>
#include <stdint.h>

// Reassembles MQTT messages that esp-mqtt delivers in several MQTT_EVENT_DATA chunks
// (payload larger than its receive buffer). Pure C++, unit-tested on the host.
//
// Rules: a message starts with offset 0 (topic only present on that chunk), chunks must
// arrive in order, messages larger than the buffer are dropped (not truncated).
class ChunkAssembler {
 public:
  static constexpr size_t kTopicMax = 96;

  ChunkAssembler(uint8_t* buffer, size_t capacity) : buf_(buffer), cap_(capacity) {}

  // Returns true when this chunk completed a message; then topic()/data()/size() are valid
  // until the next add().
  bool add(const char* topic, size_t topicLen, const uint8_t* data, size_t len, size_t offset,
           size_t total);

  const char* topic() const { return topic_; }
  const uint8_t* data() const { return buf_; }
  size_t size() const { return total_; }
  uint32_t dropped() const { return dropped_; }

 private:
  uint8_t* buf_;
  size_t cap_;
  char topic_[kTopicMax] = {};
  size_t total_ = 0;
  size_t received_ = 0;
  bool active_ = false;
  uint32_t dropped_ = 0;
};
