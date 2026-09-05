#pragma once
#include <Arduino.h>
#include <atomic>
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
//   / 単一 consumer (audioTask)。リセットは両インデックスを変更するため
//   短いクリティカルセクションでコピーとリセットの競合を防ぐ。
class Audio {
 public:
  void begin(AsyncWebSocket* ws) {
    ws_ = ws;

    i2s_config_t cfg = {};
    cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX | I2S_MODE_RX);
    cfg.sample_rate = AUDIO_SAMPLE_RATE;
    cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
    // INMP441 は WS 1 周期につき 64 SCK 必須 (TDK datasheet p11)。
    // ESP32 の 16bit mono FIFO 並び替えを避け、DMA も 32bit に統一。
    // WebSocket の PCM16 との変換は step() で明示する。
    cfg.bits_per_chan = I2S_BITS_PER_CHAN_32BIT;
    // TX/RX とも左 mono。MAX98357A は配線した SD の左右選択を実機確認する。
    // https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/peripherals/i2s.html
    cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
    cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
    cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
    cfg.dma_buf_count = DMA_COUNT;
    cfg.dma_buf_len = DMA_FRAMES;
    cfg.use_apll = false;
    cfg.tx_desc_auto_clear = true;  // 再生データ欠乏時にノイズでなく無音を出す
    if (i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr) != ESP_OK) {
      Serial.println("[audio] I2S driver initialization failed");
      return;
    }

    i2s_pin_config_t pins = {};
    pins.mck_io_num = I2S_PIN_NO_CHANGE;
    pins.bck_io_num = PIN_I2S_BCLK;
    pins.ws_io_num = PIN_I2S_WS;
    pins.data_out_num = PIN_I2S_DOUT;
    pins.data_in_num = PIN_I2S_DIN;
    if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) {
      Serial.println("[audio] I2S pin initialization failed");
      i2s_driver_uninstall(I2S_NUM_0);
      return;
    }
    i2s_zero_dma_buffer(I2S_NUM_0);

    if (xTaskCreatePinnedToCore(taskEntry, "audio", 4096, this, 5, nullptr, 0) != pdPASS) {
      Serial.println("[audio] audio task creation failed");
      i2s_driver_uninstall(I2S_NUM_0);
    }
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
      if (bridgeId_ != 0) {
        if (client) client->close();
        return;
      }
      bridgeId_ = client ? client->id() : 0;
      // 切断・再接続時は再生状態をリセットし、古い音声が漏れないようにする
      resetPlayback();
      // 録音中 (pttActive_) にブリッジ側の回線 (iPhone テザリング等) が
      // 瞬断して再接続した場合、新しい接続には ptt_start が一度も送られて
      // いない。ブリッジのプロトコル状態機械が ptt_start を録音セッション
      // の起点として期待しているため、再接続直後に送り直して同期する
      if (pttActive_ && client) client->text("{\"type\":\"ptt_start\"}");
      return;
    }
    // 拒否した 2 本目の切断を、正規ブリッジの切断と取り違えない。
    if (!client || client->id() != bridgeId_) return;
    if (type == WS_EVT_DISCONNECT) {
      bridgeId_ = 0;
      // 切断時は再生状態をリセットし、古い音声が漏れないようにする
      resetPlayback();
      return;
    }
    if (type != WS_EVT_DATA) return;
    AwsFrameInfo* info = (AwsFrameInfo*)arg;
    // 分割フレームは扱わない (制御 JSON も PCM チャンクも単一フレームに収まる)
    if (!info->final || info->index != 0 || info->len != len) return;
    if (info->opcode == WS_TEXT && len <= 256) {
      handleControl((const char*)data, len);
    } else if (info->opcode == WS_BINARY) {
      pushPlayback(data, len);
    }
  }

 private:
  static constexpr int DMA_COUNT = 6, DMA_FRAMES = 256;
  static constexpr uint32_t DMA_DRAIN_MS =
      (DMA_COUNT + 1) * DMA_FRAMES * 1000 / AUDIO_SAMPLE_RATE + 1;
  static constexpr size_t CHUNK_BYTES = 640;    // 20ms @16kHz/16bit/mono
  static constexpr size_t RING_BYTES = 16384;   // 再生バッファ ~0.5s 分

  static void taskEntry(void* p) { static_cast<Audio*>(p)->run(); }

  void run() { for (;;) step(); }

  // 1 回の I2S 処理。未送信分を保持し、短い書込/timeout でも PCM を捨てない。
  void step() {
    uint32_t dma[CHUNK_BYTES / 2];  // DMA: PCM32、通信とリング: PCM16
    portENTER_CRITICAL(&ringMux_);
    const uint32_t currentGeneration = generation_;
    portEXIT_CRITICAL(&ringMux_);
    if (clearDma_.exchange(false)) {
      i2s_zero_dma_buffer(I2S_NUM_0);
      txCount_ = txOffset_ = 0;
    }
    if (playing_) {
      if (txOffset_ == txCount_) {
        txGeneration_ = currentGeneration;
        txCount_ = popPlayback(tx_, sizeof(tx_), txGeneration_);
        txOffset_ = 0;
        txProgressMs_ = millis();
      }
      if (txOffset_ < txCount_) {
        portENTER_CRITICAL(&ringMux_);
        const bool stale = generation_ != txGeneration_;
        portEXIT_CRITICAL(&ringMux_);
        if (stale) return;  // 次の周期で新セッションの DMA reset を実施
        size_t written = 0;
        const size_t remaining = txCount_ - txOffset_;
        for (size_t i = 0; i < remaining / 2; ++i) {
          const uint16_t sample = (uint16_t)tx_[txOffset_ + 2*i] |
                                   ((uint16_t)tx_[txOffset_ + 2*i+1] << 8);
          dma[i] = (uint32_t)sample << 16;  // 符号付き値の左シフトは使わない
        }
        const esp_err_t err = i2s_write(I2S_NUM_0, dma, remaining * 2,
                                       &written, pdMS_TO_TICKS(50));
        if (written > remaining * 2 || (written & 3) ||
            (err != ESP_OK && err != ESP_ERR_TIMEOUT)) {
          resetPlayback(txGeneration_);
        } else {
          txOffset_ += written / 2;
          if (written) lastWriteMs_ = txProgressMs_ = millis();
          if (!written && millis() - txProgressMs_ >= 1000) resetPlayback(txGeneration_);
          if (!written) vTaskDelay(pdMS_TO_TICKS(5));
        }
      } else {
        portENTER_CRITICAL(&ringMux_);
        // tts_begin/reset と排他。古い音声の drain 完了で新しい再生を消さない。
        if (generation_ == txGeneration_ && draining_ &&
            millis() - lastWriteMs_ >= DMA_DRAIN_MS) {
          draining_ = false; playing_ = false;
        }
        portEXIT_CRITICAL(&ringMux_);
        vTaskDelay(pdMS_TO_TICKS(5));
      }
      // 再生中の RX は破棄。次の発話へ古い DMA 音声を混ぜない。
      size_t discarded = 0;
      while (i2s_read(I2S_NUM_0, dma, sizeof(dma), &discarded, 0) == ESP_OK && discarded) {}
    } else {
      size_t bytesRead = 0;
      const esp_err_t err = i2s_read(I2S_NUM_0, dma, sizeof(dma), &bytesRead, pdMS_TO_TICKS(50));
      if (err == ESP_OK && bytesRead > 0 && bytesRead <= sizeof(dma) && !(bytesRead & 3) &&
          ws_ && pttActive_ && !playing_) {
        uint8_t pcm[CHUNK_BYTES];
        for (size_t i = 0; i < bytesRead / 4; ++i) {
          pcm[2*i] = (uint8_t)(dma[i] >> 16);
          pcm[2*i+1] = (uint8_t)(dma[i] >> 24);
        }
        ws_->binaryAll(pcm, bytesRead / 2);
      }
      if (err != ESP_OK) vTaskDelay(pdMS_TO_TICKS(5));
    }
  }

  void handleControl(const char* data, size_t len) {
    JsonDocument doc;
    if (deserializeJson(doc, data, len)) return;
    const char* t = doc["type"] | "";
    if (!strcmp(t, "tts_begin")) {
      portENTER_CRITICAL(&ringMux_);
      ringHead_ = ringTail_ = 0; overflowNotified_ = false;
      ++generation_;
      clearDma_ = true;
      draining_ = false;
      playing_ = true;   // 再生中は録音タスク側が自動的に止まる (run() 参照)
      portEXIT_CRITICAL(&ringMux_);
    } else if (!strcmp(t, "tts_end")) {
      portENTER_CRITICAL(&ringMux_);
      if (playing_) draining_ = true;  // バッファが空になるまで再生継続し、その後 playing_=false
      portEXIT_CRITICAL(&ringMux_);
    }
  }

  void resetPlayback(uint32_t expected = UINT32_MAX) {
    portENTER_CRITICAL(&ringMux_);
    if (expected != UINT32_MAX && expected != generation_) {
      portEXIT_CRITICAL(&ringMux_); return;
    }
    playing_ = false; draining_ = false;
    ringHead_ = ringTail_ = 0; overflowNotified_ = false;
    ++generation_;
    clearDma_ = true;  // I2S 呼出しは audioTask だけが所有する
    portEXIT_CRITICAL(&ringMux_);
  }

  // ---- リングバッファ (producer: onEvent/AsyncTCPタスク, consumer: audioTask)
  void flushRing() {
    portENTER_CRITICAL(&ringMux_);
    ringHead_ = ringTail_ = 0; overflowNotified_ = false;
    portEXIT_CRITICAL(&ringMux_);
  }

  // 呼出側が ringMux_ を保持する。
  size_t ringUsed() const {
    const size_t h = ringHead_, t = ringTail_;
    return h >= t ? h - t : RING_BYTES - t + h;
  }

  void pushPlayback(const uint8_t* data, size_t len) {
    if (!playing_ || draining_ || (len & 1)) return;  // PCM16 のサンプル境界
    bool notify = false;
    portENTER_CRITICAL(&ringMux_);
    // 1 byte 空きだと溢れ時に奇数 byte だけ残り、以後全 PCM が byte ずれする。
    const size_t freeBytes = RING_BYTES - 2 - ringUsed();
    if (len > freeBytes) {
      len = freeBytes;
      notify = !overflowNotified_;
      overflowNotified_ = true;
    } else overflowNotified_ = false;
    size_t h = ringHead_;
    for (size_t i = 0; i < len; i++) {
      ring_[h] = data[i]; h = (h + 1) % RING_BYTES;
    }
    ringHead_ = h;
    portEXIT_CRITICAL(&ringMux_);
    if (notify) {
      Serial.println("[audio] playback ring overflow, dropping whole samples");
      if (ws_) ws_->textAll("{\"type\":\"overflow\"}");
    }
  }

  size_t popPlayback(uint8_t* out, size_t maxLen, uint32_t expected = UINT32_MAX) {
    portENTER_CRITICAL(&ringMux_);
    if (expected != UINT32_MAX && expected != generation_) {
      portEXIT_CRITICAL(&ringMux_); return 0;
    }
    const size_t n = min(maxLen & ~size_t(1), ringUsed());
    size_t t = ringTail_;
    for (size_t i = 0; i < n; i++) {
      out[i] = ring_[t]; t = (t + 1) % RING_BYTES;
    }
    ringTail_ = t;
    portEXIT_CRITICAL(&ringMux_);
    return n;
  }

  AsyncWebSocket* ws_ = nullptr;
  std::atomic<bool> pttActive_{false};
  std::atomic<bool> playing_{false};
  std::atomic<bool> draining_{false};
  // producer (onEvent/AsyncTCPタスク) 側のみが読み書きするため volatile 不要
  bool overflowNotified_ = false;
  uint32_t bridgeId_ = 0;
  std::atomic<bool> clearDma_{false};
  portMUX_TYPE ringMux_ = portMUX_INITIALIZER_UNLOCKED;
  uint8_t ring_[RING_BYTES];
  size_t ringHead_ = 0, ringTail_ = 0;
  uint32_t generation_ = 0, txGeneration_ = 0, lastWriteMs_ = 0, txProgressMs_ = 0;
  uint8_t tx_[CHUNK_BYTES];  // audioTask だけが所有する書込途中の PCM
  size_t txCount_ = 0, txOffset_ = 0;
};
