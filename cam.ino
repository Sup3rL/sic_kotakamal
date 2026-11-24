/*
 * Kotak Amal Cerdas - ESP32-CAM (AI-Thinker) capture node
 *
 * The box has TWO of these: "top" (straight down at the landing tray) and "side"
 * (at about 45 degrees). Flash this same sketch on both, only CAM_NAME changes.
 * A folded note or a coin that is hard to read from one angle is often clear from the other.
 *
 * Job: when the main ESP32 pulls the shared TRIGGER line HIGH, take one JPEG of the
 * landing tray and POST it to the AI server: POST /capture?cam=<CAM_NAME>
 * Collect mode vs normal mode is chosen on the server (COLLECT=1), not here.
 *
 * Board: "AI Thinker ESP32-CAM", PSRAM enabled.
 * Flash with the ESP32-CAM-MB board. If upload fails, hold IO0 (the MB's
 * "IO0" button), tap RST, then upload.
 */

#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>

// ---------- Settings: change these ----------
const char* WIFI_SSID = "yey";
const char* WIFI_PASS = "12121212";
// Laptop running server/photo_receiver.py (photos only, for now) or server.py (with AI).
// Both listen on port 8000. Find the laptop IP with `ipconfig` (Windows, "IPv4 Address").
const char* SERVER    = "http://172.22.9.175:8000";
const char* CAM_NAME  = "top";     // "top" on the camera above the tray, "side" on the angled one

// ---------- Pins ----------
const int PIN_TRIGGER = 13;   // from main ESP32 GPIO 27 (plus a shared GND)
const int PIN_LIGHT   = 4;    // on-board flash LED; or a MOSFET for the LED strip
const bool USE_ONBOARD_FLASH = false;  // the on-board flash causes glare; prefer the strip

// AI-Thinker camera pins
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

bool lastTrigger = LOW;

bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_VGA;      // 640x480, matches the detector input size
  c.jpeg_quality = 12;               // 10-15 is a good balance
  c.fb_count = 1;                    // 1 buffer: no "cam_hal: FB-OVF" spam, photo is always fresh
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (esp_camera_init(&c) != ESP_OK) return false;

  sensor_t* s = esp_camera_sensor_get();
  s->set_whitebal(s, 1);
  s->set_exposure_ctrl(s, 1);
  s->set_gain_ctrl(s, 1);
  // s->set_vflip(s, 1);  // uncomment if the image is upside down
  return true;
}

// Starts Wi-Fi and waits up to 15 s. Never calls begin() while an attempt is still running.
void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.printf("[WIFI] connecting to \"%s\"", WIFI_SSID);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) { delay(500); Serial.print("."); }
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("\n[WIFI] OK, IP: %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.printf("\n[WIFI] FAILED (status %d). Check SSID/password, use a 2.4 GHz hotspot. Retrying in 30 s.\n", (int)WiFi.status());
}

void captureAndSend() {
  Serial.println("Taking photo...");
  if (USE_ONBOARD_FLASH) digitalWrite(PIN_LIGHT, HIGH);
  delay(150);  // let exposure settle under the light (main ESP32 turns the LEDs on)

  // Throw away one frame: the buffer may hold an image from before the note landed.
  camera_fb_t* fb = esp_camera_fb_get();
  if (fb) esp_camera_fb_return(fb);
  fb = esp_camera_fb_get();

  if (USE_ONBOARD_FLASH) digitalWrite(PIN_LIGHT, LOW);
  if (!fb) { Serial.println("Capture failed"); return; }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("Photo OK (%u bytes) but NO Wi-Fi, so it was not sent.\n", fb->len);
    esp_camera_fb_return(fb);
    return;
  }
  String url = String(SERVER) + "/capture?cam=" + CAM_NAME;
  Serial.printf("Photo OK (%u bytes). Sending to %s ... (can take up to 10 s)\n", fb->len, url.c_str());
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(8000);
  http.begin(url);
  http.addHeader("Content-Type", "image/jpeg");
  int code = http.POST(fb->buf, fb->len);
  Serial.printf("POST %s -> %d, %u bytes: %s\n", url.c_str(), code, fb->len, http.getString().c_str());
  http.end();
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_TRIGGER, INPUT_PULLDOWN);
  pinMode(PIN_LIGHT, OUTPUT);
  digitalWrite(PIN_LIGHT, LOW);
  if (!initCamera()) {
    Serial.println("Camera init failed: check the ribbon cable and the 5V supply. Restarting.");
    delay(2000);
    ESP.restart();
  }
  connectWifi();
  Serial.printf("Ready: camera \"%s\"\n", CAM_NAME);
}

void loop() {
  static uint32_t lastWifiTry = millis();
  if (WiFi.status() != WL_CONNECTED && millis() - lastWifiTry > 30000) { lastWifiTry = millis(); connectWifi(); }

  bool trig = digitalRead(PIN_TRIGGER);
  if (trig == HIGH && lastTrigger == LOW) captureAndSend();  // rising edge only
  lastTrigger = trig;

  // Serial "c" = manual capture, handy while testing on the table.
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == 'c' || ch == 'C') captureAndSend();
  }
  delay(10);
}
