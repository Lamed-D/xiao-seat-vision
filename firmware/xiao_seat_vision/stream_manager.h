#pragma once

#include <stdint.h>

#include "esp_camera.h"
#include "esp_http_server.h"

// Optional, OFF-by-default MJPEG preview stream (auxiliary to AI inference).
//
// Ownership rule: only the Arduino loop task touches the camera driver.
// The HTTP stream handler (separate httpd task) never calls esp_camera_fb_get();
// it only reads a mutex-protected copy of the latest JPEG published by the loop.
//
//  port 80 : GET /            control page (ON/OFF buttons)
//            GET /api/status  JSON status
//            POST /api/stream?enable=0|1
//  port 81 : GET /stream      multipart MJPEG (one client at a time)
//
// Every request needs ?token=<XSV_WEB_TOKEN>. With an empty token the web
// server is not started at all.
namespace stream {

bool begin(const char* token, uint8_t maxFps);

// Call every loop(): auto-off checks and (only while ON with a client attached)
// rate-limited captures for the stream.
void loop();

// Offer a frame that the loop already captured (e.g. for inference).
// Copies only when streaming is ON and a client is attached.
void offerFrame(const camera_fb_t* fb);

void setEnabled(bool on, const char* reason);
bool available();     // web server running
bool enabled();       // streaming switched ON
bool clientActive();  // a /stream client is attached
void printStatus();

// Shared with other web modules (test_input):
httpd_handle_t controlServer();
// Returns true if ?token= matches; otherwise sends 401 and returns false.
bool authorize(httpd_req_t* req);

}  // namespace stream
