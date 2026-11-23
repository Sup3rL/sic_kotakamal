/*
 * Kotak Amal Cerdas - IR sensor test (FC-51), for calibrating the sensitivity
 *
 * Board: "ESP32 Dev Module". No libraries needed.
 * Wiring: FC-51 VCC -> 3V3, GND -> GND, OUT -> GPIO 17
 *
 * Every time something passes the beam it prints one line with how long it blocked the beam,
 * so even a fast coin shows up. Serial Monitor 115200.
 *   r = reset the counter      s = show the current state
 *
 * How to calibrate:
 *   1. Empty funnel, nothing moving: the green "obstacle" LED on the FC-51 must be OFF and
 *      the Serial Monitor quiet. If it says BLOCKED or keeps counting, turn the blue potentiometer
 *      a little anti-clockwise (less sensitive) until it stays quiet.
 *   2. Drop 20 coins of Rp100 (the smallest) one by one, then type s.
 *      Count must be 20. If it is lower, turn the potentiometer a little clockwise (more sensitive)
 *      or move the sensor closer to the narrowest part of the funnel.
 *   3. Repeat 1 and 2 until both pass. Then also try a folded note and a handful of coins.
 */

const int PIN_IR = 17;

volatile unsigned long passes = 0;
volatile unsigned long blockedAt = 0;
volatile unsigned long lastMs = 0;   // duration of the last pass, in ms
volatile bool newPass = false;

void IRAM_ATTR onChange() {
  unsigned long now = millis();
  if (digitalRead(PIN_IR) == LOW) {           // beam blocked: something is passing
    blockedAt = now;
  } else if (blockedAt) {                     // beam clear again: the object has passed
    lastMs = now - blockedAt;
    blockedAt = 0;
    passes++;
    newPass = true;
  }
}

void setup() {
  Serial.begin(115200);
  delay(300);
  pinMode(PIN_IR, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_IR), onChange, CHANGE);
  Serial.println("\nIR test ready (GPIO 17). Drop coins through the funnel. r = reset, s = state");
  Serial.printf("Now: %s\n", digitalRead(PIN_IR) == LOW ? "BLOCKED  <- too sensitive or something in the way" : "clear (good)");
}

void loop() {
  if (newPass) {
    newPass = false;
    Serial.printf("[IR] pass #%lu, beam blocked for %lu ms\n", passes, lastMs);
  }

  // Warn if the beam stays blocked: too sensitive, or seeing the funnel wall / outside light
  static unsigned long warned = 0;
  if (blockedAt && millis() - blockedAt > 2000 && millis() - warned > 2000) {
    warned = millis();
    Serial.println("[IR] BLOCKED for more than 2 s: turn the potentiometer anti-clockwise (less sensitive)");
  }

  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'r') { passes = 0; Serial.println("[IR] counter reset to 0"); }
    if (c == 's') Serial.printf("[IR] now %s, passes counted: %lu\n",
                                digitalRead(PIN_IR) == LOW ? "BLOCKED" : "clear", passes);
  }
  delay(5);
}
