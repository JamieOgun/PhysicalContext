#include <M5Unified.h>
#include <esp_camera.h>
#include <WiFi.h>
#include "secrets.h"

constexpr int PIN_WHITE = 1;   // CoreS3-Lite Port A white
constexpr int PIN_YELLOW = 2;  // CoreS3-Lite Port A yellow

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

GC0308 Camera;

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
  M5.Display.println("Triggered Camera");
  M5.Display.println();
  M5.Display.println(message);
  M5.Display.print("count: ");
  M5.Display.println(count);
}

void drawCaptureOverlay(int count) {
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(0, 0);
  M5.Display.print("count: ");
  M5.Display.println(count);
}

void captureAndDisplay(int count) {
  if (!Camera.get()) {
    drawStatus("capture failed", count);
    return;
  }

  M5.Display.pushImage(0, 0, M5.Display.width(), M5.Display.height(),
                       reinterpret_cast<uint16_t*>(Camera.fb->buf));
  Camera.free();
  drawCaptureOverlay(count);
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

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - start < 10000) {
    delay(250);
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi connected: ");
    Serial.println(WiFi.localIP());

    drawStatus("WiFi connected", 0);
    delay(2000);
  } else {
    Serial.println("WiFi connection failed");

    drawStatus("WiFi failed", 0);
    delay(2000);
  }

  if (!Camera.begin()) {
    Serial.println("Camera Init Fail");
    drawStatus("camera init failed", 0);
    return;
  }

  Serial.println("Camera Init Success");
  Camera.sensor->set_framesize(Camera.sensor, FRAMESIZE_QVGA);
  drawStatus("press ATOM", 0);
}


void loop() {
  M5.update();

  static int count = 0;
  static bool lastTriggered = false;

  bool whiteLow = digitalRead(PIN_WHITE) == LOW;
  bool yellowLow = digitalRead(PIN_YELLOW) == LOW;
  bool triggered = whiteLow || yellowLow;

  if (triggered && !lastTriggered) {
    count++;
    captureAndDisplay(count);
  }

  lastTriggered = triggered;
  delay(10);
}
