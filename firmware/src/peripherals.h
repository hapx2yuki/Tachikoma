#pragma once
#include <Adafruit_NeoPixel.h>
#include <atomic>
#include <DFRobotDFPlayerMini.h>
#include <HardwareSerial.h>
#include "config.h"

// LED / サウンド / バッテリー監視
// - DFPlayer は ACK 無効 (isACK=false) で初期化し、再生要求はキュー経由で
//   loop 側から発行する (async ハンドラのブロック回避)
// - バッテリーは移動平均で監視し、VBAT_CUT 未満が 3 秒続いたら cutout() が
//   true になる (main 側でサーボ脱力に使う)

enum LedMode { LED_OFF = 0, LED_IDLE, LED_ACTIVE, LED_ALERT };

class Peripherals {
 public:
  void begin() {
    strip_.begin();
    strip_.setBrightness(120);
    strip_.show();
    df_serial_.begin(9600, SERIAL_8N1, PIN_DF_RX, PIN_DF_TX);
    df_ok_ = df_.begin(df_serial_, /*isACK=*/false, /*doReset=*/true);
    if (df_ok_) df_.volume(22);
    analogReadResolution(12);
    analogSetPinAttenuation(PIN_VBAT, ADC_11db);
  }

  void setLedMode(LedMode m) { mode_ = m; }

  // async ハンドラから呼んでよい (キューに積むだけ)
  void queueTrack(int n) {
    if (n > 0) pending_track_ = n;
  }

  float vbat() const { return vbat_avg_; }
  bool lowBattery() const { return batterySeen_ && vbat_avg_ < VBAT_WARN; }
  bool cutout() const { return cut_; }

  // loop から毎周期呼ぶ
  void tick(bool walking) {
    const uint32_t t = millis();

    // 再生キュー
    const int n = pending_track_.exchange(0);
    if (df_ok_ && n > 0) {
      df_.playMp3Folder(n);  // ACK 無効なのでブロックしない
    }

    // バッテリー (10Hz 移動平均, カット判定)
    if (t - lastVbat_ >= 100) {
      lastVbat_ = t;
      const float v = analogReadMilliVolts(PIN_VBAT) / 1000.0f * VBAT_DIV;
      vbat_avg_ = (vbat_avg_ <= 0.1f) ? v : vbat_avg_ * 0.9f + v * 0.1f;
      // USB のみの校正は許容するが、一度検知した電池の 0V 喪失は無視しない。
      if (v > 3.0f) batterySeen_ = true;
      if (batterySeen_ && vbat_avg_ < VBAT_CUT) {
        if (belowSince_ == 0) belowSince_ = t;
        if (t - belowSince_ > 3000) cut_ = true;
      } else {
        belowSince_ = 0;
      }
    }

    // LED
    if (t - lastLed_ < 60) return;
    lastLed_ = t;
    const float breath = 0.5f + 0.5f * sinf(t * 0.0015f);
    uint8_t r = 0, g = 0, b = 0;
    switch ((cut_ || lowBattery()) ? LED_ALERT : mode_) {
      case LED_OFF: break;
      case LED_IDLE:   b = 60 + 80 * breath; g = 30 + 30 * breath; break;
      case LED_ACTIVE: b = 200; g = 90; break;
      case LED_ALERT:  r = (t / 300) % 2 ? 220 : 20; break;
    }
    for (int i = LED_MAIN_EYE; i < LED_RED0; i++)
      strip_.setPixelColor(i, strip_.Color(r ? r : g / 3, g, b));
    const bool blink = walking ? ((t / 250) % 2) : true;
    for (int i = LED_RED0; i < N_LED; i++)
      strip_.setPixelColor(i, blink ? strip_.Color(180, 8, 0) : 0);
    strip_.show();
  }

 private:
  Adafruit_NeoPixel strip_{N_LED, PIN_LED, NEO_GRB + NEO_KHZ800};
  HardwareSerial df_serial_{2};
  DFRobotDFPlayerMini df_;
  bool df_ok_ = false;
  LedMode mode_ = LED_IDLE;
  std::atomic<int> pending_track_{0};
  bool batterySeen_ = false;
  float vbat_avg_ = 0;
  uint32_t lastVbat_ = 0, lastLed_ = 0, belowSince_ = 0;
  bool cut_ = false;
};
