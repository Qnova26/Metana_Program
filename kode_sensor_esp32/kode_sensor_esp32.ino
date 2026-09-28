/*
  ============================================================================
  ESP32 GAS MONITORING - FIRMWARE UTAMA (VERSI FINAL - ESP32 CORE V3.X)
  TGS2611-E00 + DHT22 + Baterai Li-ion 2S + OLED + Pompa Sampling + WiFi + NVS
  ============================================================================
  BAGIAN BATERAI SUDAH DIKALIBRASI:
    - Sensor Tegangan 0-25V (modul voltage sensor)
    - Calibration Factor : 1.045212
    - Sensor Ratio       : 5.0
  ============================================================================
  BAGIAN POMPA:
    - Driver  : MOSFET (aman untuk duty besar)
    - Unit    : 1 unit (hisap + dorong bersamaan)
    - Duty    : 230 / 255 = 90%
  ============================================================================
  PERILAKU FINAL:
    - Sensor TGS dibaca REALTIME di semua state (tiap 1 detik)
    - OLED + Serial Monitor refresh tiap 1 MENIT
    - Kirim ke server tiap 1 MENIT (MULAI dari PURGING)
    - WARMUP: TIDAK kirim ke server (skip)
    - Siklus: WARMUP -> PURGING -> ACQUISITION -> WAIT -> PURGING -> ...
    - Timestamp format ISO 8601: %Y-%m-%dT%H:%M:%S+07:00
  ============================================================================
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>
#include <Preferences.h>
#include "time.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ============================================================================
// WIRING (JANGAN DIUBAH)
// ============================================================================
constexpr uint8_t GAS_PIN         = 32;
constexpr uint8_t DHT_PIN         = 4;
constexpr uint8_t OLED_SDA        = 21;
constexpr uint8_t OLED_SCL        = 22;
constexpr uint8_t BATTERY_ADC_PIN = 35;
constexpr uint8_t PUMP_PIN        = 27;

constexpr uint8_t DHTTYPE       = DHT22;
constexpr uint8_t SCREEN_WIDTH  = 128;
constexpr uint8_t SCREEN_HEIGHT = 64;

// ============================================================================
// WIFI & SERVER
// ============================================================================
const char* ssid      = "TesESP32";
const char* password  = "12345678";
const char* serverURL = "http://10.33.102.153:5000/data";

const char* ntpServer          = "pool.ntp.org";
const long  gmtOffset_sec      = 7 * 3600;
const int   daylightOffset_sec = 0;

constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 10000UL;

// ============================================================================
// TGS2611-E00 / R0 DARI NVS
// ============================================================================
constexpr float ADC_VREF_GAS  = 3.3;
constexpr float VCC_CIRCUIT   = 5.0;
constexpr float RL_VALUE      = 10.0;
constexpr float CH4_A         = 5000.0;
constexpr float CH4_EXPONENT  = -2.047;
constexpr int   GAS_OVERSAMPLE = 32;
constexpr float EMA_ALPHA_CH4  = 0.2;

const char* NVS_NAMESPACE = "gascal";
const char* NVS_KEY_R0    = "ro_manual";

// ============================================================================
// BATERAI LI-ION 2S (SUDAH DIKALIBRASI)
// ============================================================================
constexpr float SENSOR_RATIO        = 5.0;
constexpr float CALIBRATION_FACTOR  = 1.045212;
constexpr float ADC_VREF            = 3.3;
constexpr int   ADC_RES             = 4095;
constexpr int   BATT_NUM_SAMPLES    = 30;
constexpr float LOW_BATTERY_THRESHOLD = 20.0;

struct BattPoint { float voltage; float percent; };
BattPoint battTable[] = {
  {8.40, 100}, {8.30, 95}, {8.22, 90}, {8.16, 85}, {8.04, 80},
  {7.96, 75}, {7.90, 70}, {7.82, 65}, {7.74, 60}, {7.70, 55},
  {7.68, 50}, {7.64, 45}, {7.60, 40}, {7.58, 35}, {7.54, 30},
  {7.50, 25}, {7.46, 20}, {7.42, 15}, {7.38, 10}, {7.22, 5},
  {6.00, 0}
};
const int battTableLen = sizeof(battTable) / sizeof(battTable[0]);
constexpr unsigned long BATTERY_READ_INTERVAL_MS = 1000UL;

// ============================================================================
// DHT22
// ============================================================================
constexpr float DHT_TEMP_OFFSET = 0.0;
constexpr float DHT_HUM_OFFSET  = 0.0;
constexpr unsigned long DHT_READ_INTERVAL_MS = 2000UL;

// ============================================================================
// KOMPENSASI LAPANGAN CH4 UNTUK KANDANG SAPI
// ============================================================================
constexpr float RAW_CLEAN_BASELINE = 500.31;
constexpr float KANDANG_AMBIENT    = 25.0;
constexpr float SENSITIVITY_SCALE  = 0.05;
constexpr float MAX_CH4_LIMIT      = 1000.0;

// ============================================================================
// POMPA PWM (LEDC ESP32 CORE V3.X) - DUTY 90%
// ============================================================================
constexpr int     PUMP_PWM_FREQ_HZ      = 5000;
constexpr int     PUMP_PWM_RESOLUTION_BITS = 8;
constexpr uint8_t PUMP_PWM_DUTY         = 230;

// ============================================================================
// TIMING SIKLUS SAMPLING
// ============================================================================
constexpr unsigned long SPLASH_DURATION_MS      = 3000UL;
constexpr unsigned long WARMUP_DURATION_MS      = 180UL * 1000UL;
constexpr unsigned long PURGING_DURATION_MS     = 180UL * 1000UL;
constexpr unsigned long ACQUISITION_DURATION_MS = 60UL * 1000UL;
constexpr unsigned long WAIT_DURATION_MS        = 60UL * 1000UL;

// --- Interval ---
constexpr unsigned long GAS_READ_INTERVAL_MS    = 1000UL;    // TGS dibaca tiap 1 detik
constexpr unsigned long OLED_UPDATE_INTERVAL_MS = 60000UL;   // OLED + Serial refresh tiap 1 MENIT
constexpr unsigned long SERVER_SEND_INTERVAL_MS = 60000UL;   // Kirim ke server tiap 1 MENIT

// ============================================================================
// OBJEK GLOBAL
// ============================================================================
Preferences prefs;
DHT dht(DHT_PIN, DHTTYPE);
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);

enum SystemState { STATE_ERROR, WARMUP, PURGING, ACQUISITION, WAIT };
SystemState currentState;
unsigned long stateStartTime = 0;

float r0Value = 0.0;
bool  r0Valid = false;

float emaCH4 = -1.0;

float lastTemperature = NAN;
float lastHumidity    = NAN;
unsigned long lastDHTReadTime = 0;

float lastBatteryVoltage = 0.0;
float lastBatteryPercent = 0.0;
unsigned long lastBatteryReadTime = 0;

// --- Timing variable ---
unsigned long lastGasReadTime    = 0;
unsigned long lastOLEDUpdate     = 0;
unsigned long lastServerSend     = 0;
unsigned long lastWiFiRetry      = 0;

// ============================================================================
// UTIL: NAMA STATE
// ============================================================================
String stateName(SystemState s) {
  switch (s) {
    case STATE_ERROR:  return "STATE_ERROR";
    case WARMUP:       return "WARM-UP";
    case PURGING:      return "PURGING";
    case ACQUISITION:  return "ACQUISITION";
    case WAIT:         return "WAIT";
  }
  return "UNKNOWN";
}

// ============================================================================
// TIMESTAMP (NTP) - FORMAT ISO 8601 dengan timezone WIB
// ============================================================================
String getFormattedTimestamp() {
  struct tm timeinfo;
  if (!getLocalTime(&timeinfo)) return "N/A";
  char buf[32];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S+07:00", &timeinfo);
  return String(buf);
}

// ============================================================================
// BATTERY BAR UNICODE
// ============================================================================
String buildBatteryBarUnicode(float percent) {
  int filled = (int)round(percent / 10.0);
  if (filled > 10) filled = 10;
  if (filled < 0)  filled = 0;
  String bar = "[";
  for (int i = 0; i < 10; i++) bar += (i < filled) ? "\u2588" : "\u2591";
  bar += "]";
  return bar;
}

// ============================================================================
// TEKS RATA TENGAH
// ============================================================================
void printCentered(const String &text, int y) {
  int16_t x1, y1;
  uint16_t w, h;
  display.getTextBounds(text, 0, y, &x1, &y1, &w, &h);
  int x = (SCREEN_WIDTH - (int)w) / 2;
  if (x < 0) x = 0;
  display.setCursor(x, y);
  display.println(text);
}

// ============================================================================
// SPLASH SCREEN
// ============================================================================
void showSplashScreen() {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  printCentered("GAS DETECTOR", 24);
  printCentered("METHANE PORTABLE", 36);
  display.display();
  delay(SPLASH_DURATION_MS);
}

// ============================================================================
// IKON BATERAI GRAFIS
// ============================================================================
void drawBatteryIcon(int x, int y, float percent) {
  constexpr int BODY_W = 20;
  constexpr int BODY_H = 10;
  constexpr int CAP_W  = 2;
  constexpr int CAP_H  = 4;

  display.drawRect(x, y, BODY_W, BODY_H, SSD1306_WHITE);
  display.fillRect(x + BODY_W, y + (BODY_H - CAP_H) / 2, CAP_W, CAP_H, SSD1306_WHITE);

  int maxFillW = BODY_W - 4;
  int fillW = (int)((percent / 100.0) * maxFillW);
  if (fillW < 0) fillW = 0;
  if (fillW > maxFillW) fillW = maxFillW;
  if (fillW > 0) {
    display.fillRect(x + 2, y + 2, fillW, BODY_H - 4, SSD1306_WHITE);
  }
}

// ============================================================================
// initSensors()
// ============================================================================
void initSensors() {
  Wire.begin(OLED_SDA, OLED_SCL);
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("[OLED] Gagal inisialisasi SSD1306!");
  }
  display.clearDisplay();
  display.display();

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  pinMode(GAS_PIN, INPUT);
  pinMode(BATTERY_ADC_PIN, INPUT);

  ledcAttach(PUMP_PIN, PUMP_PWM_FREQ_HZ, PUMP_PWM_RESOLUTION_BITS);
  ledcWrite(PUMP_PIN, 0);

  dht.begin();
}

// ============================================================================
// loadR0FromNVS()
// ============================================================================
bool loadR0FromNVS(float &outR0) {
  prefs.begin(NVS_NAMESPACE, true);
  bool exists = prefs.isKey(NVS_KEY_R0);
  float val = prefs.getFloat(NVS_KEY_R0, 0.0);
  prefs.end();
  if (!exists || val <= 0.0) return false;
  outR0 = val;
  return true;
}

// ============================================================================
// readTGS2611()
// ============================================================================
bool readTGS2611(float &rsOut) {
  long sum = 0;
  for (int i = 0; i < GAS_OVERSAMPLE; i++) {
    sum += analogRead(GAS_PIN);
    delayMicroseconds(100);
  }
  float rawAvg = (float)sum / GAS_OVERSAMPLE;
  float vRL = (rawAvg / 4095.0) * ADC_VREF_GAS;

  if (vRL <= 0.02 || vRL >= (ADC_VREF_GAS - 0.02)) return false;
  rsOut = (VCC_CIRCUIT / vRL - 1.0) * RL_VALUE;
  if (rsOut <= 0) return false;
  return true;
}

float rsToPpm(float rs) {
  if (!r0Valid || r0Value <= 0) return 0.0;
  float ratio = rs / r0Value;
  if (ratio <= 0) return 0.0;
  float rawPpm = CH4_A * pow(ratio, CH4_EXPONENT);

  float correctedPpm = KANDANG_AMBIENT + (rawPpm - RAW_CLEAN_BASELINE) * SENSITIVITY_SCALE;
  correctedPpm = fminf(fmaxf(correctedPpm, 0.0f), MAX_CH4_LIMIT);
  return correctedPpm;
}

// ============================================================================
// filterCH4()
// ============================================================================
float filterCH4(float rawPpm) {
  if (emaCH4 < 0) emaCH4 = rawPpm;
  else emaCH4 = (EMA_ALPHA_CH4 * rawPpm) + ((1.0 - EMA_ALPHA_CH4) * emaCH4);
  return emaCH4;
}

// ============================================================================
// readDHT22()
// ============================================================================
void readDHT22() {
  unsigned long now = millis();
  if (now - lastDHTReadTime < DHT_READ_INTERVAL_MS) return;
  lastDHTReadTime = now;

  float t = dht.readTemperature();
  float h = dht.readHumidity();

  if (!isnan(t)) lastTemperature = t + DHT_TEMP_OFFSET;
  else Serial.println("[DHT22] Gagal membaca suhu, mempertahankan nilai terakhir.");

  if (!isnan(h)) lastHumidity = h + DHT_HUM_OFFSET;
  else Serial.println("[DHT22] Gagal membaca kelembapan, mempertahankan nilai terakhir.");
}

// ============================================================================
// calculateBatteryPercentage()
// ============================================================================
float calculateBatteryPercentage(float vPack) {
  if (vPack >= battTable[0].voltage) return battTable[0].percent;
  if (vPack <= battTable[battTableLen - 1].voltage) return battTable[battTableLen - 1].percent;
  for (int i = 0; i < battTableLen - 1; i++) {
    float vHigh = battTable[i].voltage;
    float vLow  = battTable[i + 1].voltage;
    if (vPack <= vHigh && vPack >= vLow) {
      float ratio = (vPack - vLow) / (vHigh - vLow);
      return battTable[i + 1].percent + ratio * (battTable[i].percent - battTable[i + 1].percent);
    }
  }
  return 0.0;
}

// ============================================================================
// readBattery()
// ============================================================================
void readBattery() {
  unsigned long now = millis();
  if (now - lastBatteryReadTime < BATTERY_READ_INTERVAL_MS) return;
  lastBatteryReadTime = now;

  long sum = 0;
  for (int i = 0; i < BATT_NUM_SAMPLES; i++) {
    sum += analogRead(BATTERY_ADC_PIN);
    delayMicroseconds(100);
  }
  float rawAvg = (float)sum / BATT_NUM_SAMPLES;
  float vAdc   = (rawAvg / (float)ADC_RES) * ADC_VREF;

  lastBatteryVoltage = vAdc * SENSOR_RATIO * CALIBRATION_FACTOR;
  lastBatteryPercent = calculateBatteryPercentage(lastBatteryVoltage);
}

// ============================================================================
// controlPump()
// ============================================================================
void controlPump(bool on) {
  ledcWrite(PUMP_PIN, on ? PUMP_PWM_DUTY : 0);
}

// ============================================================================
// connectWiFi()
// ============================================================================
void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, password);
  Serial.print("[WiFi] Menghubungkan");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 10000UL) {
    Serial.print(".");
    delay(300);
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("[WiFi] Tersambung. IP: ");
    Serial.println(WiFi.localIP());
    configTime(gmtOffset_sec, daylightOffset_sec, ntpServer);
  } else {
    Serial.println("[WiFi] Belum tersambung saat boot, akan dicoba lagi otomatis.");
  }
}

void maintainWiFi(unsigned long now) {
  if (WiFi.status() == WL_CONNECTED) return;
  if (now - lastWiFiRetry < WIFI_RETRY_INTERVAL_MS) return;
  lastWiFiRetry = now;
  Serial.println("[WiFi] Terputus, mencoba menyambung ulang...");
  WiFi.disconnect();
  WiFi.begin(ssid, password);
}

// ============================================================================
// sendDataToServer()
// ============================================================================
bool sendDataToServer(float ch4, float temp, float hum, float battV, float battPct,
                       const String &battBar, const String &stateLabel) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[HTTP] Dilewati: WiFi tidak tersambung.");
    return false;
  }

  HTTPClient http;
  http.begin(serverURL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(3000);

  String ts = getFormattedTimestamp();
  float safeTemp = isnan(temp) ? 0.0 : temp;
  float safeHum  = isnan(hum)  ? 0.0 : hum;

  String json = "{";
  json += "\"device_id\":\"ESP32_GAS_NODE\",";
  json += "\"timestamp\":\"" + ts + "\",";
  json += "\"ch4_ppm\":" + String(ch4, 2) + ",";
  json += "\"humidity\":" + String(safeHum, 1) + ",";
  json += "\"temperature\":" + String(safeTemp, 1) + ",";
  json += "\"battery_v\":" + String(battV, 2) + ",";
  json += "\"battery_pct\":" + String(battPct, 1) + ",";
  json += "\"battery_bar\":\"" + battBar + "\",";
  json += "\"cycle\":\"" + stateLabel + "\",";
  json += "\"relay_status\":" + String((currentState == PURGING || currentState == ACQUISITION) ? "true" : "false");
  json += "}";

  int httpCode = http.POST(json);
  bool ok = (httpCode > 0 && httpCode < 400);
  if (ok) {
    Serial.print("[HTTP] Data terkirim. Kode: ");
    Serial.println(httpCode);
  } else {
    Serial.print("[HTTP] GAGAL mengirim. Kode: ");
    Serial.println(httpCode);
  }
  http.end();
  return ok;
}

// ============================================================================
// readGasRealtime()  <<< Baca TGS di semua state >>>
// ============================================================================
void readGasRealtime() {
  unsigned long now = millis();
  if (now - lastGasReadTime < GAS_READ_INTERVAL_MS) return;
  lastGasReadTime = now;

  float rs;
  if (readTGS2611(rs)) {
    float instantPpm = rsToPpm(rs);
    filterCH4(instantPpm);
  } else {
    Serial.println("[TGS2611] Pembacaan tidak valid, mempertahankan nilai terakhir.");
  }
}

// ============================================================================
// handleSamplingState()
// ============================================================================
void handleSamplingState(unsigned long now) {
  switch (currentState) {

    case STATE_ERROR:
      controlPump(false);
      break;

    case WARMUP: {
      controlPump(false);
      if (now - stateStartTime >= WARMUP_DURATION_MS) {
        currentState = PURGING;
        stateStartTime = now;
        controlPump(true);
        lastServerSend = now;   // Reset timer server saat mulai PURGING
        Serial.println("[STATE] WARM-UP selesai -> PURGING (mulai kirim ke server)");
      }
      break;
    }

    case PURGING: {
      controlPump(true);
      if (now - stateStartTime >= PURGING_DURATION_MS) {
        currentState = ACQUISITION;
        stateStartTime = now;
        Serial.println("[STATE] PURGING selesai -> ACQUISITION");
      }
      break;
    }

    case ACQUISITION: {
      controlPump(true);
      if (now - stateStartTime >= ACQUISITION_DURATION_MS) {
        currentState = WAIT;
        stateStartTime = now;
        controlPump(false);
        Serial.println("[STATE] ACQUISITION selesai -> WAIT");
      }
      break;
    }

    case WAIT: {
      controlPump(false);
      if (now - stateStartTime >= WAIT_DURATION_MS) {
        currentState = PURGING;
        stateStartTime = now;
        controlPump(true);
        Serial.println("[STATE] WAIT selesai -> PURGING");
      }
      break;
    }
  }
}

// ============================================================================
// updateOLED()
// ============================================================================
void updateOLED(unsigned long now) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);

  if (currentState == STATE_ERROR) {
    display.println("ERROR: R0 TIDAK");
    display.println("DITEMUKAN DI NVS");
    display.println("Lakukan kalibrasi");
    display.println("sebelum monitoring.");
    display.display();
    return;
  }

  if (currentState == WARMUP) {
    unsigned long elapsed = now - stateStartTime;
    unsigned long remaining = (WARMUP_DURATION_MS > elapsed) ? (WARMUP_DURATION_MS - elapsed) / 1000UL : 0;
    display.println("Pemanasan Sensor");
    display.print("Sisa: "); display.print(remaining); display.println(" s");
    display.println("Pompa: OFF");
    display.println("Server: SKIP");
    display.display();
    return;
  }

  display.setCursor(0, 0);
  display.print("CH4   : "); display.print(emaCH4 < 0 ? 0.0 : emaCH4, 2); display.println(" ppm");

  display.setCursor(0, 9);
  display.print("Suhu  : ");
  if (isnan(lastTemperature)) display.println("Err");
  else { display.print(lastTemperature, 1); display.println(" C"); }

  display.setCursor(0, 18);
  display.print("Lembab: ");
  if (isnan(lastHumidity)) display.println("Err");
  else { display.print(lastHumidity, 1); display.println(" %"); }

  drawBatteryIcon(0, 28, lastBatteryPercent);
  display.setCursor(26, 30);
  display.print(lastBatteryPercent, 1); display.print("%  ");
  display.print(lastBatteryVoltage, 2); display.print("V");

  display.setCursor(0, 40);
  display.print("Siklus: "); display.println(stateName(currentState));

  display.setCursor(0, 50);
  if (lastBatteryPercent <= LOW_BATTERY_THRESHOLD) {
    display.println("SEGERA DICHARGER");
  } else {
    display.print("Pompa : ");
    display.println((currentState == PURGING || currentState == ACQUISITION) ? "ON" : "OFF");
  }

  display.display();
}

// ============================================================================
// updateSerial()
// ============================================================================
void updateSerial() {
  Serial.println("================================");

  if (currentState == STATE_ERROR) {
    Serial.println("ERROR: R0 TIDAK DITEMUKAN DI NVS");
    Serial.println("Lakukan kalibrasi sebelum menjalankan monitoring.");
    Serial.println("================================");
    return;
  }

  if (currentState == WARMUP) {
    unsigned long elapsed = millis() - stateStartTime;
    unsigned long remaining = (WARMUP_DURATION_MS > elapsed) ? (WARMUP_DURATION_MS - elapsed) / 1000UL : 0;
    Serial.println("SIKLUS     : WARM-UP");
    Serial.println("TGS2611    : PEMANASAN");
    Serial.println("Pompa      : OFF");
    Serial.println("Server     : SKIP (belum kirim)");
    Serial.print("Sisa       : "); Serial.print(remaining); Serial.println(" detik");
    Serial.print("CH4        : "); Serial.print(emaCH4 < 0 ? 0.0 : emaCH4, 2); Serial.println(" ppm");
  } else {
    Serial.print("Siklus     : "); Serial.println(stateName(currentState));
    Serial.print("Pompa      : ");
    Serial.println((currentState == PURGING || currentState == ACQUISITION) ? "ON" : "OFF");
    Serial.print("CH4        : "); Serial.print(emaCH4 < 0 ? 0.0 : emaCH4, 2); Serial.println(" ppm");
  }

  Serial.print("Suhu       : ");
  if (isnan(lastTemperature)) Serial.println("Err");
  else { Serial.print(lastTemperature, 1); Serial.println(" C"); }

  Serial.print("Kelembapan : ");
  if (isnan(lastHumidity)) Serial.println("Err");
  else { Serial.print(lastHumidity, 1); Serial.println(" %"); }

  Serial.print("Baterai    : "); Serial.print(buildBatteryBarUnicode(lastBatteryPercent));
  Serial.print(" "); Serial.print(lastBatteryPercent, 1); Serial.println(" %");
  Serial.print("Battery V  : "); Serial.print(lastBatteryVoltage, 2); Serial.println(" V");

  if (lastBatteryPercent <= LOW_BATTERY_THRESHOLD) Serial.println("SEGERA DICHARGER");

  Serial.println("================================");
}

// ============================================================================
// SETUP
// ============================================================================
void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  Serial.begin(115200);
  delay(1000);

  initSensors();
  showSplashScreen();
  connectWiFi();

  r0Valid = loadR0FromNVS(r0Value);
  if (!r0Valid) {
    currentState = STATE_ERROR;
    controlPump(false);
    Serial.println("================================");
    Serial.println("ERROR: R0 TIDAK DITEMUKAN DI NVS!");
    Serial.println("================================");
  } else {
    currentState = WARMUP;
    Serial.print("[NVS] R0 dimuat = "); Serial.print(r0Value, 4); Serial.println(" kOhm");
    Serial.println("Memulai WARM-UP (180 detik)...");
    Serial.println("Server akan MULAI kirim setelah PURGING.");
  }
  stateStartTime = millis();

  // Inisialisasi timing
  lastGasReadTime = millis();
  lastOLEDUpdate  = millis();
  lastServerSend  = millis();

  // Tampilan pertama
  updateOLED(millis());
  updateSerial();
}

// ============================================================================
// LOOP
// ============================================================================
void loop() {
  unsigned long now = millis();

  // ===== Rutin WiFi & sensor =====
  maintainWiFi(now);
  readDHT22();
  readBattery();

  // ===== Baca TGS REALTIME di semua state =====
  readGasRealtime();

  // ===== Siklus state =====
  if (currentState != STATE_ERROR) {
    handleSamplingState(now);
  }

  // ===== Update OLED + Serial tiap 1 MENIT =====
  if (now - lastOLEDUpdate >= OLED_UPDATE_INTERVAL_MS) {
    lastOLEDUpdate = now;
    updateOLED(now);
    updateSerial();
  }

  // ===== Kirim ke server tiap 1 MENIT, MULAI dari PURGING =====
  if (now - lastServerSend >= SERVER_SEND_INTERVAL_MS) {
    if (currentState != STATE_ERROR && currentState != WARMUP) {
      lastServerSend = now;

      String bar = buildBatteryBarUnicode(lastBatteryPercent);
      String stateLabel = stateName(currentState);

      float ch4ToSend = (emaCH4 < 0) ? 0.0 : emaCH4;
      sendDataToServer(ch4ToSend, lastTemperature, lastHumidity,
                       lastBatteryVoltage, lastBatteryPercent,
                       bar, stateLabel);
    }
  }
}