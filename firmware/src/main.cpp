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
volatile int cal_ch = -1;
#endif

// STA (iPhone テザリング) 資格情報は NVS 保存 (setupWiFi() と /wifi POST
// ハンドラ参照)。ハードコード禁止 — SSID/パスワードは Web UI の設定タブから入力する
String staSsid;
bool wifiApReady = false;
bool wifiStaConfigured = false;
String wifiStaState = "UNCONFIGURED";

bool isPlaceholderCredential(const String& value) {
  const char* candidates[] = {
      "change-me-8chars", "change-me", "password", "password123",
      "your-password", "your_password", "YOUR_PASSWORD"};
  for (const char* candidate : candidates)
    if (value == candidate) return true;
  return false;
}

bool validWifiCredentials(const String& ssid, const String& pass) {
  return ssid.length() >= 1 && ssid.length() <= 32 && pass.length() >= 8 &&
         pass.length() <= 63 && !isPlaceholderCredential(ssid) &&
         !isPlaceholderCredential(pass);
}

void refreshWifiState() {
  if (!wifiStaConfigured) return;
  switch (WiFi.status()) {
    case WL_CONNECTED: wifiStaState = "CONNECTED"; break;
    case WL_NO_SSID_AVAIL: wifiStaState = "SSID_NOT_FOUND"; break;
    case WL_CONNECT_FAILED: wifiStaState = "CONNECT_FAILED"; break;
    case WL_CONNECTION_LOST: wifiStaState = "CONNECTION_LOST"; break;
    case WL_DISCONNECTED: wifiStaState = "DISCONNECTED"; break;
    default: wifiStaState = "CONNECTING"; break;
  }
}

int servoCommandStatus() {
  if (servos.faulted() || !servos.i2cOk(0) || !servos.i2cOk(1) ||
      !peri.vbatVerified() || !servos.powerReady())
    return 503;
  if (!servos.ready()) return 409;
  return 200;
}

// HTTP は WebSocket の連続制御と同じ所有権を持たせる。active な WS が
// ある間は別クライアントの一発操作を拒否し、所有者がいないときだけ
// confirm=1 という明示操作を要求する。これは暗号認証ではなく、ローカル
// AP 上の誤クリック・別タブ競合を防ぐための安全確認である。
bool httpMutationConfirmed(AsyncWebServerRequest* r) {
  if (r->hasParam("confirm") && r->getParam("confirm")->value() == "1") return true;
  return r->hasParam("confirm", true) &&
         r->getParam("confirm", true)->value() == "1";
}

bool httpOwnerSessionMatches(AsyncWebServerRequest* r, uint32_t ownerId) {
  for (const bool post : {false, true}) {
    if (!r->hasParam("session", post)) continue;
    int session = 0;
    if (parseControlInteger(r->getParam("session", post)->value().c_str(),
                             1, 2147483647, session) &&
        static_cast<uint32_t>(session) == ownerId)
      return true;
  }
  return false;
}

bool httpMutationAllowed(AsyncWebServerRequest* r) {
  uint32_t activeOwnerId = 0;
  portENTER_CRITICAL(&controlMux);
  if (controlClientId != 0) {
    if (millis() - pendingControl.lastCmdMs <= CONTROL_LEASE_MS) {
      activeOwnerId = controlClientId;
    } else {
      // loop がまだ期限切れ処理をしていない場合も、HTTP 側で古い所有権を
      // 生かさない。速度/PTT/stand は次の loop で停止処理を通る。
      pendingControl.vx = pendingControl.vy = pendingControl.wz = 0;
      pendingControl.ptt = false;
      pendingControl.stand = false;
      controlClientId = 0;
    }
  }
  portEXIT_CRITICAL(&controlMux);
  if (activeOwnerId != 0) {
    // UI は WS_EVT_CONNECT で受け取った一時 session を返す。同じ制御
    // クライアントだけを通し、別タブ/別端末の HTTP は confirm を付けても拒否する。
    if (httpOwnerSessionMatches(r, activeOwnerId)) return true;
    r->send(409, "text/plain", "control is owned by an active WebSocket client");
    return false;
  }
  if (!httpMutationConfirmed(r)) {
    r->send(409, "text/plain", "confirm=1 required when no WebSocket owner is active");
    return false;
  }
  return true;
}

// 搭載設定を操作画面へ返し、未搭載の機能を成功表示で隠さない。
void addFeatureTelemetry(JsonDocument& doc) {
  doc["profile"] = HARDWARE_PROFILE;
  doc["features"]["status_leds"] = STATUS_LEDS_ENABLED;
  doc["features"]["dfplayer"] = DFPLAYER_ENABLED;
  doc["features"]["i2s_audio"] = audio.ready();
  doc["features"]["i2s_state"] = audio.stateName();
  doc["features"]["audio_volume_percent"] = TACHIKOMA_AUDIO_VOLUME_PERCENT;
  doc["features"]["body_h_min_mm"] = TK_CONTROL_BODY_H_MIN;
  doc["features"]["body_h_max_mm"] = TK_CONTROL_BODY_H_MAX;
  doc["features"]["body_h_default_mm"] = TK_CONTROL_BODY_H_DEFAULT;
  doc["features"]["print_first_profile"] = PRINT_FIRST_PROFILE_ENABLED;
#if TACHIKOMA_PRINT_FIRST_PROFILE
  doc["features"]["print_first_profile_status"] = PRINT_FIRST_PROFILE_STATUS;
  doc["features"]["print_first_profile_adopted"] = PRINT_FIRST_PROFILE_ADOPTED;
#else
  doc["features"]["print_first_profile_status"] = "INACTIVE";
  doc["features"]["print_first_profile_adopted"] = false;
#endif
}

void onWsEvent(AsyncWebSocket*, AsyncWebSocketClient* client, AwsEventType type,
               void* arg, uint8_t* data, size_t len) {
  if (type == WS_EVT_CONNECT) {
    if (client) {
      String session = "{\"type\":\"control_session\",\"session\":";
      session += static_cast<int>(client->id());
      session += "}";
      client->text(session.c_str());
    }
    return;
  }
  if (type == WS_EVT_DISCONNECT) {
    portENTER_CRITICAL(&controlMux);
    if (client && client->id() == controlClientId) {
      pendingControl.vx = pendingControl.vy = pendingControl.wz = 0;
      pendingControl.ptt = false;
      pendingControl.stand = false;
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
  const bool heartbeat = !doc["vx"].isNull() && !doc["vy"].isNull() &&
                         !doc["wz"].isNull();
  portENTER_CRITICAL(&controlMux);
  const bool owner = client && (controlClientId == 0 || client->id() == controlClientId);
  if (!owner) {
    portEXIT_CRITICAL(&controlMux);
    if (client) client->text("{\"type\":\"control_busy\"}");
    return;
  }
  if (updateControlFromJson(doc, pendingControl, millis()) && client && heartbeat &&
      controlClientId == 0)
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
  const String apSsid(AP_SSID), apPass(AP_PASS);
  if (validWifiCredentials(apSsid, apPass)) {
    wifiApReady = WiFi.softAP(AP_SSID, AP_PASS);
    if (!wifiApReady) wifiStaState = "AP_FAILED";
  } else {
    wifiApReady = false;
    wifiStaState = "AP_UNCONFIGURED";
  }

  Preferences p;
  p.begin(STA_PREFS_NS, /*readOnly=*/true);
  staSsid = p.getString("ssid", "");
  const String staPass = p.getString("pass", "");
  p.end();
  if (validWifiCredentials(staSsid, staPass)) {
    wifiStaConfigured = true;
    wifiStaState = "CONNECTING";
    WiFi.begin(staSsid.c_str(), staPass.c_str());
  } else {
    wifiStaConfigured = false;
    if (staSsid.length()) wifiStaState = "STA_CREDENTIALS_INVALID";
  }

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
    if (!r->hasParam("n")) {
      r->send(400, "text/plain", "track number required");
      return;
    }
    int track = 0;
    if (!parseControlInteger(r->getParam("n")->value().c_str(),
                             1, DFPLAYER_TRACK_MAX, track)) {
      r->send(400, "text/plain", "track number must be an integer in 1..9999");
      return;
    }
    if (!DFPLAYER_ENABLED) {
      r->send(409, "text/plain", "DFPlayer is not fitted in this profile; I2S voice remains available");
      return;
    }
    if (!httpMutationAllowed(r)) return;
    // DFPlayer の API は SD ファイルの存在を ACK しないため、200 は
    // 数値範囲内のキュー投入を意味し、mp3/NNNN.mp3 の存在は保証しない。
    if (!peri.queueTrack(track)) {
      r->send(503, "text/plain", "DFPlayer unavailable or track queue failed");
    } else {
      r->send(200, "text/plain", "ok");
    }
  });
  server.on("/eye", HTTP_GET, [](AsyncWebServerRequest* r) {
    // 視線モード: /eye?mode=kyoro|front|scan
    const String m = r->hasParam("mode") ? r->getParam("mode")->value() : "";
    int mode = -1;
    if (m == "kyoro") mode = Eyes::KYORO;
    else if (m == "front") mode = Eyes::FRONT;
    else if (m == "scan") mode = Eyes::SCAN;
    if (mode < 0) { r->send(400, "text/plain", "mode must be kyoro, front, or scan"); return; }
    if (!httpMutationAllowed(r)) return;
    const int status = servoCommandStatus();
    if (status != 200) {
      r->send(status, "text/plain", status == 409 ? "servo is at rest; explicit start required"
                                                    : "servo power or PCA is unavailable");
      return;
    }
    portENTER_CRITICAL(&controlMux);
    pendingControl.eyeMode = mode;
    portEXIT_CRITICAL(&controlMux);
    r->send(200, "text/plain", "ok");
  });
  server.on("/arm", HTTP_GET, [](AsyncWebServerRequest* r) {
    // プリセット: /arm?pose=tuck|ready|reach|wave
    const String p = r->hasParam("pose") ? r->getParam("pose")->value() : "";
    const float* pose = p == "tuck" ? ARM_POSE_TUCK :
                        p == "ready" ? ARM_POSE_READY :
                        p == "reach" ? ARM_POSE_REACH : nullptr;
    if (!pose && p != "wave") { r->send(400, "text/plain", "pose must be tuck, ready, reach, or wave"); return; }
    if (!httpMutationAllowed(r)) return;
    const int status = servoCommandStatus();
    if (status != 200) {
      r->send(status, "text/plain", status == 409 ? "servo is at rest; explicit start required"
                                                    : "servo power or PCA is unavailable");
      return;
    }
    portENTER_CRITICAL(&controlMux);
    if (pose) {
      pendingControl.arm.yaw = pose[0]; pendingControl.arm.pitch = pose[1];
      pendingControl.arm.elbow = pose[2]; pendingControl.armAction = 1;
    } else if (p == "wave") pendingControl.armAction = 2;
    portEXIT_CRITICAL(&controlMux);
    r->send(200, "text/plain", "ok");
  });
#ifdef CALIBRATION_MODE
  // 単体診断専用: /cal?ch=<使用中ch>&us=<軸別保護範囲内>。全軸同時に
  // パルスを出さず、選択した1軸以外は常時 full-off にする。
  server.on("/cal", HTTP_GET, [](AsyncWebServerRequest* r) {
    if (r->hasParam("stop") && r->getParam("stop")->value() == "1") {
      servos.stopCalibration();
      cal_ch = -1;
      r->send(200, "text/plain", "stopped");
      return;
    }
    if (!r->hasParam("ch") || !r->hasParam("us")) {
      r->send(400, "text/plain", "ch and us are required; one axis only");
      return;
    }
    if (servos.faulted() || !servos.i2cOk(0) || !servos.i2cOk(1)) {
      r->send(503, "text/plain", "PCA9685 is unavailable; calibration output is disabled");
      return;
    }
    int ch = -1, us = 0, low = 0, high = 0;
    if (!parseControlInteger(r->getParam("ch")->value().c_str(), 0, N_CH - 1, ch)) {
      r->send(400, "text/plain", "ch must be an integer in 0..31");
      return;
    }
    if (!servos.calibrationPulseRange(ch, low, high)) {
      r->send(409, "text/plain", "ch is unused or not a supported calibration axis");
      return;
    }
    if (!parseControlInteger(r->getParam("us")->value().c_str(), low, high, us)) {
      r->send(400, "text/plain", "us is outside the axis-safe pulse range");
      return;
    }
    if (!httpMutationAllowed(r)) return;
    cal_ch = ch;
    cal_us = us;
    r->send(200, "text/plain", "selected");
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
      if (!httpMutationAllowed(r)) return;
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
    refreshWifiState();
    JsonDocument doc;
    doc["ssid"] = staSsid;
    doc["connected"] = WiFi.status() == WL_CONNECTED;
    doc["ip"] = WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString() : "";
    doc["ap_ready"] = wifiApReady;
    doc["sta_configured"] = wifiStaConfigured;
    doc["state"] = wifiStaState;
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
      if (!validWifiCredentials(ssid, pass)) {
        r->send(400, "text/plain", "ssid/password is invalid or a placeholder");
        return;
      }
      if (!httpMutationAllowed(r)) return;
      Preferences p;
      p.begin(STA_PREFS_NS, false);
      p.putString("ssid", ssid);
      p.putString("pass", pass);
      p.end();
      staSsid = ssid;
      wifiStaConfigured = true;
      wifiStaState = "CONNECTING";
      WiFi.begin(ssid.c_str(), pass.c_str());
    } else {
      r->send(400, "text/plain", "ssid and pass are required");
      return;
    }
    r->send(202, "text/plain", "connection attempt started");
  });
  server.begin();
}

void setup() {
  Serial.begin(115200);
  randomSeed(esp_random());  // 目のサッカード用
  Wire.begin(PIN_SDA, PIN_SCL);
  servos.begin();
  // ADC 分圧の成立を観測するまで通常PWMは許可しない。校正ビルドの単一軸
  // 診断だけが calibrateUs(..., diagnostic=true) として別経路を持つ。
  servos.setPowerReady(false);
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
  Serial.printf("Hardware profile: %s; status LEDs=%d; DFPlayer=%d; I2S=%s; AP=%d; STA=%s\n",
                HARDWARE_PROFILE, STATUS_LEDS_ENABLED, DFPLAYER_ENABLED,
                audio.stateName(), wifiApReady, wifiStaState.c_str());
  Serial.println("Tachikoma booted: PWM stays off until VBAT is verified and start is explicit");
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

  // 電源サンプルをPWM/制御より先に更新する。範囲外の瞬時値でも次の
  // サーボ書込みを許可しない。
  peri.samplePower();
  const bool vbatVerified = peri.vbatVerified();
  servos.setPowerReady(vbatVerified);
#ifndef CALIBRATION_MODE
  if (!vbatVerified) {
    // VBAT が未成立/範囲外になった時点で、stand と WS 所有権を捨てる。
    // 復電しても以前の stand を再利用せず、クライアントの明示 heartbeat
    // + stand=1 を再度受けるまで PWM を出さない。
    portENTER_CRITICAL(&controlMux);
    pendingControl.vx = pendingControl.vy = pendingControl.wz = 0;
    pendingControl.ptt = false;
    pendingControl.stand = false;
    pendingControl.armAction = 0;
    controlClientId = 0;
    portEXIT_CRITICAL(&controlMux);
  }
#endif

  portENTER_CRITICAL(&controlMux);
  // 期限切れ値をメールボックスからも消す。millis の周回で古い速度を復活させない。
  if (millis() - pendingControl.lastCmdMs > CONTROL_LEASE_MS) {
    pendingControl.vx = pendingControl.vy = pendingControl.wz = 0;
    pendingControl.ptt = false;
    pendingControl.stand = false;
    controlClientId = 0;
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
    const bool pttRequested = (millis() - control.lastCmdMs > CONTROL_LEASE_MS) ? false : control.ptt;
    if (pttRequested != pttPrev || (pttRequested && !audio.ready())) {
      const bool accepted = audio.setPtt(pttRequested);
      pttPrev = accepted ? pttRequested : false;
    }
  }

  bool telemetryStand = false;
  bool telemetryLease = true;
#ifdef CALIBRATION_MODE
  peri.setLedMode(LED_IDLE);
  peri.tick(false);
  const bool calibrationOutput =
      servos.calibrateUs(cal_ch, cal_us, peri.cutout(), control.stand,
                         true);  // 単一軸の安全な診断だけを許可
  telemetryStand = calibrationOutput && cal_ch >= 0 && !peri.cutout();
#else
  const bool timeout = millis() - control.lastCmdMs > CONTROL_LEASE_MS;
  const float vx = timeout ? 0 : control.vx;
  const float vy = timeout ? 0 : control.vy;
  const float wz = timeout ? 0 : control.wz;

  // 低電圧カット: VBAT_CUT 未満 3 秒で脱力ラッチ (peripherals.h)
  const bool standAllowed = control.stand && !timeout && vbatVerified && !peri.cutout();
  telemetryStand = standAllowed && servos.ready();
  telemetryLease = !timeout;

  // 起動/脱力の状態遷移
  static bool wasStanding = false;
  static LegOutput legOutput;
  static bool legCurInit = false;   // 脚スルー段の初期化フラグ (F-01)
  if (!standAllowed) gait.stop();
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
    refreshWifiState();
    JsonDocument doc;
    addFeatureTelemetry(doc);
    doc["vbat"] = peri.vbat();
    doc["h"] = control.h;
    doc["low"] = peri.lowBattery();
    doc["cut"] = peri.cutout();
    doc["stand"] = telemetryStand;
    doc["lease_valid"] = telemetryLease;
    doc["vbat_verified"] = peri.vbatVerified();
    doc["vbat_state"] = peri.vbatState();
    // PCA V+ / servo rail に電圧センサを設けていないため、ここは常に
    // UNVERIFIED。VBAT の検知をサーボ V+ の通電確認として表示しない。
    doc["servo_rail_verified"] = false;
    doc["servo_rail_state"] = "UNVERIFIED_NO_SENSOR";
    doc["wifi_ap_ready"] = wifiApReady;
    doc["wifi_sta_state"] = wifiStaState;
    doc["i2c"] = servos.i2cOk(0) && servos.i2cOk(1);  // PCA9685 ×2 応答 (F-05)
    String out;
    serializeJson(doc, out);
    ws.textAll(out);
    ws.cleanupClients();
  }
}
