#include <Arduino.h>

constexpr int ATOM_BUTTON_PIN = 39;
constexpr int TRIGGER_YELLOW = 26;
constexpr int TRIGGER_WHITE = 32;

void setup() {
  pinMode(ATOM_BUTTON_PIN, INPUT);
  pinMode(TRIGGER_YELLOW, OUTPUT);
  pinMode(TRIGGER_WHITE, OUTPUT);

  digitalWrite(TRIGGER_YELLOW, HIGH);
  digitalWrite(TRIGGER_WHITE, HIGH);
}

void loop() {
  bool pressed = digitalRead(ATOM_BUTTON_PIN) == LOW;

  digitalWrite(TRIGGER_YELLOW, pressed ? LOW : HIGH);
  digitalWrite(TRIGGER_WHITE, pressed ? LOW : HIGH);

  delay(10);
}
