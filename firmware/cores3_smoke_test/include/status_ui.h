#pragma once

#include <Arduino.h>

enum class UiState : uint8_t {
  Starting,
  Idle,
  Capturing,
  Queued,
  Sending,
  Retrying,
  Captioning,
  Complete,
  CaptionUnavailable,
  ProcessingDelayed,
  Error,
};

class StatusUi {
public:
  void setViewfinderMode(bool enabled);
  void drawCameraFrame(const uint16_t* pixels, int width, int height);
  void setState(UiState state, int count, const char* shortId = nullptr,
                const char* detail = nullptr);
  void update();

private:
  void draw();
  void drawFullScreen();
  void drawOverlay();
  void drawFullScreenIndicator();
  void drawOverlayIndicator();
  void drawReticle();
  void drawCenteredText(const char* text, int y, int preferredSize,
                        uint16_t color, int maxWidth = 296);
  const char* title() const;
  const char* detail() const;
  bool isAnimated() const;
  uint16_t accentColor() const;

  UiState state_ = UiState::Starting;
  int count_ = 0;
  char shortId_[9] = {};
  char detail_[48] = {};
  uint8_t animationFrame_ = 0;
  unsigned long lastAnimationAt_ = 0;
  bool viewfinderMode_ = false;
};
