#pragma once
#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPAsyncWebServer.h>
#include <driver/i2s.h>
#include <string.h>

#include "config.h"

// I2S0 全二重ドライバ (INMP441 マイク + MAX98357A アンプ, 16kHz/16bit/mono)。
// - 録音: setPtt(true) 中、専用タスクが I2S から読んだ PCM を /audio WS の
//   全接続クライアント (ブリッジ) へバイナリ送出する。
// - 再生: /audio WS で受けたバイナリ PCM をリングバッファへ積み、同じ
//   専用タスクが I2S へ書き出す。
// - 半二重運用: tts_begin〜tts_end の間 (playing_) は録音を行わない
//   (マイクがスピーカー音を拾うエコーの回避。AEC 無し)。
// - 制御プロトコル (テキストフレーム, JSON):
//     firmware → ブリッジ: {"type":"ptt_start"} / {"type":"ptt_end"}
//     ブリッジ → firmware: {"type":"tts_begin"}  / {"type":"tts_end"}
// - 全処理を core0 の専用タスクで行い、gait 制御 (core1, main loop 50Hz) を
//   阻害しない。ring バッファは単一 producer (/audio WSハンドラ, AsyncTCPタスク)
//   / 単一 consumer (audioTask) の lock-free SPSC (head/tail は 32bit で
//   Xtensa 上アトミック。Servos のトリム管理と同じ volatile 方式)。
class Audio {
 public:
  void begin(AsyncWebSocket* ws) {
    ws_ = ws;

    i2s_config_t cfg = {};
    cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_RX);
    cfg.sample_rate = AUDIO_SAMPLE_RATE;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
    // mono。TX(MAX98357A)/RX(INMP441) 共有設定のため単純に ALL_LEFT へは
    // 差し替えられない (RX 側の読み出し互換性が未検証)。ONLY_LEFT は
    // MAX98357A の SD ピン結線 (GND/VDD/フローティング) によって音量が
    // 半分または無音になり得るため、実機組立時に SD ピン結線を確認し、
    // 音量異常があれば RX 側の互換性検証込みで見直すこと (UNVERIFIED)
    cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = 6;
    cfg.dma_buf_len = 256;
    cfg.use_apll = false;
    cfg.tx_desc_auto_clear = true;  // 再生データ欠乏時にノイズでなく無音を出す
    i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr);

    i2s_pin_config_t pins = {};
    pins.mck_io_num = I2S_PIN_NO_CHANGE;
    pins.bck_io_num = PIN_I2S_BCLK;
    pins.ws_io_num = PIN_I2S_WS;
    pins.data_out_num = PIN_I2S_DOUT;
    pins.data_in_num = PIN_I2S_DIN;
    i2s_set_pin(I2S_NUM_0, &pins);
    i2s_zero_dma_buffer(I2S_NUM_0);

    xTaskCreatePinnedToCore(taskEntry, "audio", 4096, this, 5, nullptr, 0);
  }

  // main の操作系 /ws から呼ぶ (PTT ボタンの edge)。ブリッジへ
  // ptt_start/ptt_end を通知し、録音タスクを起動/停止する
  void setPtt(bool active) {
    if (active == pttActive_) return;
    pttActive_ = active;
    if (ws_) ws_->textAll(active ? "{\"type\":\"ptt_start\"}"
                                  : "{\"type\":\"ptt_end\"}");
  }

  // /audio WS のイベントハンドラから呼ぶ (main.cpp)
  void onEvent(AsyncWebSocket* server, AsyncWebSocketClient* client,
               AwsEventType type, void* arg, uint8_t* data, size_t len) {
    if (type == WS_EVT_CONNECT) {
      // 単一ブリッジ接続を前提とした設計。2 台目以降が繋がると再生/録音の
      // フレームが両方へ混線するため、既存接続がある場合は新規接続を拒否する
      if (server && server->count() > 1) {
        if (client) client->close();
        return;
      }
      // 切断・再接続時は再生状態をリセットし、古い音声が漏れないようにする
      resetPlayback();
      // 録音中 (pttActive_) にブリッジ側の回線 (iPhone テザリング等) が
      // 瞬断して再接続した場合、新しい接続には ptt_start が一度も送られて
      // いない。ブリッジのプロトコル状態機械が ptt_start を録音セッション
      // の起点として期待しているため、再接続直後に送り直して同期する
      if (pttActive_ && client) client->text("{\"type\":\"ptt_start\"}");
      return;
    }
    if (type == WS_EVT_DISCONNECT) {
      // 切断時は再生状態をリセットし、古い音声が漏れないようにする
      resetPlayback();
      return;
    }
    if (type != WS_EVT_DATA) return;
    AwsFrameInfo* info = (AwsFrameInfo*)arg;
    // 分割フレームは扱わない (制御 JSON も PCM チャンクも単一フレームに収まる)
    if (!info->final || info->index != 0 || info->len != len) return;
    if (info->opcode == WS_TEXT) {
      handleControl((const char*)data, len);
    } else if (info->opcode == WS_BINARY) {
      pushPlayback(data, len);
    }
  }

 private:
  static constexpr size_t CHUNK_BYTES = 640;    // 20ms @16kHz/16bit/mono
  static constexpr size_t RING_BYTES = 16384;   // 再生バッファ ~0.5s 分

  static void taskEntry(void* p) { static_cast<Audio*>(p)->run(); }

  void run() {
    uint8_t buf[CHUNK_BYTES];
    for (;;) {
      if (playing_) {
        const size_t n = popPlayback(buf, sizeof(buf));
        if (n > 0) {
          size_t written = 0;
          i2s_write(I2S_NUM_0, buf, n, &written, pdMS_TO_TICKS(50));
        } else if (draining_) {
          // tts_end 後、バッファが空になったら再生モードを抜けて録音を許可する
          draining_ = false;
          playing_ = false;
          i2s_zero_dma_buffer(I2S_NUM_0);
        } else {
          vTaskDelay(pdMS_TO_TICKS(5));
        }
      } else if (pttActive_) {
        size_t bytesRead = 0;
        const esp_err_t err =
            i2s_read(I2S_NUM_0, buf, sizeof(buf), &bytesRead, pdMS_TO_TICKS(50));
        if (err == ESP_OK && bytesRead > 0 && ws_) ws_->binaryAll(buf, bytesRead);
      } else {
        vTaskDelay(pdMS_TO_TICKS(10));
      }
    }
  }

  void handleControl(const char* data, size_t len) {
    JsonDocument doc;
    if (deserializeJson(doc, data, len)) return;
    const char* t = doc["type"] | "";
    if (!strcmp(t, "tts_begin")) {
      flushRing();
      draining_ = false;
      playing_ = true;   // 再生中は録音タスク側が自動的に止まる (run() 参照)
    } else if (!strcmp(t, "tts_end")) {
      draining_ = true;  // バッファが空になるまで再生継続し、その後 playing_=false
    }
  }

  void resetPlayback() {
    playing_ = false;
    draining_ = false;
    flushRing();
    i2s_zero_dma_buffer(I2S_NUM_0);
  }

  // ---- リングバッファ (producer: onEvent/AsyncTCPタスク, consumer: audioTask)
  void flushRing() { ringHead_ = ringTail_ = 0; overflowNotified_ = false; }

  size_t ringUsed() const {
    const size_t h = ringHead_, t = ringTail_;
    return h >= t ? h - t : RING_BYTES - t + h;
  }

  void pushPlayback(const uint8_t* data, size_t len) {
    const size_t freeBytes = RING_BYTES - 1 - ringUsed();
    if (len > freeBytes) {
      len = freeBytes;  // 溢れは切り捨て (次チャンクで追従)
      // ブリッジがリアルタイム再生ペースより速く送った場合の可聴ドロップ。
      // フロー制御は無いため、最低限ブリッジへ知らせて調整を促す
      // (連続オーバーフロー中は1回だけ通知し、洪水を避ける)
      if (!overflowNotified_) {
        overflowNotified_ = true;
        Serial.println("[audio] playback ring overflow, dropping tail");
        if (ws_) ws_->textAll("{\"type\":\"overflow\"}");
      }
    } else {
      overflowNotified_ = false;
    }
    size_t h = ringHead_;
    for (size_t i = 0; i < len; i++) {
      ring_[h] = data[i];
      h = (h + 1) % RING_BYTES;
    }
    ringHead_ = h;
  }

  size_t popPlayback(uint8_t* out, size_t maxLen) {
    const size_t n = min(maxLen, ringUsed());
    size_t t = ringTail_;
    for (size_t i = 0; i < n; i++) {
      out[i] = ring_[t];
      t = (t + 1) % RING_BYTES;
    }
    ringTail_ = t;
    return n;
  }

  AsyncWebSocket* ws_ = nullptr;
  volatile bool pttActive_ = false;
  volatile bool playing_ = false;
  volatile bool draining_ = false;
  // producer (onEvent/AsyncTCPタスク) 側のみが読み書きするため volatile 不要
  bool overflowNotified_ = false;
  uint8_t ring_[RING_BYTES];
  volatile size_t ringHead_ = 0, ringTail_ = 0;
};
