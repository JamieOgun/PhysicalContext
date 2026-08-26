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

constexpr int PIN_WHITE = 1;
constexpr int PIN_YELLOW = 2;
constexpr unsigned long DEBOUNCE_MS = 800;
constexpr unsigned long CONFIRMATION_MS = 3000;
constexpr unsigned long RETRY_DELAY_MS = 2000;
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

enum class UploadEventType : uint8_t { Uploading, Retrying, Succeeded };

struct PendingCapture {
  uint8_t* jpegBuffer = nullptr;
  size_t jpegLength = 0;
  char clientCaptureId[37] = {};
  uint64_t deviceTimestamp = 0;
  int count = 0;
};

struct UploadEvent {
  UploadEventType type = UploadEventType::Uploading;
  int count = 0;
  int httpStatus = 0;
  char shortId[9] = {};
  float sharpness = 0;
  float brightness = 0;
  bool hasBlurryClassification = false;
  bool isBlurry = false;
};

GC0308 Camera;
QueueHandle_t uploadQueue = nullptr;
QueueHandle_t uploadEventQueue = nullptr;
char deviceId[24] = {};
int captureCount = 0;
bool confirmationVisible = false;
unsigned long confirmationStartedAt = 0;

bool GC0308::begin() {
  config = &camera_config;
  M5.In_I2C.release();
  esp_err_t err = esp_camera_init(config);
  if (err != ESP_OK) {
    return false;
  }
  sensor = esp_camera_sensor_get();
  return sensor != nullptr;
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

void drawStatus(const char* message, int count) {
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setCursor(0, 0);
  M5.Display.println("Physical Context");
  M5.Display.println();
  M5.Display.println(message);
  M5.Display.print("count: ");
  M5.Display.println(count);
}

void drawCaptureOverlay(const char* message, int count) {
  M5.Display.fillRect(0, 0, M5.Display.width(), 54, TFT_BLACK);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(0, 0);
  M5.Display.println(message);
  M5.Display.print("count: ");
  M5.Display.println(count);
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
  const char* shortId = document["short_id"] | "";
  if (error || shortId[0] == '\0') {
    return false;
  }

  snprintf(event.shortId, sizeof(event.shortId), "%.8s", shortId);
  event.sharpness = document["sharpness"] | 0.0f;
  event.brightness = document["brightness"] | 0.0f;
  event.hasBlurryClassification = document["is_blurry"].is<bool>();
  event.isBlurry = document["is_blurry"] | false;
  return true;
}

void sendUploadEvent(const UploadEvent& event) {
  xQueueSend(uploadEventQueue, &event, pdMS_TO_TICKS(100));
}

void uploadWorkerTask(void*) {
  while (true) {
    PendingCapture* capture = nullptr;
    if (xQueueReceive(uploadQueue, &capture, portMAX_DELAY) != pdTRUE || capture == nullptr) {
      continue;
    }

    UploadEvent event{};
    event.type = UploadEventType::Uploading;
    event.count = capture->count;
    sendUploadEvent(event);

    while (true) {
      UploadEvent result{};
      result.count = capture->count;
      if (WiFi.status() == WL_CONNECTED && uploadCapture(*capture, result)) {
        result.type = UploadEventType::Succeeded;
        sendUploadEvent(result);
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

bool captureAndQueue(int count) {
  drawStatus("CAPTURING", count);
  if (!Camera.get()) {
    drawStatus("capture failed", count);
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
    drawStatus("JPEG failed", count);
    return false;
  }

  PendingCapture* capture = new (std::nothrow) PendingCapture{};
  if (capture == nullptr) {
    free(jpegBuffer);
    drawStatus("memory error", count);
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
    drawStatus("QUEUE FULL", count);
    return false;
  }

  Serial.printf("Queued JPEG: %u bytes id=%s\n",
                static_cast<unsigned>(jpegLength), capture->clientCaptureId);
  drawCaptureOverlay("QUEUED", count);
  return true;
}

void handleUploadEvents() {
  UploadEvent event{};
  while (xQueueReceive(uploadEventQueue, &event, 0) == pdTRUE) {
    switch (event.type) {
      case UploadEventType::Uploading:
        drawCaptureOverlay("UPLOADING", event.count);
        break;
      case UploadEventType::Retrying:
        drawStatus("QUEUED retry", event.count);
        confirmationVisible = false;
        Serial.printf("Upload failed: HTTP %d; retrying\n", event.httpStatus);
        break;
      case UploadEventType::Succeeded: {
        char message[32];
        if (event.hasBlurryClassification && event.isBlurry) {
          snprintf(message, sizeof(message), "OK %s blurry", event.shortId);
        } else {
          snprintf(message, sizeof(message), "OK %s", event.shortId);
        }
        drawStatus(message, event.count);
        confirmationStartedAt = millis();
        confirmationVisible = true;
        Serial.printf("Upload OK %s sharpness=%.2f brightness=%.2f\n",
                      event.shortId, event.sharpness, event.brightness);
        break;
      }
    }
  }
}

void setup() {
  Serial.begin(115200);
  auto cfg = M5.config();
  M5.begin(cfg);

  pinMode(PIN_WHITE, INPUT_PULLUP);
  pinMode(PIN_YELLOW, INPUT_PULLUP);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(2);
  drawStatus("connecting WiFi", 0);

  uploadQueue = xQueueCreate(UPLOAD_QUEUE_SIZE, sizeof(PendingCapture*));
  uploadEventQueue = xQueueCreate(8, sizeof(UploadEvent));
  if (uploadQueue == nullptr || uploadEventQueue == nullptr) {
    drawStatus("queue init failed", 0);
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
    delay(250);
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi connected: ");
    Serial.println(WiFi.localIP());
    configTime(0, 0, "pool.ntp.org");
    drawStatus("WiFi connected", 0);
    delay(2000);
  } else {
    Serial.println("WiFi connection failed");
    drawStatus("WiFi offline", 0);
    delay(2000);
  }

  if (!Camera.begin()) {
    Serial.println("Camera Init Fail");
    drawStatus("camera init failed", 0);
    return;
  }

  Camera.sensor->set_framesize(Camera.sensor, FRAMESIZE_QVGA);
  xTaskCreatePinnedToCore(uploadWorkerTask, "capture-upload", 8192, nullptr, 1,
                          nullptr, 0);
  Serial.printf("Camera ready; daemon=%s device=%s\n", DAEMON_URL, deviceId);
  drawStatus("IDLE", 0);
}

void loop() {
  M5.update();
  handleUploadEvents();

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
    drawStatus("IDLE", captureCount);
    confirmationVisible = false;
  }

  lastTriggered = triggered;
  delay(10);
}
