#include <M5Unified.h>

constexpr int PIN_WHITE = 1;  // CoreS3-Lite Port A white
constexpr int PIN_YELLOW = 2; // CoreS3-Lite Port A yellow

void drawCount(int count) {
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setCursor(0, 0);
  M5.Display.println("ATOM Trigger");
  M5.Display.println();
  M5.Display.print("count: ");
  M5.Display.println(count);
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);

  pinMode(PIN_WHITE, INPUT_PULLUP);
  pinMode(PIN_YELLOW, INPUT_PULLUP);

  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(2);
  drawCount(0);
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
    drawCount(count);
  }

  lastTriggered = triggered;
  delay(10);
}
