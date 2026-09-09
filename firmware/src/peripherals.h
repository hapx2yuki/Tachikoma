#pragma once
#include <atomic>
#include "profile_config.h"
#if TACHIKOMA_STATUS_LEDS
#include <Adafruit_NeoPixel.h>
#endif
#if TACHIKOMA_DFPLAYER
#include <DFRobotDFPlayerMini.h>
#include <HardwareSerial.h>
#endif
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
#if TACHIKOMA_STATUS_LEDS
    strip_.begin();
    strip_.setBrightness(120);
    strip_.show();
#endif
#if TACHIKOMA_DFPLAYER
    df_serial_.begin(9600, SERIAL_8N1, PIN_DF_RX, PIN_DF_TX);
    df_ok_ = df_.begin(df_serial_, /*isACK=*/false, /*doReset=*/true);
    if (df_ok_) df_.volume(22);
#endif
    analogReadResolution(12);
    analogSetPinAttenuation(PIN_VBAT, ADC_11db);
  }

  void setLedMode(LedMode m) { mode_ = m; }

  // async ハンドラから呼んでよい (キューに積むだけ)
  bool queueTrack(int n) {
    if (!DFPLAYER_ENABLED || !df_ok_ || n < 1 || n > DFPLAYER_TRACK_MAX) return false;
    pending_track_ = n;
    return true;
  }

  float vbat() const { return vbat_avg_; }
  bool lowBattery() const { return batterySeen_ && vbat_avg_ < VBAT_WARN; }
  bool cutout() const { return cut_; }
  // これは GPIO34 の VBAT 分圧線と2S電池の成立だけを確認する値であり、
  // サーボ V+ レールの通電・電流・断線は検知しない。通常PWMを許可する
  // ソフトゲートとして使うが、サーボ電源の実機合否を意味しない。
  // batterySeen_ は一度成立した後も保持するが、瞬時の範囲外サンプルで
  // vbatVerified() を即時 false にするため、断線時の数秒間を通電可能と誤認しない。
  bool vbatVerified() const { return vbatVerified_; }
  bool vbatUnverified() const { return !vbatVerified_ && !cut_; }
  const char* vbatState() const {
    if (cut_) return "CUTOUT_LATCHED";
    if (!batterySeen_) return "UNVERIFIED";
    if (!vbatVerified_) return "OUT_OF_RANGE";
    return "VERIFIED";
  }

  // PWMを出す前に呼ぶ電源サンプル。tick() からも呼ばれるが、main loop は
  // LEDや他の処理より先にこの関数を呼んで fail closed を成立させる。
  void samplePower() {
    const uint32_t t = millis();
    if (t - lastVbat_ < 100) return;
    lastVbat_ = t;
    const float v = analogReadMilliVolts(PIN_VBAT) / 1000.0f * VBAT_DIV;
    vbat_avg_ = (vbat_avg_ <= 0.1f) ? v : vbat_avg_ * 0.9f + v * 0.1f;
    const bool sampleValid = v >= VBAT_CUT && v <= VBAT_MAX_VALID;
    if (sampleValid) batterySeen_ = true;
    // 未接続・断線・USBのみ・範囲外の電圧はいずれも通常出力を許可しない。
    vbatVerified_ = batterySeen_ && sampleValid && vbat_avg_ >= VBAT_CUT &&
                    vbat_avg_ <= VBAT_MAX_VALID && !cut_;
    if (batterySeen_ && vbat_avg_ < VBAT_CUT) {
      if (belowSince_ == 0) belowSince_ = t;
      if (t - belowSince_ > 3000) cut_ = true;
    } else {
      belowSince_ = 0;
    }
    if (cut_) vbatVerified_ = false;
  }

  // loop から毎周期呼ぶ
  void tick(bool walking) {
    const uint32_t t = millis();

    // 再生キュー
#if TACHIKOMA_DFPLAYER
    const int n = pending_track_.exchange(0);
    if (df_ok_ && n > 0) {
      df_.playMp3Folder(n);  // ACK 無効なのでブロックしない
    }
#endif

    samplePower();

    // LED
#if TACHIKOMA_STATUS_LEDS
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
#else
    (void)walking;
#endif
  }

 private:
#if TACHIKOMA_STATUS_LEDS
  Adafruit_NeoPixel strip_{N_LED, PIN_LED, NEO_GRB + NEO_KHZ800};
#endif
#if TACHIKOMA_DFPLAYER
  HardwareSerial df_serial_{2};
  DFRobotDFPlayerMini df_;
#endif
  bool df_ok_ = false;
  LedMode mode_ = LED_IDLE;
  std::atomic<int> pending_track_{0};
  bool batterySeen_ = false;
  float vbat_avg_ = 0;
  uint32_t lastVbat_ = 0, lastLed_ = 0, belowSince_ = 0;
  bool cut_ = false;
  bool vbatVerified_ = false;
};
