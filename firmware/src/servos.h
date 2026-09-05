#pragma once
#include <Adafruit_PWMServoDriver.h>
#include <Preferences.h>
#include <Wire.h>
#include "config.h"

// PCA9685 ×2 ラッパ: 角度(deg)→パルス、トリム管理、ソフトスタート。
// グローバル ch 0-31 (board = ch/16, ローカル = ch%16)。
// トリムの NVS 保存はライトビハインド (setTrim は RAM のみ、persistTrims が
// 静穏 2 秒後にまとめて書く)。
class Servos {
 public:
  void begin() {
    Wire.setTimeOut(5);  // バス喪失で停止処理自体が長時間詰まることを避ける
    for (int b = 0; b < 2; b++) {
      pwm_[b] = Adafruit_PWMServoDriver(PCA_ADDR[b]);
      i2cOk_[b] = pwm_[b].begin();   // PCA9685 が I2C に応答したか (F-05)
      if (i2cOk_[b]) {
        i2cOk_[b] = fullOffBoard(b);  // 周波数再設定前に残留パルスを消す
        pwm_[b].setOscillatorFrequency(25000000);
        pwm_[b].setPWMFreq(SERVO_FREQ);
        prescale_[b] = (uint8_t)(pwm_[b].getOscillatorFrequency() /
                                 (SERVO_FREQ * 4096.0f) + 0.5f - 1.0f);
        i2cOk_[b] = i2cOk_[b] && configurationOk(b);
      }
      if (!i2cOk_[b]) fault_ = true;
    }
    prefs_.begin("trim", false);
    for (int i = 0; i < N_CH; i++) {
      trim_us_[i] = constrain(prefs_.getShort(key(i).c_str(), 0), -200, 200);
      enabled_[i] = false;
      used_[i] = false;
    }
    // 使用チャンネルの登録 (softStart の対象)
    for (int leg = 0; leg < 4; leg++)
      for (int j = 0; j < 3; j++) used_[PCA_CH[leg][j]] = true;
    // CH_HEAD は機構未確定。未使用のまま full-off を維持する。
    for (int a = 0; a < 2; a++)
      for (int j = 0; j < 3; j++) used_[ARM_CH[a][j]] = true;
    used_[EYE_CH[0]] = used_[EYE_CH[2]] = true;  // 中央は固定カメラ
    disableAll();  // ESP32 だけの再起動でも PCA に残った旧パルスを消す
  }

  // 順次イネーブル (突入電流対策)。loop から繰り返し呼ぶ
  void softStart() {
    if (fault_ || started_ >= N_CH) return;
    if (millis() - lastStart_ < 100) return;
    while (started_ < N_CH && !used_[started_]) started_++;
    if (started_ < N_CH) {
      const int ch = started_++;
      enabled_[ch] = true;
      // enabled の印だけでは ready 後に全軸が同時始動してしまう。
      // 組立時に合わせる中立パルスをここで 1 軸だけ出して順次通電する。
      writeDeg(ch, 0.0f);
    }
    lastStart_ = millis();
  }
  bool ready() const {
    if (fault_) return false;
    for (int i = 0; i < N_CH; i++)
      if (used_[i] && !enabled_[i]) return false;
    return true;
  }

  void writeDeg(int ch, float deg) {
    if (ch < 0 || ch >= N_CH || !enabled_[ch] || !isfinite(deg)) return;
    float us = 1500.0f + deg * (US_MAX - US_MIN) / DEG_RANGE + trim(ch);
    us = fminf((float)US_MAX, fmaxf((float)US_MIN, us));
    writeUs(ch, (int)us);
  }

  void writeJoint(int leg, int joint, float deg) {
    writeDeg(PCA_CH[leg][joint], deg * JOINT_SIGN[leg][joint]);
  }

  void allNeutral() { allUs(1500); }

  // CALIBRATION_MODE 用: 全 ch に同一パルス幅 (トリム込み, US_MIN..US_MAX にクランプ)。
  // 500/2500 を出してホーンの振れ角を分度器で確認し、180° 品か 270° 品かを
  // 組付け前に見分ける (assembly.md §1-1, 2026-09-04 レビュー E-06)
  void allUs(int us) {
    for (int ch = 0; ch < N_CH; ch++)
      if (enabled_[ch]) {
        int v = us + trim(ch);
        v = v < US_MIN ? US_MIN : (v > US_MAX ? US_MAX : v);
        writeUs(ch, v);
      }
  }

  void calibrateUs(int us, bool cutout, bool stand = true) {
    const bool allowed = stand && !cutout;
    if (allowed && !calibrationAllowed_) enableAll();
    if (!allowed && calibrationAllowed_) disableAll();
    calibrationAllowed_ = allowed;
    if (allowed) { softStart(); allUs(us); }
  }

  void disableAll() {
    // ALL_LED_OFF_H の full-off ビットを 1 回書くだけで全 16ch を停止する。
    // 通信故障時も健全側の基板は停止し、故障側への停止命令は serviceFault で再試行。
    for (int b = 0; b < 2; ++b) {
      if (!fullOffBoard(b)) { i2cOk_[b] = false; fault_ = true; }
    }
    for (int ch = 0; ch < N_CH; ch++) enabled_[ch] = false;
    started_ = N_CH;  // softStart 再開防止 (再開は enableAll)
  }
  void enableAll() { if (!fault_) { started_ = 0; lastStart_ = millis(); } }
  bool faulted() const { return fault_; }
  void serviceFault() {
    if (millis() - lastBusCheck_ < 100) return;
    lastBusCheck_ = millis();
    if (!fault_) {
      for (int b = 0; b < 2; ++b) {
        if (!configurationOk(b)) { i2cOk_[b] = false; fault_ = true; }
      }
    }
    if (fault_) disableAll();  // 接触が復帰しても自動再通電しない。電源再投入で復旧。
  }
  bool i2cOk(int b) const { return (b == 0 || b == 1) ? i2cOk_[b] : false; }

  int trim(int ch) const {
    if (ch < 0 || ch >= N_CH) return 0;
    portENTER_CRITICAL(&trimMux_);
    const int value = trim_us_[ch];
    portEXIT_CRITICAL(&trimMux_);
    return value;
  }

  // async ハンドラから呼ばれる: RAM 更新のみ (範囲チェック必須)
  void setTrim(int ch, int us) {
    if (ch < 0 || ch >= N_CH) return;
    portENTER_CRITICAL(&trimMux_);
    trim_us_[ch] = (short)constrain(us, -200, 200);
    dirty_ = true;
    lastTrimMs_ = millis();
    portEXIT_CRITICAL(&trimMux_);
  }

  // loop から毎周期呼ぶ: 変更が 2 秒落ち着いたら NVS へまとめて保存
  void persistTrims() {
    short snapshot[N_CH];
    portENTER_CRITICAL(&trimMux_);
    if (!dirty_ || millis() - lastTrimMs_ < 2000) {
      portEXIT_CRITICAL(&trimMux_);
      return;
    }
    for (int i = 0; i < N_CH; ++i) snapshot[i] = trim_us_[i];
    dirty_ = false;  // 保存中の新しい更新は次回保存分として残す
    portEXIT_CRITICAL(&trimMux_);
    for (int i = 0; i < N_CH; i++) {
      if (prefs_.getShort(key(i).c_str(), 0) != snapshot[i])
        prefs_.putShort(key(i).c_str(), snapshot[i]);
    }
  }

 private:
  bool readRegister(int b, uint8_t reg, uint8_t& value) {
    Wire.beginTransmission(PCA_ADDR[b]);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0 || Wire.requestFrom(PCA_ADDR[b], (uint8_t)1) != 1)
      return false;
    value = Wire.read();
    return true;
  }
  bool configurationOk(int b) {
    uint8_t mode = 0, prescale = 0;
    return readRegister(b, 0x00, mode) && readRegister(b, 0xFE, prescale) &&
           (mode & 0x30) == 0x20 && prescale == prescale_[b]; // AI=1, SLEEP=0
  }
  bool fullOffBoard(int b) {
    Wire.beginTransmission(PCA_ADDR[b]);
    Wire.write((uint8_t)0xFD); // ALL_LED_OFF_H (PCA9685 データシート §7.3.3)
    Wire.write((uint8_t)0x10);
    return Wire.endTransmission() == 0;
  }
  void writeUs(int ch, int us) {
    if (fault_) return;
    const int b = ch / 16;
    // writeMicroseconds は内部の prescale 読出失敗も書込失敗も返さない。
    // 初期化・定期検査済みの値で同じ変換を行い、setPWM の ACK を検査する。
    const double usPerTick = 1000000.0 * (prescale_[b] + 1) /
                             pwm_[b].getOscillatorFrequency();
    if (pwm_[b].setPWM(ch % 16, 0, (uint16_t)(us / usPerTick)) != 0) {
      i2cOk_[b] = false; fault_ = true;
      disableAll();
    }
  }
  static String key(int i) { return String("t") + i; }
  Adafruit_PWMServoDriver pwm_[2];
  Preferences prefs_;
  mutable portMUX_TYPE trimMux_ = portMUX_INITIALIZER_UNLOCKED;
  short trim_us_[N_CH] = {0};
  bool calibrationAllowed_ = true;
  bool enabled_[N_CH] = {false};
  bool used_[N_CH] = {false};
  int started_ = 0;
  bool i2cOk_[2] = {false, false};
  bool fault_ = false;
  uint8_t prescale_[2] = {0, 0};
  uint32_t lastBusCheck_ = 0;
  uint32_t lastStart_ = 0;
  bool dirty_ = false;
  uint32_t lastTrimMs_ = 0;
};
