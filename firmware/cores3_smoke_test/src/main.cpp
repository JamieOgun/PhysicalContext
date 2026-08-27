#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <M5Unified.h>
#include <WiFi.h>
#include <esp_camera.h>
#include <esp_heap_caps.h>
#include <esp_system.h>
#include <img_converters.h>
#include <time.h>

#include <new>

#include "local_config.h"
#include "secrets.h"
#include "status_ui.h"

constexpr int PIN_WHITE = 1;
constexpr int PIN_YELLOW = 2;
constexpr unsigned long DEBOUNCE_MS = 800;
constexpr unsigned long CONFIRMATION_MS = 3000;
constexpr unsigned long RETRY_DELAY_MS = 2000;
constexpr unsigned long STATUS_POLL_MS = 500;
constexpr unsigned long STATUS_TIMEOUT_MS = 90000;
constexpr uint8_t JPEG_QUALITY = 80;
constexpr size_t UPLOAD_QUEUE_SIZE = 3;
constexpr char MULTIPART_BOUNDARY[] = "----PhysicalContextBoundary7MA4YWxk";

class GC0308 {
public:
  camera_fb_t* fb = nullptr;
  sensor_t* sensor = nullptr;
  camera_config_t* config = nullptr;

  bool begin();
  bool get();
  bool free();
};

static camera_config_t camera_config = {
  .pin_pwdn = -1,
  .pin_reset = -1,
  .pin_xclk = -1,
  .pin_sccb_sda = 12,
  .pin_sccb_scl = 11,
  .pin_d7 = 47,
  .pin_d6 = 48,
  .pin_d5 = 16,
  .pin_d4 = 15,
  .pin_d3 = 42,
  .pin_d2 = 41,
  .pin_d1 = 40,
  .pin_d0 = 39,
  .pin_vsync = 46,
  .pin_href = 38,
  .pin_pclk = 45,
  .xclk_freq_hz = 20000000,
  .ledc_timer = LEDC_TIMER_0,
  .ledc_channel = LEDC_CHANNEL_0,
  .pixel_format = PIXFORMAT_RGB565,
  .frame_size = FRAMESIZE_QVGA,
  .jpeg_quality = 0,
  .fb_count = 2,
  .fb_location = CAMERA_FB_IN_PSRAM,
  .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
  .sccb_i2c_port = -1,
};

enum class UploadEventType : uint8_t {
  Sending,
  Retrying,
  Captioning,
  Succeeded,
  CaptionUnavailable,
  ProcessingDelayed,
};

enum class CapturePollResult : uint8_t { Processing, Ready, NotFound, Failed };

struct PendingCapture {
  uint8_t* jpegBuffer = nullptr;
  size_t jpegLength = 0;
  char clientCaptureId[37] = {};
  uint64_t deviceTimestamp = 0;
  int count = 0;
};

struct UploadEvent {
  UploadEventType type = UploadEventType::Sending;
  int count = 0;
  int httpStatus = 0;
  char captureId[33] = {};
  char shortId[9] = {};
  float sharpness = 0;
  float brightness = 0;
  bool hasBlurryClassification = false;
  bool isBlurry = false;
};

struct CaptionJob {
  int count = 0;
  char captureId[33] = {};
  char shortId[9] = {};
  float sharpness = 0;
  float brightness = 0;
  bool hasBlurryClassification = false;
  bool isBlurry = false;
};

GC0308 Camera;
QueueHandle_t uploadQueue = nullptr;
QueueHandle_t uploadEventQueue = nullptr;
QueueHandle_t captionQueue = nullptr;
char deviceId[24] = {};
int captureCount = 0;
bool confirmationVisible = false;
unsigned long confirmationStartedAt = 0;
StatusUi statusUi;

bool GC0308::begin() {
  config = &camera_config;
  M5.In_I2C.release();
  esp_err_t err = esp_camera_init(config);
  if (err != ESP_OK) {
    return false;
  }
  sensor = esp_camera_sensor_get();
  if (sensor == nullptr) {
    return false;
  }

  // The GC0308 delivers a horizontally mirrored frame. Any text in the scene
  // then reaches the captioner reversed, which makes the caption's visible-text
  // field worthless for retrieval. If pictures still read mirrored after
  // flashing, change this 1 to a 0.
  if (sensor->set_hmirror != nullptr) {
    Serial.printf("Camera hmirror: %d\n", sensor->set_hmirror(sensor, 1));
  } else {
    Serial.println("Camera hmirror unsupported by this sensor driver");
  }
  return true;
}

bool GC0308::get() {
  fb = esp_camera_fb_get();
  return fb != nullptr;
}

bool GC0308::free() {
  if (fb == nullptr) {
    return false;
  }
  esp_camera_fb_return(fb);
  fb = nullptr;
  return true;
}

void makeClientCaptureId(char* output, size_t outputSize) {
  uint8_t bytes[16];
  esp_fill_random(bytes, sizeof(bytes));
  bytes[6] = (bytes[6] & 0x0F) | 0x40;
  bytes[8] = (bytes[8] & 0x3F) | 0x80;
  snprintf(output, outputSize,
           "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-"
           "%02x%02x%02x%02x%02x%02x",
           bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5],
           bytes[6], bytes[7], bytes[8], bytes[9], bytes[10], bytes[11],
           bytes[12], bytes[13], bytes[14], bytes[15]);
}

uint64_t currentDeviceTimestamp() {
  time_t now = time(nullptr);
  return now >= 1700000000 ? static_cast<uint64_t>(now) : 0;
}

String makeTextPart(const char* name, const String& value) {
  String part = "--";
  part += MULTIPART_BOUNDARY;
  part += "\r\nContent-Disposition: form-data; name=\"";
  part += name;
  part += "\"\r\n\r\n";
  part += value;
  part += "\r\n";
  return part;
}

bool uploadCapture(const PendingCapture& capture, UploadEvent& event) {
  char timestamp[24];
  snprintf(timestamp, sizeof(timestamp), "%llu",
           static_cast<unsigned long long>(capture.deviceTimestamp));

  String prefix = makeTextPart("device_ts", timestamp);
  prefix += makeTextPart("device_id", deviceId);
  prefix += makeTextPart("client_capture_id", capture.clientCaptureId);
  prefix += "--";
  prefix += MULTIPART_BOUNDARY;
  prefix += "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"";
  prefix += "\r\nContent-Type: image/jpeg\r\n\r\n";

  String suffix = "\r\n--";
  suffix += MULTIPART_BOUNDARY;
  suffix += "--\r\n";

  size_t bodyLength = prefix.length() + capture.jpegLength + suffix.length();
  uint8_t* body = static_cast<uint8_t*>(
      heap_caps_malloc(bodyLength, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
  if (body == nullptr) {
    body = static_cast<uint8_t*>(malloc(bodyLength));
  }
  if (body == nullptr) {
    return false;
  }

  size_t offset = 0;
  memcpy(body + offset, prefix.c_str(), prefix.length());
  offset += prefix.length();
  memcpy(body + offset, capture.jpegBuffer, capture.jpegLength);
  offset += capture.jpegLength;
  memcpy(body + offset, suffix.c_str(), suffix.length());

  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(5000);
  if (!http.begin(DAEMON_URL)) {
    free(body);
    return false;
  }

  String contentType = "multipart/form-data; boundary=";
  contentType += MULTIPART_BOUNDARY;
  http.addHeader("Content-Type", contentType);
  int statusCode = http.POST(body, bodyLength);
  String responseBody = statusCode > 0 ? http.getString() : "";
  http.end();
  free(body);

  event.httpStatus = statusCode;
  if (statusCode != HTTP_CODE_OK && statusCode != HTTP_CODE_CREATED) {
    return false;
  }

  JsonDocument document;
  DeserializationError error = deserializeJson(document, responseBody);
  const char* captureId = document["capture_id"] | "";
  const char* shortId = document["short_id"] | "";
  if (error || captureId[0] == '\0' || shortId[0] == '\0') {
    return false;
  }

  snprintf(event.captureId, sizeof(event.captureId), "%.32s", captureId);
  snprintf(event.shortId, sizeof(event.shortId), "%.8s", shortId);
  event.sharpness = document["sharpness"] | 0.0f;
  event.brightness = document["brightness"] | 0.0f;
  event.hasBlurryClassification = document["is_blurry"].is<bool>();
  event.isBlurry = document["is_blurry"] | false;
  return true;
}

CapturePollResult pollCaptureStatus(const char* captureId,
                                    bool& captionAvailable,
                                    int& httpStatus) {
  String statusUrl = DAEMON_URL;
  statusUrl += "/";
  statusUrl += captureId;
  statusUrl += "/status";

  HTTPClient http;
  http.setConnectTimeout(3000);
  http.setTimeout(5000);
  if (!http.begin(statusUrl)) {
    return CapturePollResult::Failed;
  }

  httpStatus = http.GET();
  String responseBody = httpStatus > 0 ? http.getString() : "";
  http.end();

  if (httpStatus == HTTP_CODE_NOT_FOUND) {
    return CapturePollResult::NotFound;
  }
  if (httpStatus != HTTP_CODE_OK) {
    return CapturePollResult::Failed;
  }

  JsonDocument document;
  DeserializationError error = deserializeJson(document, responseBody);
  const char* state = document["state"] | "";
  if (error || state[0] == '\0') {
    return CapturePollResult::Failed;
  }

  if (strcmp(state, "ready") == 0) {
    captionAvailable = document["caption_available"] | false;
    return CapturePollResult::Ready;
  }
  return CapturePollResult::Processing;
}

void sendUploadEvent(const UploadEvent& event) {
  xQueueSend(uploadEventQueue, &event, pdMS_TO_TICKS(100));
}

bool queueCaptionStatus(const UploadEvent& upload) {
  CaptionJob job{};
  job.count = upload.count;
  snprintf(job.captureId, sizeof(job.captureId), "%s", upload.captureId);
  snprintf(job.shortId, sizeof(job.shortId), "%s", upload.shortId);
  job.sharpness = upload.sharpness;
  job.brightness = upload.brightness;
  job.hasBlurryClassification = upload.hasBlurryClassification;
  job.isBlurry = upload.isBlurry;
  return xQueueSend(captionQueue, &job, pdMS_TO_TICKS(100)) == pdTRUE;
}

void uploadWorkerTask(void*) {
  while (true) {
    PendingCapture* capture = nullptr;
    if (xQueueReceive(uploadQueue, &capture, portMAX_DELAY) != pdTRUE || capture == nullptr) {
      continue;
    }

    UploadEvent event{};
    event.type = UploadEventType::Sending;
    event.count = capture->count;
    sendUploadEvent(event);

    while (true) {
      UploadEvent result{};
      result.count = capture->count;
      if (WiFi.status() == WL_CONNECTED && uploadCapture(*capture, result)) {
        result.type = UploadEventType::Captioning;
        sendUploadEvent(result);
        if (!queueCaptionStatus(result)) {
          result.type = UploadEventType::ProcessingDelayed;
          sendUploadEvent(result);
        }
        break;
      }

      result.type = UploadEventType::Retrying;
      sendUploadEvent(result);
      vTaskDelay(pdMS_TO_TICKS(RETRY_DELAY_MS));
    }

    free(capture->jpegBuffer);
    delete capture;
  }
}

void captionStatusWorkerTask(void*) {
  while (true) {
    CaptionJob job{};
    if (xQueueReceive(captionQueue, &job, portMAX_DELAY) != pdTRUE) {
      continue;
    }

    unsigned long startedAt = millis();
    while (true) {
      bool captionAvailable = false;
      int httpStatus = 0;
      CapturePollResult pollResult = CapturePollResult::Failed;
      if (WiFi.status() == WL_CONNECTED) {
        pollResult = pollCaptureStatus(job.captureId, captionAvailable, httpStatus);
      }

      if (pollResult == CapturePollResult::Ready) {
        UploadEvent event{};
        event.type = captionAvailable ? UploadEventType::Succeeded
                                      : UploadEventType::CaptionUnavailable;
        event.count = job.count;
        event.httpStatus = httpStatus;
        snprintf(event.shortId, sizeof(event.shortId), "%s", job.shortId);
        event.sharpness = job.sharpness;
        event.brightness = job.brightness;
        event.hasBlurryClassification = job.hasBlurryClassification;
        event.isBlurry = job.isBlurry;
        sendUploadEvent(event);
        break;
      }

      if (pollResult == CapturePollResult::NotFound ||
          millis() - startedAt >= STATUS_TIMEOUT_MS) {
        UploadEvent event{};
        event.type = UploadEventType::ProcessingDelayed;
        event.count = job.count;
        event.httpStatus = httpStatus;
        snprintf(event.shortId, sizeof(event.shortId), "%s", job.shortId);
        sendUploadEvent(event);
        break;
      }

      vTaskDelay(pdMS_TO_TICKS(STATUS_POLL_MS));
    }
  }
}

bool captureAndQueue(int count) {
  confirmationVisible = false;
  statusUi.setState(UiState::Capturing, count);
  if (!Camera.get()) {
    statusUi.setState(UiState::Error, count, nullptr,
                      "Camera did not return an image");
    return false;
  }

  M5.Display.pushImage(0, 0, Camera.fb->width, Camera.fb->height,
                       reinterpret_cast<uint16_t*>(Camera.fb->buf));

  uint8_t* jpegBuffer = nullptr;
  size_t jpegLength = 0;
  bool converted = frame2jpg(Camera.fb, JPEG_QUALITY, &jpegBuffer, &jpegLength);
  Camera.free();

  if (!converted || jpegBuffer == nullptr) {
    free(jpegBuffer);
    statusUi.setState(UiState::Error, count, nullptr, "Image conversion failed");
    return false;
  }

  PendingCapture* capture = new (std::nothrow) PendingCapture{};
  if (capture == nullptr) {
    free(jpegBuffer);
    statusUi.setState(UiState::Error, count, nullptr, "Not enough memory");
    return false;
  }

  capture->jpegBuffer = jpegBuffer;
  capture->jpegLength = jpegLength;
  capture->deviceTimestamp = currentDeviceTimestamp();
  capture->count = count;
  makeClientCaptureId(capture->clientCaptureId, sizeof(capture->clientCaptureId));

  if (xQueueSend(uploadQueue, &capture, 0) != pdTRUE) {
    free(capture->jpegBuffer);
    delete capture;
    statusUi.setState(UiState::Error, count, nullptr, "Upload queue is full");
    return false;
  }

  Serial.printf("Queued JPEG: %u bytes id=%s\n",
                static_cast<unsigned>(jpegLength), capture->clientCaptureId);
  statusUi.setState(UiState::Queued, count);
  return true;
}

void handleUploadEvents() {
  UploadEvent event{};
  while (xQueueReceive(uploadEventQueue, &event, 0) == pdTRUE) {
    if (event.count < captureCount) {
      continue;
    }

    switch (event.type) {
      case UploadEventType::Sending:
        statusUi.setState(UiState::Sending, event.count);
        break;
      case UploadEventType::Retrying:
        statusUi.setState(UiState::Retrying, event.count);
        confirmationVisible = false;
        Serial.printf("Upload failed: HTTP %d; retrying\n", event.httpStatus);
        break;
      case UploadEventType::Captioning:
        statusUi.setState(UiState::Captioning, event.count, event.shortId);
        Serial.printf("Upload OK %s; captioning started\n", event.shortId);
        break;
      case UploadEventType::Succeeded:
        statusUi.setState(UiState::Complete, event.count, event.shortId,
                          event.hasBlurryClassification && event.isBlurry
                              ? "Caption ready - image may be blurry"
                              : "Caption ready");
        confirmationStartedAt = millis();
        confirmationVisible = true;
        Serial.printf("Caption ready %s sharpness=%.2f brightness=%.2f\n",
                      event.shortId, event.sharpness, event.brightness);
        break;
      case UploadEventType::CaptionUnavailable:
        statusUi.setState(UiState::CaptionUnavailable, event.count,
                          event.shortId);
        confirmationStartedAt = millis();
        confirmationVisible = true;
        Serial.printf("Saved %s without a caption\n", event.shortId);
        break;
      case UploadEventType::ProcessingDelayed:
        statusUi.setState(UiState::ProcessingDelayed, event.count,
                          event.shortId);
        confirmationStartedAt = millis();
        confirmationVisible = true;
        Serial.printf("Caption status delayed for %s: HTTP %d\n", event.shortId,
                      event.httpStatus);
        break;
    }
  }
}

void setup() {
  Serial.begin(115200);
  auto cfg = M5.config();
  M5.begin(cfg);

  pinMode(PIN_WHITE, INPUT_PULLUP);
  pinMode(PIN_YELLOW, INPUT_PULLUP);
  statusUi.setState(UiState::Starting, 0);

  uploadQueue = xQueueCreate(UPLOAD_QUEUE_SIZE, sizeof(PendingCapture*));
  uploadEventQueue = xQueueCreate(12, sizeof(UploadEvent));
  captionQueue = xQueueCreate(8, sizeof(CaptionJob));
  if (uploadQueue == nullptr || uploadEventQueue == nullptr || captionQueue == nullptr) {
    statusUi.setState(UiState::Error, 0, nullptr,
                      "Could not create work queues");
    return;
  }

  uint64_t chipId = ESP.getEfuseMac();
  snprintf(deviceId, sizeof(deviceId), "cores3-%04llX%08llX",
           static_cast<unsigned long long>((chipId >> 32) & 0xFFFF),
           static_cast<unsigned long long>(chipId & 0xFFFFFFFF));

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 10000) {
    statusUi.update();
    delay(20);
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi connected: ");
    Serial.println(WiFi.localIP());
    configTime(0, 0, "pool.ntp.org");
  } else {
    Serial.println("WiFi connection failed");
  }

  if (!Camera.begin()) {
    Serial.println("Camera Init Fail");
    statusUi.setState(UiState::Error, 0, nullptr,
                      "Camera initialization failed");
    return;
  }

  Camera.sensor->set_framesize(Camera.sensor, FRAMESIZE_QVGA);
  xTaskCreatePinnedToCore(uploadWorkerTask, "capture-upload", 8192, nullptr, 1,
                          nullptr, 0);
  xTaskCreatePinnedToCore(captionStatusWorkerTask, "caption-status", 6144,
                          nullptr, 1, nullptr, 0);
  Serial.printf("Camera ready; daemon=%s device=%s\n", DAEMON_URL, deviceId);
  statusUi.setState(UiState::Idle, 0);
}

void loop() {
  M5.update();
  handleUploadEvents();
  statusUi.update();

  static bool lastTriggered = false;
  static bool hasCaptured = false;
  static unsigned long lastCaptureAt = 0;

  bool whiteLow = digitalRead(PIN_WHITE) == LOW;
  bool yellowLow = digitalRead(PIN_YELLOW) == LOW;
  bool triggered = whiteLow || yellowLow;
  unsigned long now = millis();
  bool newPress = triggered && !lastTriggered;
  bool debounceElapsed = !hasCaptured || now - lastCaptureAt >= DEBOUNCE_MS;

  if (newPress && debounceElapsed) {
    lastCaptureAt = now;
    hasCaptured = true;
    int nextCount = captureCount + 1;
    if (captureAndQueue(nextCount)) {
      captureCount = nextCount;
    }
  }

  if (confirmationVisible && millis() - confirmationStartedAt >= CONFIRMATION_MS) {
    statusUi.setState(UiState::Idle, captureCount);
    confirmationVisible = false;
  }

  lastTriggered = triggered;
  delay(10);
}
