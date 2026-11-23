/*
 * Kotak Amal Cerdas - BASIC controller (ESP32 DevKit V1), no Wi-Fi / MQTT / AI yet
 *
 * Flow for every donation:
 *   1. IR sensor sees money falling in
 *   2. relay turns the LED ON
 *   3. wait until nothing more falls in (QUIET_MS) and coins stop rolling (SETTLE_MS)
 *   4. pulse the trigger wire -> ESP32-CAM takes the photo
 *   5. wait CAPTURE_MS so the camera has grabbed the picture
 *   6. servo tilts the tray (money slides into the vault), then goes back level
 *   7. relay turns the LED OFF
 *   8. VL53L0X measures how full the vault is
 * The MPU6050 is read the whole time (50 Hz) and summarised once per second. Type "f" to print
 * those rows as "[FEAT] {...}"; ai/record_serial.py uses them to record the Stage 3 dataset.
 *
 * Board: "ESP32 Dev Module" (or "DOIT ESP32 DEVKIT V1"). NOT "AI Thinker ESP32-CAM".
 * Libraries (Library Manager): ESP32Servo, VL53L0X (by Pololu). Wire is built in.
 *
 * Wiring:
 *   IR FC-51 OUT          -> GPIO 17   (VCC 3V3, GND)
 *   Relay module IN       -> GPIO 26   (relay VCC 5V, GND; LED power goes through COM / NO)
 *   Trigger to ESP32-CAM  -> GPIO 27 -> IO13 on the camera (plus a shared GND)
 *   Servo MG996R signal   -> GPIO 25   (servo power from the 5V 3A rail, NOT from the ESP32)
 *   MPU6050 + VL53L0X     -> SDA 21 / SCL 22 (3V3, GND)
 *
 * Serial Monitor 115200. Commands:
 *   s = self-check     c = run one full cycle now (as if money fell in)
 *   p = photo only     d = tilt the tray once        l = relay/LED on/off
 *   i = IR state       v = VL53 live on/off          m = MPU live on/off
 *   f = [FEAT] rows for record_serial.py              a number (e.g. 150) = move the servo there
 */

#include <Wire.h>
#include <ESP32Servo.h>
#include <VL53L0X.h>

// Donation state type must be declared before Arduino generates function prototypes.
enum State { IDLE, COLLECTING, CAPTURING, DUMPING, RETURNING };

// ---------- Pins ----------
const int PIN_IR      = 18;
const int PIN_RELAY   = 26;
const int PIN_TRIGGER = 27;
const int PIN_SERVO   = 25;

// Most cheap relay modules switch ON when IN is LOW ("active low"). If your LED is ON when it
// should be OFF (and the other way round), change this to false.
const bool RELAY_ACTIVE_LOW = true;

// ---------- Tuning ----------
const int TRAY_FLAT = 150;                  // calibrated on Uno's servo: tray level
const int TRAY_DUMP = 90;                   // calibrated: tray tipped (this servo turns the other way)
const unsigned long QUIET_MS       = 1500;  // nothing fell past the IR for this long = donor is done
const unsigned long SETTLE_MS      = 400;   // extra time for a rolling coin to stop
const unsigned long MAX_COLLECT_MS = 8000;  // never wait longer than this before taking the photo
const unsigned long CAPTURE_MS     = 1000;  // time the camera needs after the trigger to grab the picture
const unsigned long DUMP_HOLD_MS   = 1000;  // how long the tray stays tipped
const unsigned long RETURN_MS      = 700;   // time for the tray to get back level before the LED goes off
const int VAULT_EMPTY_MM = 175;             // calibrated: VL53L0X reading with an empty vault
const int VAULT_FULL_MM  = 40;              // reading when the vault is full

Servo tray;
VL53L0X tof;
bool hasTof = false, hasMpu = false;
bool vlLive = false, mpuLive = false, featSerial = false;
int fullnessPct = -1;
unsigned long servoBusyUntil = 0;

// ---------- IR (interrupt, so a fast coin is never missed) ----------
volatile bool irHit = false;
volatile unsigned long irCount = 0;
void IRAM_ATTR onIr() { irHit = true; irCount++; }
bool takeIrHit() {
  noInterrupts(); bool h = irHit; irHit = false; interrupts();
  return h || digitalRead(PIN_IR) == LOW;
}

// ---------- Relay ----------
bool relayOn = false;
void setRelay(bool on) {
  relayOn = on;
  digitalWrite(PIN_RELAY, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
  Serial.printf("[RELAY]  LED %s\n", on ? "ON" : "OFF");
}

// ---------- Camera trigger (one-way wire: the ESP32 cannot see whether the photo worked) ----------
void triggerCamera() {
  digitalWrite(PIN_TRIGGER, HIGH); delay(50); digitalWrite(PIN_TRIGGER, LOW);
  Serial.println("[CAMERA] trigger sent on GPIO 27 -> the camera takes a photo now "
                 "(check the photo_receiver.py window on the laptop)");
}

// ---------- I2C helpers ----------
bool i2cPresent(uint8_t addr) { Wire.beginTransmission(addr); return Wire.endTransmission() == 0; }

// ---------- VL53L0X ----------
int readVaultMm() {
  if (!hasTof) return -1;
  int mm = tof.readRangeSingleMillimeters();
  if (tof.timeoutOccurred() || mm >= 2000) return -1;
  return mm;
}
void reportVault() {
  int mm = readVaultMm();
  if (mm < 0) { Serial.println("[VL53]   no reading"); return; }
  fullnessPct = constrain(map(mm, VAULT_EMPTY_MM, VAULT_FULL_MM, 0, 100), 0, 100);
  Serial.printf("[VL53]   %d mm -> vault %d%% full%s\n", mm, fullnessPct, fullnessPct >= 90 ? "  <- WARNING: empty the vault" : "");
}

// ---------- MPU6050 (raw registers, no library needed) ----------
const uint8_t MPU = 0x68;
bool mpuBegin() {
  Wire.beginTransmission(MPU); Wire.write(0x6B); Wire.write(0);   // wake up
  if (Wire.endTransmission() != 0) return false;
  Wire.beginTransmission(MPU); Wire.write(0x1C); Wire.write(0x08); Wire.endTransmission();  // accel +-4 g
  Wire.beginTransmission(MPU); Wire.write(0x1B); Wire.write(0x08); Wire.endTransmission();  // gyro +-500 deg/s
  return true;
}
bool mpuRead(float& ax, float& ay, float& az, float& gx, float& gy, float& gz) {
  Wire.beginTransmission(MPU); Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU, (uint8_t)14) != 14) return false;
  int16_t r[7];
  for (int i = 0; i < 7; i++) r[i] = (Wire.read() << 8) | Wire.read();
  ax = r[0] / 8192.0; ay = r[1] / 8192.0; az = r[2] / 8192.0;   // g
  gx = r[4] / 65.5;   gy = r[5] / 65.5;   gz = r[6] / 65.5;     // deg/s
  return true;
}

// 50 Hz samples summarised into one row per second (same 10 features as server/config.py)
struct Window {
  int n = 0;
  float sum[3] = {0, 0, 0}, sq[3] = {0, 0, 0};
  float accMax = 0, accMin = 99, gyroMax = 0, gyroSum = 0;
} win;
unsigned long lastSample = 0, lastWindow = 0;

void sampleMotion() {
  if (!hasMpu || millis() - lastSample < 20) return;
  lastSample = millis();
  float ax, ay, az, gx, gy, gz;
  if (!mpuRead(ax, ay, az, gx, gy, gz)) return;
  float a[3] = {ax, ay, az};
  for (int i = 0; i < 3; i++) { win.sum[i] += a[i]; win.sq[i] += a[i] * a[i]; }
  float mag = sqrtf(ax * ax + ay * ay + az * az);
  float g = sqrtf(gx * gx + gy * gy + gz * gz);
  win.accMax = max(win.accMax, mag); win.accMin = min(win.accMin, mag);
  win.gyroMax = max(win.gyroMax, g); win.gyroSum += g;
  win.n++;
}

void motionWindow() {
  if (!hasMpu || millis() - lastWindow < 1000 || win.n < 10) return;
  lastWindow = millis();
  const char* ax[3] = {"x", "y", "z"};
  float mean[3], sd[3];
  for (int i = 0; i < 3; i++) {
    mean[i] = win.sum[i] / win.n;
    sd[i] = sqrtf(max(0.0f, win.sq[i] / win.n - mean[i] * mean[i]));
  }
  bool servo = millis() < servoBusyUntil;   // our own servo shook the box: not real data
  if (featSerial) {
    Serial.printf("[FEAT] {\"mean_x\":%.5f,\"mean_y\":%.5f,\"mean_z\":%.5f,\"std_x\":%.5f,\"std_y\":%.5f,\"std_z\":%.5f,"
                  "\"acc_max\":%.5f,\"acc_min\":%.5f,\"gyro_max\":%.3f,\"gyro_mean\":%.3f,\"servo\":%s}\n",
                  mean[0], mean[1], mean[2], sd[0], sd[1], sd[2], win.accMax, win.accMin,
                  win.gyroMax, win.gyroSum / win.n, servo ? "true" : "false");
  }
  if (mpuLive) {
    float len = sqrtf(mean[0] * mean[0] + mean[1] * mean[1] + mean[2] * mean[2]);
    float tilt = acosf(constrain(mean[2] / max(len, 0.01f), -1.0f, 1.0f)) * 57.3f;
    Serial.printf("[MPU]    tilt %.0f deg, shake (acc max) %.2f g, rotation max %.0f deg/s%s\n",
                  tilt, win.accMax, win.gyroMax, servo ? " (servo moving)" : "");
  }
  win = Window();
}

// ---------- Donation flow ----------
State state = IDLE;
unsigned long stateAt = 0, lastActivity = 0;
void setState(State s) { state = s; stateAt = millis(); }

void startCycle(const char* why) {
  Serial.printf("[IR]     %s (IR passes so far: %lu)\n", why, irCount);
  setRelay(true);                          // step 2: LED on
  lastActivity = millis();
  setState(COLLECTING);
}

void donationFlow() {
  unsigned long dt = millis() - stateAt;
  switch (state) {
    case IDLE:
      if (takeIrHit() && dt > 500) startCycle("money detected");
      break;

    case COLLECTING:                        // step 3: wait until the donor is done
      if (takeIrHit()) lastActivity = millis();
      if (millis() - lastActivity >= QUIET_MS + SETTLE_MS || dt >= MAX_COLLECT_MS) {
        triggerCamera();                    // step 4: photo
        setState(CAPTURING);
      }
      break;

    case CAPTURING:                         // step 5: give the camera time to grab the picture
      if (dt >= CAPTURE_MS) {
        Serial.printf("[SERVO]  tilting tray to %d deg\n", TRAY_DUMP);
        servoBusyUntil = millis() + DUMP_HOLD_MS + RETURN_MS + 800;
        tray.write(TRAY_DUMP);              // step 6: servo tilts the tray
        setState(DUMPING);
      }
      break;

    case DUMPING:
      if (dt >= DUMP_HOLD_MS) {
        tray.write(TRAY_FLAT);
        Serial.printf("[SERVO]  tray back to %d deg (level)\n", TRAY_FLAT);
        setState(RETURNING);
      }
      break;

    case RETURNING:
      if (dt >= RETURN_MS) {
        setRelay(false);                    // step 7: LED off
        reportVault();                      // step 8: how full is the vault
        takeIrHit();                        // ignore anything the tilt itself caused
        Serial.println("---- ready for the next donation ----");
        setState(IDLE);
      }
      break;
  }
}

// ---------- Self-check ----------
void selfCheck(bool atBoot) {
  Serial.println("\n========== SELF-CHECK ==========");
  hasMpu = i2cPresent(MPU) ? (atBoot ? mpuBegin() : true) : false;
  Serial.printf("[MPU]    %s\n", hasMpu ? "OK (I2C 0x68)" : "MISSING: check SDA 21 / SCL 22 / 3V3 / GND");
  if (!i2cPresent(0x29)) {
    hasTof = false;
    Serial.println("[VL53]   MISSING: nothing at I2C 0x29. Check SDA 21 / SCL 22 / VIN / GND");
  } else {
    if (atBoot || !hasTof) { tof.setTimeout(100); hasTof = tof.init(); }
    if (!hasTof) Serial.println("[VL53]   ERROR: found at 0x29 but init failed. Power it off and on");
    else reportVault();
  }
  bool blocked = digitalRead(PIN_IR) == LOW;
  Serial.printf("[IR]     pin %d reads %s, passes counted: %lu%s\n", PIN_IR, blocked ? "BLOCKED" : "clear", irCount,
                blocked ? "  <- WARNING: blocked with nothing in the funnel? turn the IR potentiometer" : "");
  Serial.printf("[RELAY]  pin %d, LED is %s (active %s). Type l to test\n", PIN_RELAY, relayOn ? "ON" : "OFF",
                RELAY_ACTIVE_LOW ? "LOW" : "HIGH");
  Serial.printf("[SERVO]  pin %d, level = %d deg, tilt = %d deg. Type d to test\n", PIN_SERVO, TRAY_FLAT, TRAY_DUMP);
  Serial.printf("[CAMERA] trigger pin %d -> CAM IO13. Type p, then look at the camera's Serial Monitor\n", PIN_TRIGGER);
  Serial.println("Commands: s c p d l i v m f, or a number = servo angle");
  Serial.println("================================");
}

// ---------- Serial commands ----------
void serialCommands() {
  if (!Serial.available()) return;
  char c = Serial.peek();
  if (isDigit(c)) {                         // e.g. 150 -> move servo, to find TRAY_FLAT / TRAY_DUMP
    int a = constrain((int)Serial.parseInt(), 0, 180);
    tray.write(a);
    Serial.printf("[SERVO]  -> %d deg\n", a);
    return;
  }
  c = Serial.read();
  bool idle = state == IDLE;
  if (c == 's') selfCheck(false);
  if (c == 'c' && idle) startCycle("manual cycle (c)");
  if (c == 'p') triggerCamera();
  if (c == 'd' && idle) {                   // tilt the tray once (LED goes off afterwards)
    Serial.printf("[SERVO]  tilting tray to %d deg\n", TRAY_DUMP);
    servoBusyUntil = millis() + DUMP_HOLD_MS + RETURN_MS + 800;
    tray.write(TRAY_DUMP);
    setState(DUMPING);
  }
  if (c == 'l' && idle) setRelay(!relayOn);
  if (c == 'i') Serial.printf("[IR]     now %s, passes counted: %lu\n", digitalRead(PIN_IR) == LOW ? "BLOCKED" : "clear", irCount);
  if (c == 'v') { vlLive = !vlLive; Serial.printf("[VL53]   live %s\n", vlLive ? "ON" : "OFF"); }
  if (c == 'm') { mpuLive = !mpuLive; Serial.printf("[MPU]    live %s\n", mpuLive ? "ON" : "OFF"); }
  if (c == 'f') { featSerial = !featSerial; Serial.printf("[FEAT] %s\n", featSerial ? "ON" : "OFF"); }
}

void vaultLive() {
  static unsigned long last = 0;
  if (!vlLive || state != IDLE || millis() - last < 500) return;
  last = millis();
  int mm = readVaultMm();
  Serial.printf("[VL53]   live: %s\n", mm < 0 ? "no reading" : (String(mm) + " mm").c_str());
}

void setup() {
  Serial.begin(115200);
  delay(300);
  // Set the relay to OFF before making the pin an output, so the LED doesn't flash at boot
  digitalWrite(PIN_RELAY, RELAY_ACTIVE_LOW ? HIGH : LOW);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_TRIGGER, OUTPUT); digitalWrite(PIN_TRIGGER, LOW);
  pinMode(PIN_IR, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_IR), onIr, FALLING);

  Wire.begin(21, 22);
  tray.setPeriodHertz(50);
  tray.attach(PIN_SERVO, 500, 2400);
  tray.write(TRAY_FLAT);

  Serial.println("\nKotak Amal Cerdas - basic controller (IR -> LED -> photo -> servo -> LED off)");
  selfCheck(true);
  takeIrHit();                              // forget anything seen while booting
  stateAt = millis();
}

void loop() {
  donationFlow();
  serialCommands();
  sampleMotion();
  motionWindow();
  vaultLive();
}
