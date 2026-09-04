#pragma once
#include <Adafruit_PWMServoDriver.h>
#include <Preferences.h>
#include "config.h"

// PCA9685 ×2 ラッパ: 角度(deg)→パルス、トリム管理、ソフトスタート。
// グローバル ch 0-31 (board = ch/16, ローカル = ch%16)。
// トリムの NVS 保存はライトビハインド (setTrim は RAM のみ、persistTrims が
// 静穏 2 秒後にまとめて書く)。
class Servos {
 public:
  void begin() {
    for (int b = 0; b < 2; b++) {
      pwm_[b] = Adafruit_PWMServoDriver(PCA_ADDR[b]);
      i2cOk_[b] = pwm_[b].begin();   // PCA9685 が I2C に応答したか (F-05)
      pwm_[b].setOscillatorFrequency(25000000);
      pwm_[b].setPWMFreq(SERVO_FREQ);
    }
    prefs_.begin("trim", false);
    for (int i = 0; i < N_CH; i++) {
      trim_us_[i] = prefs_.getShort(key(i).c_str(), 0);
      enabled_[i] = false;
      used_[i] = false;
    }
    // 使用チャンネルの登録 (softStart の対象)
    for (int leg = 0; leg < 4; leg++)
      for (int j = 0; j < 3; j++) used_[PCA_CH[leg][j]] = true;
    used_[CH_HEAD] = true;
    for (int a = 0; a < 2; a++)
      for (int j = 0; j < 3; j++) used_[ARM_CH[a][j]] = true;
    for (int e = 0; e < 3; e++) used_[EYE_CH[e]] = true;
  }

  // 順次イネーブル (突入電流対策)。loop から繰り返し呼ぶ
  void softStart() {
    if (started_ >= N_CH) return;
    if (millis() - lastStart_ < 100) return;
    while (started_ < N_CH && !used_[started_]) started_++;
    if (started_ < N_CH) enabled_[started_++] = true;
    lastStart_ = millis();
  }
  bool ready() const {
    for (int i = 0; i < N_CH; i++)
      if (used_[i] && !enabled_[i]) return false;
    return true;
  }

  void writeDeg(int ch, float deg) {
    if (ch < 0 || ch >= N_CH || !enabled_[ch]) return;
    float us = 1500.0f + deg * (US_MAX - US_MIN) / DEG_RANGE + trim_us_[ch];
    us = fminf((float)US_MAX, fmaxf((float)US_MIN, us));
    pwm_[ch / 16].writeMicroseconds(ch % 16, (int)us);
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
        int v = us + trim_us_[ch];
        v = v < US_MIN ? US_MIN : (v > US_MAX ? US_MAX : v);
        pwm_[ch / 16].writeMicroseconds(ch % 16, v);
      }
  }

  void disableAll() {
    for (int ch = 0; ch < N_CH; ch++) pwm_[ch / 16].setPWM(ch % 16, 0, 0);
    for (int ch = 0; ch < N_CH; ch++) enabled_[ch] = false;
    started_ = N_CH;  // softStart 再開防止 (再開は enableAll)
  }
  void enableAll() { started_ = 0; }
  bool i2cOk(int b) const { return (b == 0 || b == 1) ? i2cOk_[b] : false; }

  int trim(int ch) const { return (ch >= 0 && ch < N_CH) ? trim_us_[ch] : 0; }

  // async ハンドラから呼ばれる: RAM 更新のみ (範囲チェック必須)
  void setTrim(int ch, int us) {
    if (ch < 0 || ch >= N_CH) return;
    trim_us_[ch] = (short)constrain(us, -200, 200);
    dirty_ = true;
    lastTrimMs_ = millis();
  }

  // loop から毎周期呼ぶ: 変更が 2 秒落ち着いたら NVS へまとめて保存
  void persistTrims() {
    if (!dirty_ || millis() - lastTrimMs_ < 2000) return;
    for (int i = 0; i < N_CH; i++) {
      if (prefs_.getShort(key(i).c_str(), 0) != trim_us_[i])
        prefs_.putShort(key(i).c_str(), trim_us_[i]);
    }
    dirty_ = false;
  }

 private:
  static String key(int i) { return String("t") + i; }
  Adafruit_PWMServoDriver pwm_[2];
  Preferences prefs_;
  volatile short trim_us_[N_CH] = {0};
  bool enabled_[N_CH] = {false};
  bool used_[N_CH] = {false};
  int started_ = 0;
  bool i2cOk_[2] = {false, false};
  uint32_t lastStart_ = 0;
  volatile bool dirty_ = false;
  volatile uint32_t lastTrimMs_ = 0;
};
