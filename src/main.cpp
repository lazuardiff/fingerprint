#include <WiFi.h>
#include <WiFiManager.h>
#include <HTTPClient.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_SSD1306.h>
#include <Adafruit_Fingerprint.h>
#include <ArduinoJson.h>
void showMessage(String line1, String line2, String line3, String line4, int delayTime);

#define FP_DEBUG 1
#ifndef FP_OLED_MS
#define FP_OLED_MS 800
#endif

const char *fpCodeToStr(uint8_t c)
{
  switch (c)
  {
  case FINGERPRINT_OK:
    return "OK";
  case FINGERPRINT_PACKETRECIEVEERR:
    return "PACKET";
  case FINGERPRINT_NOFINGER:
    return "NOFINGER";
  case FINGERPRINT_IMAGEFAIL:
    return "IMAGEFAIL";
  case FINGERPRINT_IMAGEMESS:
    return "IMAGEMESS";
  case FINGERPRINT_FEATUREFAIL:
    return "FEATFAIL";
  case FINGERPRINT_INVALIDIMAGE:
    return "INVALIDIMG";
  case FINGERPRINT_ENROLLMISMATCH:
    return "ENROLLMIS";
  case FINGERPRINT_BADLOCATION:
    return "BADLOC";
  case FINGERPRINT_FLASHERR:
    return "FLASHERR";
  case FINGERPRINT_NOTFOUND:
    return "NOTFOUND";
  case FINGERPRINT_TIMEOUT:
    return "TIMEOUT";
  default:
    return "UNKNOWN";
  }
}

void FP_LOG(const char *where, uint8_t code)
{
#if FP_DEBUG
  Serial.print("[FP] ");
  Serial.print(where);
  Serial.print(" -> code=");
  Serial.print(code);
  Serial.print(" (0x");
  Serial.print(code, HEX);
  Serial.print(") ");
  Serial.println(fpCodeToStr(code));

  // tampilkan singkat di OLED via showMessage (tanpa akses 'display' langsung)
  String line1 = String(where);
  String line2 = "FP: " + String(code) + " 0x" + String(code, HEX) + " " + fpCodeToStr(code);
  showMessage(line1, line2, "", "", FP_OLED_MS);
#endif
}

#define RX_PIN 16
#define TX_PIN 17
#define I2C_SDA 21
#define I2C_SCL 22
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define BUTTON_PIN 14
#define BUZZER_PIN 27

// WiFi and MQTT config
const char *mqtt_server = "31.97.106.30";
const int mqtt_port = 1883;
const char *mqtt_user = "guest";
const char *mqtt_password = "guest";
const char *ENROLL_TOPIC = "fingerprint/enroll";
const char *DELETE_TOPIC = "fingerprint/delete";
const char *RESET_TOPIC = "fingerprint/resetwifi";

WiFiClient espClient;
PubSubClient client(espClient);
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
HardwareSerial mySerial(2);
Adafruit_Fingerprint finger = Adafruit_Fingerprint(&mySerial);

bool wifiReady = false;
bool isBusy = false;
bool nextScanIsOff = false;

void showMessage(String line1, String line2 = "", String line3 = "", String line4 = "", int delayTime = 1000)
{
  display.clearDisplay();
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.setTextColor(WHITE);
  if (line1 != "")
    display.println(line1);
  if (line2 != "")
    display.println(line2);
  if (line3 != "")
    display.println(line3);
  if (line4 != "")
    display.println(line4);
  display.display();
  if (delayTime > 0)
    vTaskDelay(delayTime / portTICK_PERIOD_MS);
}

void buzzSuccess()
{
  ledcAttachPin(BUZZER_PIN, 0); // Channel 0
  ledcWriteTone(0, 2000);       // 2kHz frequency
  delay(300);                   // Ring for 300ms
  ledcDetachPin(BUZZER_PIN);    // Stop PWM
}

void buzzFailure()
{
  ledcAttachPin(BUZZER_PIN, 0);
  ledcWriteTone(0, 500); // 500Hz (lower pitch)
  delay(500);
  ledcDetachPin(BUZZER_PIN);
}

void buzzOffChange()
{
  ledcAttachPin(BUZZER_PIN, 0);
  ledcWriteTone(0, 1000);
  delay(100);
  ledcWriteTone(0, 600);
  delay(100);
  ledcDetachPin(BUZZER_PIN);
}

void checkButton()
{
  static bool lastState = HIGH;
  bool state = digitalRead(BUTTON_PIN);
  if (lastState == HIGH && state == LOW)
  {
    nextScanIsOff = true;
    showMessage("Scan fingerprint", "untuk merubah status", "jadi OFF");
  }
  lastState = state;
}

uint8_t enrollFingerprint(uint8_t id)
{
  isBusy = true;
  int p = -1;

  showMessage("Letakkan jari...");
  while ((p = finger.getImage()) != FINGERPRINT_OK)
  {
    FP_LOG("enroll:getImage", p);
    vTaskDelay(100 / portTICK_PERIOD_MS);
  }
  FP_LOG("enroll:getImage", p);

  p = finger.image2Tz(1);
  FP_LOG("enroll:image2Tz(1)", p);
  if (p != FINGERPRINT_OK)
  {
    isBusy = false;
    return p;
  }

  showMessage("Angkat jari");
  vTaskDelay(1500 / portTICK_PERIOD_MS);

  showMessage("Letakkan jari lagi...");
  int retryCount = 0;
  const int maxRetries = 50;
  while (retryCount < maxRetries && (p = finger.getImage()) != FINGERPRINT_OK)
  {
    FP_LOG("enroll:getImage#2", p);
    vTaskDelay(100 / portTICK_PERIOD_MS);
    retryCount++;
  }
  FP_LOG("enroll:getImage#2", p);
  if (p != FINGERPRINT_OK)
  {
    isBusy = false;
    return p;
  }

  p = finger.image2Tz(2);
  FP_LOG("enroll:image2Tz(2)", p);
  if (p != FINGERPRINT_OK)
  {
    isBusy = false;
    return p;
  }

  p = finger.createModel();
  FP_LOG("enroll:createModel", p);
  if (p != FINGERPRINT_OK)
  {
    isBusy = false;
    return p;
  }

  p = finger.storeModel(id);
  FP_LOG("enroll:storeModel", p);

  isBusy = false;
  return p;
}

uint8_t deleteFingerprint(uint8_t id)
{
  isBusy = true;
  uint8_t res = finger.deleteModel(id);
  FP_LOG("delete:deleteModel", res);
  isBusy = false;
  return res;
}

void resetWiFiSettings()
{
  WiFiManager wm;
  wm.resetSettings(); // Hapus SSID & password lama
  ESP.restart();      // Restart ESP agar langsung masuk ke AP Mode
}

void mqttCallback(char *topic, byte *payload, unsigned int length)
{
  String message;
  for (int i = 0; i < length; i++)
    message += (char)payload[i];

  StaticJsonDocument<128> doc;
  DeserializationError error = deserializeJson(doc, message);
  if (error)
    return;

  String command = doc["command"];
  uint8_t id = doc["id"];

  if (String(topic) == ENROLL_TOPIC && command == "enroll")
  {
    showMessage("Mode Daftar", "ID: " + String(id));
    uint8_t result = enrollFingerprint(id);
    String statusText = (result == FINGERPRINT_OK) ? "Fingerprint Terdaftar" : "Pendaftaran Gagal";
    showMessage(statusText, "ID: " + String(id));

    StaticJsonDocument<128> res;
    res["id"] = id;
    res["status"] = (result == FINGERPRINT_OK) ? "success" : "failed";
    if (result != FINGERPRINT_OK)
      res["reason"] = "error";

    HTTPClient http;
    http.begin("http://31.97.106.30:8080/enroll/status");
    http.addHeader("Content-Type", "application/json");
    String requestBody;
    serializeJson(res, requestBody);
    http.POST(requestBody);
    http.end();
    showMessage("Ready", "", "", "", 2000);
  }
  else if (String(topic) == DELETE_TOPIC && command == "delete")
  {
    showMessage("Mode Hapus", "ID: " + String(id));
    uint8_t result = deleteFingerprint(id);
    String statusText = (result == FINGERPRINT_OK) ? "Fingerprint Terhapus" : "Penghapusan gagal";
    showMessage(statusText, "ID: " + String(id));

    StaticJsonDocument<128> res;
    res["id"] = id;
    res["status"] = (result == FINGERPRINT_OK) ? "success" : "failed";
    if (result != FINGERPRINT_OK)
      res["reason"] = "error";

    HTTPClient http;
    http.begin("http://31.97.106.30:8080/delete/status");
    http.addHeader("Content-Type", "application/json");
    String requestBody;
    serializeJson(res, requestBody);
    http.POST(requestBody);
    http.end();
  }
  else if (String(topic) == RESET_TOPIC && command == "reset")
  {
    showMessage("Reset WiFi via MQTT");
    delay(2000);         // beri jeda untuk user lihat
    resetWiFiSettings(); // Panggil fungsi reset WiFi
  }
}

void connectToWiFi()
{
  WiFi.mode(WIFI_STA); // Pastikan mode station
  WiFiManager wm;

  // Ini akan coba connect ke WiFi tersimpan.
  // Kalau gagal, akan masuk AP mode dan buka portal
  bool res = wm.autoConnect("ESP32_Config", "admin123");

  if (!res)
  {
    Serial.println("Gagal konek WiFi");
    showMessage("WiFi gagal", "Hubungkan lagi");
    delay(3000);
    ESP.restart(); // Atau bisa masuk deep sleep dll
  }
  else
  {
    Serial.println("WiFi terhubung!");
    showMessage("WiFi terhubung");
    delay(3000);
    showMessage("Fingerprint Ready");
  }
}

int wifiRetryCount = 0;

void ensureWiFiConnected()
{
  if (WiFi.status() != WL_CONNECTED)
  {
    showMessage("WiFi terputus", "Menyambung ulang...");
    buzzFailure();
    delay(1000);
    WiFi.begin();

    unsigned long tStart = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - tStart < 10000)
    {
      showMessage("WiFi terputus", "Menyambung ulang...");
      delay(500);
    }

    if (WiFi.status() != WL_CONNECTED)
    {
      wifiRetryCount++;
      showMessage("WiFi gagal", "Percobaan: " + String(wifiRetryCount));
      buzzFailure();
      delay(3000);
      if (wifiRetryCount >= 3)
      {
        showMessage("Reset WiFi", "Masuk mode AP...");
        buzzFailure();
        delay(3000);
        WiFiManager wm;
        wm.resetSettings();
        delay(1000);
        ESP.restart();
      }
    }
    else
    {
      wifiRetryCount = 0; // reset jika berhasil
    }
  }
}

void mqttReconnect()
{
  while (!client.connected())
  {
    if (client.connect("ESP32Client", mqtt_user, mqtt_password))
    {
      client.subscribe(ENROLL_TOPIC);
      client.subscribe(DELETE_TOPIC);
      client.subscribe(RESET_TOPIC);
    }
    else
    {
      vTaskDelay(5000 / portTICK_PERIOD_MS);
    }
  }
}

void wifiTask(void *parameter)
{
  connectToWiFi();
  wifiReady = true;
  vTaskDelete(NULL);
}

void mqttTask(void *parameter)
{
  while (!wifiReady)
  {
    vTaskDelay(100 / portTICK_PERIOD_MS);
  }

  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(mqttCallback);

  while (true)
  {
    if (!client.connected())
      mqttReconnect();
    client.loop();
    vTaskDelay(10 / portTICK_PERIOD_MS);
  }
}

void fingerprintTask(void *parameter)
{
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(BUZZER_PIN, OUTPUT);
  while (!wifiReady)
  {
    vTaskDelay(100 / portTICK_PERIOD_MS);
  }

  while (true)
  {
    checkButton();
    if (isBusy)
    {
      vTaskDelay(200 / portTICK_PERIOD_MS);
      continue;
    }

    uint8_t p = finger.getImage();
    FP_LOG("scan:getImage", p);
    if (p == FINGERPRINT_OK)
    {
      p = finger.image2Tz();
      FP_LOG("scan:image2Tz", p);
      if (p != FINGERPRINT_OK)
        continue;
      p = finger.fingerSearch();
      FP_LOG("scan:fingerSearch", p);
      if (p == FINGERPRINT_OK)
      {
        Serial.print("[FP] MATCH id=");
        Serial.print(finger.fingerID);
        Serial.print(" confidence=");
        Serial.println(finger.confidence);
        StaticJsonDocument<64> doc;
        doc["id"] = finger.fingerID;
        String body;
        serializeJson(doc, body);

        String url = "http://31.97.106.30:8080/drivers/" + String(finger.fingerID);
        if (nextScanIsOff)
        {
          url += "/OFF";
          nextScanIsOff = false;
        }

        int retryHttp = 0;
        const int maxHttpRetry = 3;
        bool success = false;

        while (retryHttp < maxHttpRetry)
        {
          ensureWiFiConnected(); // ⬅ Cek & sambung WiFi ulang kalau putus

          HTTPClient http;
          http.begin(url);
          http.addHeader("Content-Type", "application/json");
          int httpCode = http.POST(body);
          if (httpCode > 0)
          {
            String payload = http.getString();
            StaticJsonDocument<256> res;
            deserializeJson(res, payload);
            const char *name = res["driver_name"] | "Unknown";
            const char *oldStatus = res["old_status"] | "-";
            const char *newStatus = res["new_status"] | "-";
            showMessage(name, String(oldStatus) + " -> " + String(newStatus));
            if (url.endsWith("/OFF"))
              buzzOffChange();
            else
              buzzSuccess();
            success = true;
            http.end();
            break;
          }
          else
          {
            retryHttp++;
            showMessage("HTTP Error", "Retry: " + String(retryHttp));
            http.end();
            delay(1000); // jeda sebelum mencoba ulang
          }
        }

        if (!success)
        {
          showMessage("Gagal kirim data");
          buzzFailure();
        }
      }
      else
      {
        showMessage("Fingerprint Tidak", "Ditemukan");
        buzzFailure();
      }
      vTaskDelay(2000 / portTICK_PERIOD_MS);
      showMessage("Fingerprint Ready");
    }
    vTaskDelay(100 / portTICK_PERIOD_MS);
  }
}

void setup()
{
  Wire.begin(I2C_SDA, I2C_SCL);
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  display.clearDisplay();
  display.display();
  showMessage("Hubungkan ke WiFi...");

  // Serial debug utama
  Serial.begin(115200);
  Serial.println("\n[FP] Booting...");

  // UART ke sensor fingerprint
  mySerial.begin(57600, SERIAL_8N1, RX_PIN, TX_PIN);
  finger.begin(57600);

  // Verifikasi sensor
  if (finger.verifyPassword())
  {
    Serial.println("[FP] verifyPassword OK");
  }
  else
  {
    Serial.println("[FP] verifyPassword FAILED");
    showMessage("Sensor FP error", "verifyPassword() fail");
    while (1)
      vTaskDelay(1000 / portTICK_PERIOD_MS);
  }

  // Parameter & info awal sensor (opsional tapi berguna)
  uint8_t gp = finger.getParameters();
  if (gp == FINGERPRINT_OK)
  {
    Serial.print("[FP] status reg: ");
    Serial.println(finger.status_reg);
    Serial.print("[FP] system id : ");
    Serial.println(finger.system_id);
    Serial.print("[FP] capacity  : ");
    Serial.println(finger.capacity);
    Serial.print("[FP] sec level : ");
    Serial.println(finger.security_level);
    Serial.print("[FP] addr      : ");
    Serial.println(finger.device_addr);
    Serial.print("[FP] packet len: ");
    Serial.println(finger.packet_len);
    Serial.print("[FP] baud rate : ");
    Serial.println(finger.baud_rate);
    if (finger.getTemplateCount() == FINGERPRINT_OK)
    {
      Serial.print("[FP] template count: ");
      Serial.println(finger.templateCount);
    }
  }
  else
  {
    Serial.print("[FP] getParameters FAIL code=");
    Serial.print(gp);
    Serial.print(" (0x");
    Serial.print(gp, HEX);
    Serial.println(")");
  }

  // Jalankan tasks
  xTaskCreatePinnedToCore(wifiTask, "WiFi", 4096, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(mqttTask, "MQTT", 8192, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(fingerprintTask, "Fingerprint", 8192, NULL, 1, NULL, 1);
}

void loop()
{
  vTaskDelay(portMAX_DELAY);
}