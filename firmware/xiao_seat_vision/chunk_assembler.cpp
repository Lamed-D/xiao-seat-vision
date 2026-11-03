#include "chunk_assembler.h"

#include <string.h>

bool ChunkAssembler::add(const char* topic, size_t topicLen, const uint8_t* data, size_t len,
                         size_t offset, size_t total) {
  if (offset == 0) {
    // New message. A previous unfinished one is lost.
    if (active_) ++dropped_;
    active_ = false;
    if (topic == nullptr || topicLen == 0 || topicLen >= kTopicMax || total > cap_) {
      ++dropped_;
      return false;
    }
    memcpy(topic_, topic, topicLen);
    topic_[topicLen] = '\0';
    total_ = total;
    received_ = 0;
    active_ = true;
  }
  if (!active_) return false;  // continuation of a dropped message
  if (offset != received_ || len > total_ - received_) {
    active_ = false;  // out of order or inconsistent sizes
    ++dropped_;
    return false;
  }
  if (len > 0) memcpy(buf_ + offset, data, len);
  received_ += len;
  if (received_ == total_) {
    active_ = false;
    return true;
  }
  return false;
}
