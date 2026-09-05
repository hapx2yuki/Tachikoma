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
#include <esp_task_wdt.h>

#include "arms.h"
#include "audio.h"
#include "config.h"
#include "control.h"
#include "eyes.h"
#include "gait.h"
#include "leg_output.h"
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

// AsyncTCP (core0) は指令だけ更新し、運動状態は loop (core1) だけが変更する。
// volatile / 構造体代入だけでは複数フィールドの同時更新にはならない。
ControlState pendingControl;
uint32_t controlClientId = 0;
portMUX_TYPE controlMux = portMUX_INITIALIZER_UNLOCKED;
#ifdef CALIBRATION_MODE
volatile int cal_us = 1500;
#endif

// STA (iPhone テザリング) 資格情報は NVS 保存 (setupWiFi() と /wifi POST
// ハンドラ参照)。ハードコード禁止 — SSID/パスワードは Web UI の設定タブから入力する
String staSsid;

void onWsEvent(AsyncWebSocket*, AsyncWebSocketClient* client, AwsEventType type,
               void* arg, uint8_t* data, size_t len) {
  if (type == WS_EVT_DISCONNECT) {
    portENTER_CRITICAL(&controlMux);
    if (client && client->id() == controlClientId) {
      pendingControl.vx = pendingControl.vy = pendingControl.wz = 0;
      pendingControl.ptt = false;
      controlClientId = 0;
    }
    portEXIT_CRITICAL(&controlMux);
    return;
  }
  if (type != WS_EVT_DATA) return;
  // 分割フレームは扱わない (UI の送信サイズなら単一フレームで収まる)
  AwsFrameInfo* info = (AwsFrameInfo*)arg;
  if (!info->final || info->index != 0 || info->len != len ||
      info->opcode != WS_TEXT) return;
  if (len > 512) return;  // UI の指令は数十バイト。異常フレームは捨てる (F-09)
  JsonDocument doc;
  if (deserializeJson(doc, data, len)) return;
  portENTER_CRITICAL(&controlMux);
  if (updateControlFromJson(doc, pendingControl, millis()) && client &&
      !doc["vx"].isNull() && !doc["vy"].isNull() && !doc["wz"].isNull())
    controlClientId = client->id();
  portEXIT_CRITICAL(&controlMux);
}

void onAudioWsEvent(AsyncWebSocket* s, AsyncWebSocketClient* c, AwsEventType type,
                     void* arg, uint8_t* data, size_t len) {
  audio.onEvent(s, c, type, arg, data, len);
}

// AP は常時維持 (操作UIのフォールバック)。STA は NVS 保存の資格情報が
// あれば iPhone テザリングへ join を試みる (資格情報はソースに置かない)
void setupWiFi() {
  WiFi.mode(WIFI_AP_STA);
  WiFi.setAutoReconnect(true);  // テザリング切断後の STA 再接続 (F-10)
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
    portENTER_CRITICAL(&controlMux);
    if (m == "kyoro") pendingControl.eyeMode = Eyes::KYORO;
    else if (m == "front") pendingControl.eyeMode = Eyes::FRONT;
    else if (m == "scan") pendingControl.eyeMode = Eyes::SCAN;
    portEXIT_CRITICAL(&controlMux);
    r->send(200, "text/plain", "ok");
  });
  server.on("/arm", HTTP_GET, [](AsyncWebServerRequest* r) {
    // プリセット: /arm?pose=tuck|ready|reach|wave
    const String p = r->hasParam("pose") ? r->getParam("pose")->value() : "";
    const float* pose = p == "tuck" ? ARM_POSE_TUCK :
                        p == "ready" ? ARM_POSE_READY :
                        p == "reach" ? ARM_POSE_REACH : nullptr;
    portENTER_CRITICAL(&controlMux);
    if (pose) {
      pendingControl.arm.yaw = pose[0]; pendingControl.arm.pitch = pose[1];
      pendingControl.arm.elbow = pose[2]; pendingControl.armAction = 1;
    } else if (p == "wave") pendingControl.armAction = 2;
    portEXIT_CRITICAL(&controlMux);
    r->send(200, "text/plain", "ok");
  });
#ifdef CALIBRATION_MODE
  // 可動端確認: /cal?us=500 / 2500 / 1500。180° 品なら 1500→500 で -90°、
  // 1500→2500 で +90° 振れる (270° 品は ±135°)。応答は現在値
  server.on("/cal", HTTP_GET, [](AsyncWebServerRequest* r) {
    if (r->hasParam("us")) {
      int us;
      if (!parseControlInteger(r->getParam("us")->value().c_str(), US_MIN, US_MAX, us)) {
        r->send(400, "text/plain", "us must be an integer within servo pulse limits");
        return;
      }
      cal_us = us;
    }
    r->send(200, "text/plain", String(cal_us));
  });
#endif
  server.on("/trim", HTTP_GET, [](AsyncWebServerRequest* r) {
    if (r->hasParam("ch") && r->hasParam("us")) {
      int channel, trim;
      if (!parseControlInteger(r->getParam("ch")->value().c_str(), 0, N_CH - 1, channel) ||
          !parseControlInteger(r->getParam("us")->value().c_str(), -200, 200, trim)) {
        r->send(400, "text/plain", "invalid channel or trim");
        return;
      }
      servos.setTrim(channel, trim);
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
  #ifdef CALIBRATION_MODE
  servos.enableAll();
#endif
  if (!servos.i2cOk(0) || !servos.i2cOk(1))
    Serial.printf("!! PCA9685 not responding: board0=%d board1=%d (check wiring / A0 jumper)\n",
                  servos.i2cOk(0), servos.i2cOk(1));
  // タスク WDT: loop が 3 秒止まったら再起動。再起動完了までは PCA に旧 PWM が残る。
  esp_task_wdt_init(3, true);
  esp_task_wdt_add(NULL);
  peri.queueTrack(1);  // 起動音 (SD にあれば, loop 側で再生)
  Serial.println("Tachikoma ready: http://192.168.4.1/ / http://tachikoma.local/");
}

void loop() {
  static uint32_t lastUs = micros();
  static uint32_t lastTelem = 0;
  esp_task_wdt_reset();
  const uint32_t nowUs = micros();
  float dt = (nowUs - lastUs) * 1e-6f;
  if (dt < 0.02f) return;  // 50Hz 制御
  lastUs = nowUs;
  // 1 周期が伸びても (NVS 書込・I2C 詰まり) 位相を一気に進めない (F-03)
  if (dt > 0.05f) dt = 0.05f;

  portENTER_CRITICAL(&controlMux);
  // 期限切れ値をメールボックスからも消す。millis の周回で古い速度を復活させない。
  if (millis() - pendingControl.lastCmdMs > 1500) {
    pendingControl.vx = pendingControl.vy = pendingControl.wz = 0;
    pendingControl.ptt = false;
  }
  const ControlState control = pendingControl;
  pendingControl.armAction = 0;
  portEXIT_CRITICAL(&controlMux);
  arms.target[0] = control.arm;
  arms.mirror = control.mirror;
  if (control.armAction == 1) {
    const float pose[3] = {control.arm.yaw, control.arm.pitch, control.arm.elbow};
    arms.setPose(pose);
  } else if (control.armAction == 2) arms.startWave();
  eyes.mode = control.eyeMode;

  servos.serviceFault();
  servos.persistTrims();

  // 音声 PTT: 通信断 (UI 切断) で自動解除する (歩行指令と同じフェイルセーフ)。
  // 歩行制御 (CALIBRATION_MODE) と無関係に、通常運用外でも音声だけは動く
  {
    static bool pttPrev = false;
    const bool pttNow = (millis() - control.lastCmdMs > 1500) ? false : control.ptt;
    if (pttNow != pttPrev) {
      audio.setPtt(pttNow);
      pttPrev = pttNow;
    }
  }

#ifdef CALIBRATION_MODE
  peri.setLedMode(LED_IDLE);
  peri.tick(false);
  servos.calibrateUs(cal_us, peri.cutout(), control.stand);  // 低電圧時は校正パルスも停止
#else
  const bool timeout = millis() - control.lastCmdMs > 1500;  // 通信断で停止
  const float vx = timeout ? 0 : control.vx;
  const float vy = timeout ? 0 : control.vy;
  const float wz = timeout ? 0 : control.wz;

  // 低電圧カット: VBAT_CUT 未満 3 秒で脱力ラッチ (peripherals.h)
  const bool standAllowed = control.stand && !peri.cutout();

  // 起動/脱力の状態遷移
  static bool wasStanding = false;
  static LegOutput legOutput;
  static bool legCurInit = false;   // 脚スルー段の初期化フラグ (F-01)
  if (standAllowed && !wasStanding) { servos.enableAll(); legCurInit = false; arms.resetOutput(); eyes.resetOutput(); }  // 再起動: ソフトスタート
  if (!standAllowed && wasStanding) servos.disableAll();  // 脱力: パルス停止
  wasStanding = standAllowed;
  if (standAllowed) servos.softStart();

  // legs[] は下の arms.update() (脚×腕連成クランプ) でも参照するため if
  // ブロックの外側で宣言する。2つの if は同一条件 (standAllowed &&
  // servos.ready()) なので、後段が実行される時点では必ず本ブロックで
  // 更新済み — 未初期化のまま読まれることはない
  LegCmd legs[4];
  if (standAllowed && servos.ready()) {
    gait.bodyH = control.h;
    gait.update(dt, vx, vy, wz, legs);
    // 出力スルー段 (arms.h の cur_ と同じ 2 段構え)。無信号→有信号の初回は
    // 「現在角は不明」なので中立 (0°) から LEG_SLEW_DPS で目標へ寄せる (F-01/F-03)
    if (!legCurInit) { legOutput.reset(); legCurInit = true; }
    legOutput.update(dt, legs);
    for (int leg = 0; leg < 4; leg++) {
      const JointAngles& angle = legOutput.angle(leg);
      servos.writeJoint(leg, 0, angle.yaw);
      servos.writeJoint(leg, 1, angle.pitch);
      servos.writeJoint(leg, 2, angle.knee);
    }
    // CH_HEAD の機構は未確定。割当が確定するまでは未使用・full-off を維持する。
  }

  const bool walking = standAllowed && servos.ready() && gait.moving();  // gait と同じ判定 (F-04)
  if (standAllowed && servos.ready()) {
    // 前脚(FR/FL)×腕 連成クランプ用 (arms.h ARM_LEG_YAW_GATE_DEG 参照)。
    // 目標とスルー後の出力を比べ、危険側の値で退出中の退避を維持する
    const JointAngles legAng[2] = {legOutput.armGuard(0, legs),
                                 legOutput.armGuard(1, legs)};
    arms.update(dt, servos, walking, gait.phase(), control.h, legAng);
    eyes.update(dt, servos, vy, wz);  // vy=前後 (進行方向バイアス)
  }
  peri.setLedMode(!control.led ? LED_OFF : (walking ? LED_ACTIVE : LED_IDLE));
  peri.tick(walking);
#endif

  if (millis() - lastTelem > 500) {
    lastTelem = millis();
    JsonDocument doc;
    doc["vbat"] = peri.vbat();
    doc["h"] = control.h;
    doc["low"] = peri.lowBattery();
    doc["cut"] = peri.cutout();
    doc["stand"] = control.stand && !peri.cutout() && !servos.faulted();
    doc["i2c"] = servos.i2cOk(0) && servos.i2cOk(1);  // PCA9685 ×2 応答 (F-05)
    String out;
    serializeJson(doc, out);
    ws.textAll(out);
    ws.cleanupClients();
  }
}
