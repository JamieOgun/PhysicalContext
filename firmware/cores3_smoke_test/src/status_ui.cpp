#include "status_ui.h"

#include <M5Unified.h>
#include <WiFi.h>

namespace {
constexpr unsigned long ANIMATION_FRAME_MS = 100;
constexpr int VIEWFINDER_TOP = 28;
constexpr int VIEWFINDER_BOTTOM = 180;

constexpr uint16_t rgb565(uint8_t red, uint8_t green, uint8_t blue) {
  return static_cast<uint16_t>(((red & 0xF8) << 8) |
                               ((green & 0xFC) << 3) | (blue >> 3));
}

constexpr uint16_t COLOR_BACKGROUND = rgb565(12, 17, 23);
constexpr uint16_t COLOR_OVERLAY = rgb565(7, 11, 15);
constexpr uint16_t COLOR_SURFACE = rgb565(25, 33, 42);
constexpr uint16_t COLOR_MUTED = rgb565(143, 156, 168);
constexpr uint16_t COLOR_CYAN = rgb565(48, 199, 211);
constexpr uint16_t COLOR_GREEN = rgb565(76, 206, 137);
constexpr uint16_t COLOR_AMBER = rgb565(245, 181, 63);
constexpr uint16_t COLOR_RED = rgb565(238, 91, 91);
}  // namespace

void StatusUi::drawCenteredText(const char* text, int y, int preferredSize,
                                uint16_t color, int maxWidth) {
  int size = preferredSize;
  M5.Display.setTextColor(color, COLOR_BACKGROUND);
  M5.Display.setTextSize(size);
  while (size > 1 && M5.Display.textWidth(text) > maxWidth) {
    M5.Display.setTextSize(--size);
  }
  M5.Display.setCursor((M5.Display.width() - M5.Display.textWidth(text)) / 2, y);
  M5.Display.print(text);
}

const char* StatusUi::title() const {
  switch (state_) {
    case UiState::Starting:
      return "Starting";
    case UiState::Idle:
      return "Ready";
    case UiState::Capturing:
      return "Capturing";
    case UiState::Queued:
      return "Queued";
    case UiState::Sending:
      return "Sending";
    case UiState::Retrying:
      return "Waiting for network";
    case UiState::Captioning:
      return "Captioning";
    case UiState::Complete:
    case UiState::CaptionUnavailable:
      return "Saved";
    case UiState::ProcessingDelayed:
      return "Still processing";
    case UiState::Error:
      return "Capture failed";
  }
  return "";
}

const char* StatusUi::detail() const {
  if (detail_[0] != '\0') {
    return detail_;
  }
  switch (state_) {
    case UiState::Starting:
      return "Connecting to Wi-Fi";
    case UiState::Idle:
      return "Press the button to capture";
    case UiState::Capturing:
      return "Hold steady";
    case UiState::Queued:
      return "Waiting to send";
    case UiState::Sending:
      return "Uploading to your Mac";
    case UiState::Retrying:
      return "Will retry automatically";
    case UiState::Captioning:
      return "Analyzing the image";
    case UiState::Complete:
      return "Caption ready";
    case UiState::CaptionUnavailable:
      return "Saved without a caption";
    case UiState::ProcessingDelayed:
      return "Finishing in the background";
    case UiState::Error:
      return "Try again";
  }
  return "";
}

bool StatusUi::isAnimated() const {
  return state_ == UiState::Starting || state_ == UiState::Capturing ||
         state_ == UiState::Queued || state_ == UiState::Sending ||
         state_ == UiState::Retrying || state_ == UiState::Captioning;
}

uint16_t StatusUi::accentColor() const {
  if (state_ == UiState::Complete || state_ == UiState::Idle) {
    return COLOR_GREEN;
  }
  if (state_ == UiState::Retrying || state_ == UiState::CaptionUnavailable ||
      state_ == UiState::ProcessingDelayed) {
    return COLOR_AMBER;
  }
  if (state_ == UiState::Error) {
    return COLOR_RED;
  }
  return COLOR_CYAN;
}

void StatusUi::drawFullScreenIndicator() {
  constexpr int8_t DOT_X[] = {0, 14, 20, 14, 0, -14, -20, -14};
  constexpr int8_t DOT_Y[] = {-20, -14, 0, 14, 20, 14, 0, -14};
  constexpr int CENTER_X = 160;
  constexpr int CENTER_Y = 88;

  M5.Display.fillRect(CENTER_X - 36, CENTER_Y - 36, 72, 72,
                      COLOR_BACKGROUND);
  uint16_t accent = accentColor();

  if (isAnimated()) {
    for (int i = 0; i < 8; ++i) {
      int distance = (i - animationFrame_ + 8) % 8;
      uint16_t color = distance == 0 ? accent :
                       distance <= 2 ? COLOR_MUTED : COLOR_SURFACE;
      int radius = distance == 0 ? 5 : 3;
      M5.Display.fillCircle(CENTER_X + DOT_X[i], CENTER_Y + DOT_Y[i], radius,
                            color);
    }
    return;
  }

  M5.Display.fillCircle(CENTER_X, CENTER_Y, 25, accent);
  if (state_ == UiState::Complete || state_ == UiState::Idle) {
    M5.Display.drawLine(148, 88, 157, 97, COLOR_BACKGROUND);
    M5.Display.drawLine(157, 97, 174, 78, COLOR_BACKGROUND);
    M5.Display.drawLine(148, 89, 157, 98, COLOR_BACKGROUND);
  } else {
    M5.Display.fillRect(158, 75, 4, 18, COLOR_BACKGROUND);
    M5.Display.fillCircle(160, 100, 2, COLOR_BACKGROUND);
  }
}

void StatusUi::drawReticle() {
  constexpr int LEFT = 132;
  constexpr int RIGHT = 188;
  constexpr int TOP = 88;
  constexpr int BOTTOM = 144;
  constexpr int CORNER = 11;

  M5.Display.drawFastHLine(LEFT, TOP, CORNER, TFT_WHITE);
  M5.Display.drawFastVLine(LEFT, TOP, CORNER, TFT_WHITE);
  M5.Display.drawFastHLine(RIGHT - CORNER, TOP, CORNER, TFT_WHITE);
  M5.Display.drawFastVLine(RIGHT, TOP, CORNER, TFT_WHITE);
  M5.Display.drawFastHLine(LEFT, BOTTOM, CORNER, TFT_WHITE);
  M5.Display.drawFastVLine(LEFT, BOTTOM - CORNER, CORNER, TFT_WHITE);
  M5.Display.drawFastHLine(RIGHT - CORNER, BOTTOM, CORNER, TFT_WHITE);
  M5.Display.drawFastVLine(RIGHT, BOTTOM - CORNER, CORNER, TFT_WHITE);
}

void StatusUi::drawOverlayIndicator() {
  constexpr int8_t DOT_X[] = {0, 6, 9, 6, 0, -6, -9, -6};
  constexpr int8_t DOT_Y[] = {-9, -6, 0, 6, 9, 6, 0, -6};
  constexpr int CENTER_X = 25;
  constexpr int CENTER_Y = 209;

  M5.Display.fillRect(8, 192, 34, 34, COLOR_OVERLAY);
  uint16_t accent = accentColor();
  if (isAnimated()) {
    for (int i = 0; i < 8; ++i) {
      int distance = (i - animationFrame_ + 8) % 8;
      uint16_t color = distance == 0 ? accent :
                       distance <= 2 ? COLOR_MUTED : COLOR_SURFACE;
      M5.Display.fillCircle(CENTER_X + DOT_X[i], CENTER_Y + DOT_Y[i],
                            distance == 0 ? 3 : 2, color);
    }
    return;
  }

  M5.Display.fillCircle(CENTER_X, CENTER_Y, 12, accent);
  if (state_ == UiState::Complete || state_ == UiState::Idle) {
    M5.Display.drawLine(19, 209, 23, 213, COLOR_OVERLAY);
    M5.Display.drawLine(23, 213, 31, 204, COLOR_OVERLAY);
  } else {
    M5.Display.fillRect(24, 202, 2, 9, COLOR_OVERLAY);
    M5.Display.fillCircle(25, 215, 1, COLOR_OVERLAY);
  }
}

void StatusUi::drawFullScreen() {
  M5.Display.fillScreen(COLOR_BACKGROUND);
  M5.Display.setTextWrap(false);

  M5.Display.setTextSize(1);
  M5.Display.setTextColor(COLOR_MUTED, COLOR_BACKGROUND);
  M5.Display.setCursor(14, 14);
  M5.Display.print("PHYSICAL CONTEXT");

  bool connected = WiFi.status() == WL_CONNECTED;
  const char* connectionLabel = connected ? "ONLINE" : "OFFLINE";
  M5.Display.fillCircle(255, 18, 4, connected ? COLOR_GREEN : COLOR_AMBER);
  M5.Display.setCursor(265, 14);
  M5.Display.print(connectionLabel);

  drawFullScreenIndicator();
  drawCenteredText(title(), 132, 3, TFT_WHITE);
  drawCenteredText(detail(), 171, 1, COLOR_MUTED);

  if (shortId_[0] != '\0') {
    char captureLabel[24];
    snprintf(captureLabel, sizeof(captureLabel), "Capture %s", shortId_);
    drawCenteredText(captureLabel, 190, 1, accentColor());
  }

  M5.Display.drawFastHLine(14, 216, M5.Display.width() - 28, COLOR_SURFACE);
  char countLabel[24];
  snprintf(countLabel, sizeof(countLabel), "%d captured", count_);
  drawCenteredText(countLabel, 224, 1, COLOR_MUTED);
}

void StatusUi::drawOverlay() {
  M5.Display.setTextWrap(false);
  M5.Display.fillRect(0, 0, M5.Display.width(), VIEWFINDER_TOP, COLOR_OVERLAY);
  M5.Display.fillRect(0, VIEWFINDER_BOTTOM, M5.Display.width(),
                      M5.Display.height() - VIEWFINDER_BOTTOM, COLOR_OVERLAY);
  M5.Display.fillRect(0, VIEWFINDER_BOTTOM, 4,
                      M5.Display.height() - VIEWFINDER_BOTTOM, accentColor());

  M5.Display.setTextSize(1);
  M5.Display.setTextColor(TFT_WHITE, COLOR_OVERLAY);
  M5.Display.setCursor(10, 10);
  M5.Display.print("PHYSICAL CONTEXT");

  char countLabel[16];
  snprintf(countLabel, sizeof(countLabel), "%d SHOTS", count_);
  M5.Display.setTextColor(COLOR_MUTED, COLOR_OVERLAY);
  M5.Display.setCursor(181, 10);
  M5.Display.print(countLabel);

  bool connected = WiFi.status() == WL_CONNECTED;
  M5.Display.fillCircle(269, 13, 4, connected ? COLOR_GREEN : COLOR_AMBER);
  M5.Display.setCursor(279, 10);
  M5.Display.print(connected ? "ON" : "OFF");

  if (state_ == UiState::Idle || state_ == UiState::Capturing) {
    drawReticle();
  }

  drawOverlayIndicator();
  M5.Display.setTextSize(2);
  M5.Display.setTextColor(TFT_WHITE, COLOR_OVERLAY);
  M5.Display.setCursor(48, 188);
  M5.Display.print(title());

  M5.Display.setTextSize(1);
  M5.Display.setTextColor(COLOR_MUTED, COLOR_OVERLAY);
  M5.Display.setCursor(48, 216);
  M5.Display.print(detail());

  if (shortId_[0] != '\0') {
    M5.Display.setTextColor(accentColor(), COLOR_OVERLAY);
    M5.Display.setCursor(256, 190);
    M5.Display.print(shortId_);
  }
}

void StatusUi::draw() {
  if (viewfinderMode_) {
    drawOverlay();
  } else {
    drawFullScreen();
  }
}

void StatusUi::setViewfinderMode(bool enabled) {
  viewfinderMode_ = enabled;
  draw();
}

void StatusUi::drawCameraFrame(const uint16_t* pixels, int width, int height) {
  if (!viewfinderMode_ || pixels == nullptr || width <= 0 ||
      height <= VIEWFINDER_TOP) {
    return;
  }

  int contentBottom = height < VIEWFINDER_BOTTOM ? height : VIEWFINDER_BOTTOM;
  int contentHeight = contentBottom - VIEWFINDER_TOP;
  M5.Display.pushImage(0, VIEWFINDER_TOP, width, contentHeight,
                       pixels + width * VIEWFINDER_TOP);
  if (state_ == UiState::Idle || state_ == UiState::Capturing) {
    drawReticle();
  }
}

void StatusUi::setState(UiState state, int count, const char* shortId,
                        const char* detail) {
  state_ = state;
  count_ = count;
  snprintf(shortId_, sizeof(shortId_), "%s", shortId == nullptr ? "" : shortId);
  snprintf(detail_, sizeof(detail_), "%s", detail == nullptr ? "" : detail);
  animationFrame_ = 0;
  lastAnimationAt_ = millis();
  draw();
}

void StatusUi::update() {
  if (!isAnimated() || millis() - lastAnimationAt_ < ANIMATION_FRAME_MS) {
    return;
  }
  lastAnimationAt_ = millis();
  animationFrame_ = (animationFrame_ + 1) % 8;
  if (viewfinderMode_) {
    drawOverlayIndicator();
  } else {
    drawFullScreenIndicator();
  }
}
