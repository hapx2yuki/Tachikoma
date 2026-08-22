#pragma once
#include <Arduino.h>

#include "config.h"
#include "servos.h"

// 目ポッドのキョロキョロ制御 (左右 2 個, SUBMICRO)。
// 2026-07-28 設計変更: 中央目 (EYE_CH[1], ch25) は固定カメラ目
// (hardware/src/make_camera.py eye_pod_camera) に置換済みでサーボを持たない
// — EYE_CH[1] は未使用として ch 番号を予約したまま残す (配線/PCA9685 の
// ch 割当を変えないため。Web UI のトリムパネルもこの ch をスキップする,
// web_ui.h 参照)。KYORO/FRONT/SCAN の全モードとも左右 2 目 (EYE_CH[0]/[2])
// だけで動作する。
// 機構: 元キットの目パーツ (白ドーム) の黒ドット群がキャップ軸から ~45°
// 偏心しており、サーボ角に応じて視線が泳ぐ。中立 (0°) = ドット群下向きで
// 組み付ける (assembly.md)。
// mode: KYORO = ランダムサッカード (既定) / FRONT = 正面固定 / SCAN = 掃引
class Eyes {
 public:
  enum Mode { KYORO = 0, FRONT = 1, SCAN = 2 };
  int mode = KYORO;

  // 50Hz で呼ぶ。歩行指令があれば進行/旋回方向へ視線をバイアスする
  // vy = 前後 (+前, ボディ座標は +Y 前。config.h / gait.h と同じ規約)
  void update(float dt, Servos& servos, float vy, float wz) {
    const int idx_of_ch[2] = {0, 2};   // 可動目のみ (EYE_CH のインデックス。1=中央は固定カメラ)
    const uint32_t now = millis();
    const float bias = constrain(vy * 30.0f + wz * 35.0f, -40.0f, 40.0f);
    for (int idx = 0; idx < 2; idx++) {
      const int i = idx_of_ch[idx];
      if (mode == FRONT) {
        tgt_[idx] = 0.0f;
      } else if (mode == SCAN) {
        tgt_[idx] = 70.0f * sinf(now * 0.0012f + idx * 3.1416f);
      } else if (now >= next_[idx]) {
        // サッカード: 30% で大きく振り、15% で左右そろって同じ方向を見る
        const long r = random(100);
        const float t = (r < 30)
            ? (float)random(-(long)EYE_LIM, (long)EYE_LIM + 1)
            : constrain(tgt_[idx] + (float)random(-25, 26), -EYE_LIM, EYE_LIM);
        if (r >= 85) {
          for (int j = 0; j < 2; j++) {
            tgt_[j] = t;
            next_[j] = now + random(400, 1800);
          }
        } else {
          tgt_[idx] = t;
          next_[idx] = now + random(300, 2200);
        }
      }
      const float goal = constrain(tgt_[idx] + bias, -EYE_LIM, EYE_LIM);
      const float step = EYE_SLEW_DPS * dt;
      cur_[idx] += constrain(goal - cur_[idx], -step, step);
      servos.writeDeg(EYE_CH[i], cur_[idx]);
    }
  }

 private:
  float cur_[2] = {0, 0}, tgt_[2] = {0, 0};
  uint32_t next_[2] = {0, 0};
};
