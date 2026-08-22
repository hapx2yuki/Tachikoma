// タチコマ歩行ロボット ファームウェア
// ESP32 + PCA9685 + 12サーボ (3DOF x 4脚) + WS2812 + DFPlayer
// 操作: WiFi AP "Tachikoma" に接続し http://192.168.4.1/

#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPAsyncWebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WiFi.h>
#include <Wire.h>

#include "arms.h"
#include "audio.h"
#include "config.h"
#include "eyes.h"
#include "gait.h"
#include "peripherals.h"
#include "servos.h"
#include "web_ui.h"

Servos servos;
Gait gait;
Arms arms;
Eyes eyes;
Peripherals peri;
Audio audio;
AsyncWebServer server(80);
AsyncWebSocket ws("/ws");      // 操作系 (移動/腕/目/PTT)
AsyncWebSocket wsAudio("/audio");  // 音声系 (ブリッジがクライアントとして接続)

// 操作状態 (WebSocket から更新)
volatile float cmd_vx = 0, cmd_vy = 0, cmd_wz = 0;
volatile float cmd_h = BODY_H_DEF;
volatile bool cmd_stand = true, cmd_led = true, cmd_ptt = false;
uint32_t lastCmdMs = 0;

// STA (iPhone テザリング) 資格情報は NVS 保存 (setupWiFi() と /wifi POST
// ハンドラ参照)。ハードコード禁止 — SSID/パスワードは Web UI の設定タブから入力する
String staSsid;

void onWsEvent(AsyncWebSocket*, AsyncWebSocketClient* client, AwsEventType type,
               void* arg, uint8_t* data, size_t len) {
  if (type != WS_EVT_DATA) return;
  // 分割フレームは扱わない (UI の送信サイズなら単一フレームで収まる)
  AwsFrameInfo* info = (AwsFrameInfo*)arg;
  if (!info->final || info->index != 0 || info->len != len ||
      info->opcode != WS_TEXT) return;
  JsonDocument doc;
  if (deserializeJson(doc, data, len)) return;
  cmd_vx = constrain((float)(doc["vx"] | 0.0f), -1.0f, 1.0f);
  cmd_vy = constrain((float)(doc["vy"] | 0.0f), -1.0f, 1.0f);
  cmd_wz = constrain((float)(doc["wz"] | 0.0f), -1.0f, 1.0f);
  cmd_h = constrain((float)(doc["h"] | BODY_H_DEF), BODY_H_MIN, BODY_H_MAX);
  cmd_stand = (doc["stand"] | 1) != 0;
  cmd_led = (doc["led"] | 1) != 0;
  cmd_ptt = (doc["ptt"] | 0) != 0;  // PTT ボタン押下中 (loop 側で edge 検出)
  // 腕 (存在するキーのみ反映。UI はスライダ操作時のみ送ってくる)
  if (!doc["ay"].isNull()) arms.target[0].yaw = doc["ay"].as<float>();
  if (!doc["ap"].isNull()) arms.target[0].pitch = doc["ap"].as<float>();
  if (!doc["ae"].isNull()) arms.target[0].elbow = doc["ae"].as<float>();
  if (!doc["amir"].isNull()) arms.mirror = doc["amir"].as<int>() != 0;
  lastCmdMs = millis();
}

void onAudioWsEvent(AsyncWebSocket* s, AsyncWebSocketClient* c, AwsEventType type,
                     void* arg, uint8_t* data, size_t len) {
  audio.onEvent(s, c, type, arg, data, len);
}

// AP は常時維持 (操作UIのフォールバック)。STA は NVS 保存の資格情報が
// あれば iPhone テザリングへ join を試みる (資格情報はソースに置かない)
void setupWiFi() {
  WiFi.mode(WIFI_AP_STA);
  WiFi.softAP(AP_SSID, AP_PASS);

  Preferences p;
  p.begin(STA_PREFS_NS, /*readOnly=*/true);
  staSsid = p.getString("ssid", "");
  const String staPass = p.getString("pass", "");
  p.end();
  if (staSsid.length()) WiFi.begin(staSsid.c_str(), staPass.c_str());

  if (MDNS.begin(MDNS_HOST)) MDNS.addService("http", "tcp", 80);
}

void setupWeb() {
  ws.onEvent(onWsEvent);
  server.addHandler(&ws);
  wsAudio.onEvent(onAudioWsEvent);
  server.addHandler(&wsAudio);
  server.on("/", HTTP_GET, [](AsyncWebServerRequest* r) {
    r->send(200, "text/html", (const __FlashStringHelper*)INDEX_HTML);
  });
  server.on("/play", HTTP_GET, [](AsyncWebServerRequest* r) {
    if (r->hasParam("n")) peri.queueTrack(r->getParam("n")->value().toInt());
    r->send(200, "text/plain", "ok");
  });
  server.on("/eye", HTTP_GET, [](AsyncWebServerRequest* r) {
    // 視線モード: /eye?mode=kyoro|front|scan
    const String m = r->hasParam("mode") ? r->getParam("mode")->value() : "";
    if (m == "kyoro") eyes.mode = Eyes::KYORO;
    else if (m == "front") eyes.mode = Eyes::FRONT;
    else if (m == "scan") eyes.mode = Eyes::SCAN;
    r->send(200, "text/plain", "ok");
  });
  server.on("/arm", HTTP_GET, [](AsyncWebServerRequest* r) {
    // プリセット: /arm?pose=tuck|ready|reach|wave
    const String p = r->hasParam("pose") ? r->getParam("pose")->value() : "";
    if (p == "tuck") arms.setPose(ARM_POSE_TUCK);
    else if (p == "ready") arms.setPose(ARM_POSE_READY);
    else if (p == "reach") arms.setPose(ARM_POSE_REACH);
    else if (p == "wave") arms.startWave();
    r->send(200, "text/plain", "ok");
  });
  server.on("/trim", HTTP_GET, [](AsyncWebServerRequest* r) {
    if (r->hasParam("ch") && r->hasParam("us")) {
      servos.setTrim(r->getParam("ch")->value().toInt(),
                     r->getParam("us")->value().toInt());
      r->send(200, "text/plain", "ok");
      return;
    }
    String out = "[";
    for (int i = 0; i < N_CH; i++) {
      out += servos.trim(i);
      if (i < N_CH - 1) out += ",";
    }
    out += "]";
    r->send(200, "application/json", out);
  });
  server.on("/wifi", HTTP_GET, [](AsyncWebServerRequest* r) {
    JsonDocument doc;
    doc["ssid"] = staSsid;
    doc["connected"] = WiFi.status() == WL_CONNECTED;
    doc["ip"] = WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "";
    String out;
    serializeJson(doc, out);
    r->send(200, "application/json", out);
  });
  server.on("/wifi", HTTP_POST, [](AsyncWebServerRequest* r) {
    // STA (iPhone テザリング) 資格情報の保存。フォーム値は Web UI 設定
    // タブからのみ受け付ける (ハードコード禁止)
    if (r->hasParam("ssid", true)) {
      const String ssid = r->getParam("ssid", true)->value();
      const String pass = r->hasParam("pass", true)
                               ? r->getParam("pass", true)->value()
                               : String();
      Preferences p;
      p.begin(STA_PREFS_NS, false);
      p.putString("ssid", ssid);
      p.putString("pass", pass);
      p.end();
      staSsid = ssid;
      WiFi.begin(ssid.c_str(), pass.c_str());
    }
    r->send(200, "text/plain", "ok");
  });
  server.begin();
}

void setup() {
  Serial.begin(115200);
  randomSeed(esp_random());  // 目のサッカード用
  Wire.begin(PIN_SDA, PIN_SCL);
  servos.begin();
  peri.begin();
  setupWiFi();
  setupWeb();
  audio.begin(&wsAudio);
  servos.enableAll();
  peri.queueTrack(1);  // 起動音 (SD にあれば, loop 側で再生)
  Serial.println("Tachikoma ready: http://192.168.4.1/ / http://tachikoma.local/");
}

void loop() {
  static uint32_t lastUs = micros();
  static uint32_t lastTelem = 0;
  const uint32_t nowUs = micros();
  const float dt = (nowUs - lastUs) * 1e-6f;
  if (dt < 0.02f) return;  // 50Hz 制御
  lastUs = nowUs;

  servos.softStart();
  servos.persistTrims();

  // 音声 PTT: 通信断 (UI 切断) で自動解除する (歩行指令と同じフェイルセーフ)。
  // 歩行制御 (CALIBRATION_MODE) と無関係に、通常運用外でも音声だけは動く
  {
    static bool pttPrev = false;
    const bool pttNow = (millis() - lastCmdMs > 1500) ? false : cmd_ptt;
    if (pttNow != pttPrev) {
      audio.setPtt(pttNow);
      pttPrev = pttNow;
    }
  }

#ifdef CALIBRATION_MODE
  servos.allNeutral();
#else
  const bool timeout = millis() - lastCmdMs > 1500;  // 通信断で停止
  const float vx = timeout ? 0 : cmd_vx;
  const float vy = timeout ? 0 : cmd_vy;
  const float wz = timeout ? 0 : cmd_wz;

  // 低電圧カット: VBAT_CUT 未満 3 秒で脱力ラッチ (peripherals.h)
  const bool standAllowed = cmd_stand && !peri.cutout();

  // 起動/脱力の状態遷移
  static bool wasStanding = true;
  if (standAllowed && !wasStanding) servos.enableAll();   // 再起動: ソフトスタート
  if (!standAllowed && wasStanding) servos.disableAll();  // 脱力: パルス停止
  wasStanding = standAllowed;

  // legs[] は下の arms.update() (脚×腕連成クランプ) でも参照するため if
  // ブロックの外側で宣言する。2つの if は同一条件 (standAllowed &&
  // servos.ready()) なので、後段が実行される時点では必ず本ブロックで
  // 更新済み — 未初期化のまま読まれることはない
  LegCmd legs[4];
  if (standAllowed && servos.ready()) {
    gait.bodyH = cmd_h;
    gait.update(dt, vx, vy, wz, legs);
    for (int leg = 0; leg < 4; leg++) {
      servos.writeJoint(leg, 0, legs[leg].ang.yaw);  // ヨーは取付方位基準
      servos.writeJoint(leg, 1, legs[leg].ang.pitch);
      servos.writeJoint(leg, 2, legs[leg].ang.knee);
    }
    // CH_HEAD: 旋回操作に連動して駆動 (物理対象は未確定 — docs/wiring.md「頭部ヨー (CH_HEAD) の物理対象」参照。頭部シェル自体は完全固定)
    servos.writeDeg(CH_HEAD, wz * 25.0f);
  }

  const bool walking = fabsf(vx) + fabsf(vy) + fabsf(wz) > 0.05f;
  if (standAllowed && servos.ready()) {
    // 前脚(FR/FL)×腕 連成クランプ用 (arms.h ARM_LEG_YAW_GATE_DEG 参照)。
    // 同じ standAllowed ブロックで直前に更新された legs[FR]/legs[FL] を使う
    const JointAngles legAng[2] = {legs[FR].ang, legs[FL].ang};
    arms.update(dt, servos, walking, gait.phase(), cmd_h, legAng);
    eyes.update(dt, servos, vy, wz);  // vy=前後 (進行方向バイアス)
  }
  peri.setLedMode(!cmd_led ? LED_OFF : (walking ? LED_ACTIVE : LED_IDLE));
  peri.tick(walking);
#endif

  if (millis() - lastTelem > 500) {
    lastTelem = millis();
    JsonDocument doc;
    doc["vbat"] = peri.vbat();
    doc["low"] = peri.lowBattery();
    doc["cut"] = peri.cutout();
    String out;
    serializeJson(doc, out);
    ws.textAll(out);
    ws.cleanupClients();
  }
}
