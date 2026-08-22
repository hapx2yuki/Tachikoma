#pragma once
#include <math.h>
#include "config.h"
#include "ik.h"
#include "servos.h"

// 腕制御: 目標角へのスルーレート制限付き追従 + プリセット + 歩行スイング +
// バイバイ (wave) アニメーション。
// UI からは [yaw, pitch, elbow] の目標角 (右腕基準) を受け、
// ミラーモードでは左腕はヨーを反転して追従する。
// 2026-07-29: 可動グリッパ廃止 (キット準拠固定爪化) につき grip 軸を削除。
// ch 19/23 は未使用のまま予約 (config.h ARM_CH 参照)。
// 2026-07-31: 前脚(FR/FL)×腕の連成クランプを追加 (config.h ARM_LEG_YAW_GATE_DEG
// 参照)。update() は呼び出し側 (main.cpp) から当該フレームの前脚関節角
// (legAng[0]=FR相当, legAng[1]=FL相当) を受け取る。

struct ArmTarget {
  float yaw = ARM_POSE_TUCK[0];
  float pitch = ARM_POSE_TUCK[1];
  float elbow = ARM_POSE_TUCK[2];
};

class Arms {
 public:
  ArmTarget target[2];   // [0]=右, [1]=左 (ミラーモード時は [0] を複製)
  bool mirror = true;    // 左右ミラー操作
  bool waving = false;   // wave アニメーション中

  void setPose(const float pose[3]) {
    waving = false;  // 明示的なポーズ指定は wave アニメーションを即中断する
    for (int a = 0; a < 2; a++) {
      target[a].yaw = pose[0];
      target[a].pitch = pose[1];
      target[a].elbow = pose[2];
    }
  }


  void startWave() { waving = true; waveStart_ = millis(); }

  // 50Hz で呼ぶ。walking 中は肩ピッチに小さなスイングを重畳。
  // bodyH は現在の体高指令 (地面ガードの基準)。
  // legAng は当該フレームの前脚関節角 ([0]=FR相当, [1]=FL相当) — 脚×腕
  // 連成クランプに使う (config.h ARM_LEG_YAW_GATE_DEG 参照)
  void update(float dt, Servos& servos, bool walking, float gaitPhase,
              float bodyH, const JointAngles legAng[2]) {
    if (mirror) target[1] = target[0];

    for (int a = 0; a < 2; a++) {
      ArmTarget t = target[a];
      // wave: 3 秒間、肘とヨーを振る (右腕のみ。ミラー時は両腕)
      if (waving) {
        const float wt = (millis() - waveStart_) * 1e-3f;
        if (wt > 3.0f) {
          waving = false;
        } else if (a == 0 || mirror) {
          t.pitch = -10.0f;
          t.elbow = 45.0f + 30.0f * sinf(wt * 12.0f);
          // 目標振幅 ±12°。内側 (yaw<0) は後段の相互接触クランプの対象だが、
          // 放射マウント (中立ヨーが正面から ARM_MOUNT_YAW_DEG=40° 外向き) では
          // 中立姿勢の時点で手先が大きく外側にあるため、旧来の前向き中立マウント
          // よりクランプは発火しにくい (実測は check_arm.py [5] 参照)
          t.yaw = 12.0f * sinf(wt * 6.0f);
        }
      }
      // 歩行スイング (前後脚と逆位相で自然に)
      if (walking && !waving) {
        t.pitch += ARM_SWING_DEG * sinf(6.28318f * gaitPhase + (a ? 3.1416f : 0));
      }

      // ---- ここから下のクランプは wave/スイング重畳後に必ず通す ----
      const float D = 57.29578f;
      t.yaw = constrain(t.yaw, -ARM_YAW_LIM, ARM_YAW_LIM);
      t.pitch = constrain(t.pitch, ARM_PITCH_MIN, ARM_PITCH_MAX);
      t.elbow = constrain(t.elbow, ARM_ELBOW_MIN, ARM_ELBOW_MAX);
      // 地面ガード: 手先が床へ潜る指令はピッチを起こして寸止めする
      const float dmax = bodyH + ARM_SHOULDER_OVER_HIP_MM - ARM_GROUND_MARGIN_MM;
      int guard = 0;
      while (ARM_UPPER_MM * sinf(t.pitch / D) +
                 ARM_REACH_MM * sinf((t.pitch + t.elbow) / D) > dmax &&
             t.pitch > ARM_PITCH_MIN && guard++ < 300)
        t.pitch -= 0.5f;
      // 折り畳み深追いガード (放射マウント固有, 2026-07-28 追加): 肘を深く
      // 巻き込む (pitch・elbow が共に大きい) と planar (肩→手先の水平距離)
      // が大きく負になり、中立ヨーが常時 ARM_MOUNT_YAW_DEG 外向きのため
      // yaw=0 に戻すだけでは sin(MOUNT_YAW+yaw) の符号が負に転じて手先が
      // 反対側 (他方の腕の領域) へ回り込む — ヨーは ±ARM_YAW_LIM の範囲では
      // これを打ち消せない (check_arm.py [5] で pitch85×elbow95 が実際に
      // 反対側まで越境することを数値確認済み)。地面ガードと同じ while パター
      // ンで pitch を起こし、planar が -5mm を割り込まないようにする
      guard = 0;
      while (ARM_UPPER_MM * cosf(t.pitch / D) +
                 ARM_REACH_MM * cosf((t.pitch + t.elbow) / D) < -5.0f &&
             t.pitch > ARM_PITCH_MIN && guard++ < 300)
        t.pitch -= 0.5f;
      // 前脚との分離は中立向きからの常時ヨーリミット (±ARM_YAW_LIM) が担う
      // (低位置マウントのため手先は常に脚と同じ高さ帯。check_arm.py [4] で
      //  全姿勢グリッドを検証済み)。
      // 連成リミット: 手先の横変位クランプ。放射マウント (中立ヨーが正面から
      // ARM_MOUNT_YAW_DEG 外向き) では手先の実方位は (MOUNT_YAW+yaw) —
      // 「yaw だけで前向き固定」の旧式は y を無視する前提のバグだった。
      // 「目標姿勢」と「スルーレート追従中の実姿勢 (cur_)」の両方で評価し、
      // 厳しい方を採用する
      {
        const float lat_max = ARM_MOUNT_X_MM - ARM_HAND_HALF_MM;
        const float planar_t = ARM_UPPER_MM * cosf(t.pitch / D)
                             + ARM_REACH_MM * cosf((t.pitch + t.elbow) / D);
        const float planar_c = ARM_UPPER_MM * cosf(cur_[a].pitch / D)
                             + ARM_REACH_MM * cosf((cur_[a].pitch + cur_[a].elbow) / D);
        if (fminf(planar_t, planar_c) < -5.0f) {
          // 折り畳みガード: planar が負 = 手が肩より後ろへ回り込む姿勢では
          // sin(MOUNT_YAW+yaw) の符号関係が崩れ、内側ヨー指令で手先が同側の
          // 股ゾーンへ張り出しかねない (check_arm [4] 参照)。折り畳み中は
          // ヨーを 0 (=中立の放射外向き, 最も安全な方向) へ戻す
          t.yaw = 0;
        } else if (t.yaw < 0) {
          // 内側ヨー: 両腕の相互接触回避。hand_x = MOUNT_X +
          // planar·sin(MOUNT_YAW+yaw) ≥ HAND_HALF を解いて yaw の下限を出す。
          // 【現在の定数では実質デッドコード】ARM_MOUNT_YAW_DEG(40°) >
          // ARM_YAW_LIM(15°) である限り、asinf(...) の値域 [0°,90°) から
          // -ARM_MOUNT_YAW_DEG-asinf(...) は常に -40°以下になり、
          // fmaxf(-ARM_YAW_LIM, ...) は常に -ARM_YAW_LIM(=無制限) を返す。
          // つまり手先中心 x の下限は事実上、上の折り畳み深追いガードと
          // MOUNT_X・sin(ARM_MOUNT_YAW_DEG) だけで決まる (実測は
          // check_arm.py [5] 参照)。ARM_MOUNT_YAW_DEG や ARM_YAW_LIM を
          // 将来変更し ARM_MOUNT_YAW_DEG ≤ ARM_YAW_LIM 近辺になった場合に
          // 再び意味を持つため、削除せず残している
          float yaw_min = -ARM_YAW_LIM;
          if (planar_t > lat_max)
            yaw_min = fmaxf(yaw_min,
                -ARM_MOUNT_YAW_DEG - asinf(lat_max / planar_t) * D);
          if (planar_c > lat_max)
            yaw_min = fmaxf(yaw_min,
                -ARM_MOUNT_YAW_DEG - asinf(lat_max / planar_c) * D);
          if (t.yaw < yaw_min) t.yaw = yaw_min;
        }
      }

      // 脚(前脚)×腕 連成クランプ (目標側, 2026-07-31 追加): 同側前脚が危険域
      // (体前方・頭部側へ大きく振れる) のときは腕ヨーを最大内寄せ
      // (-ARM_YAW_LIM) へ強制退避する。pitch/elbow は無関係 (実メッシュ
      // sweep で確認済み) なのでヨーのみ操作する。上の折り畳み/相互クランプ
      // より後に置き、常にこちらを最終的な安全側として優先する — 現行定数
      // (ARM_MOUNT_YAW_DEG=40°>ARM_YAW_LIM=15°) では相互クランプの yaw_min
      // も常に -ARM_YAW_LIM に一致するため競合しない (定数変更時は要再確認。
      // config.h ARM_LEG_YAW_GATE_DEG のコメント参照)
      if (legAng[a].yaw * ARM_LEG_YAW_SIGN[a] > ARM_LEG_YAW_GATE_DEG) {
        t.yaw = -ARM_YAW_LIM;
      }

      // スルーレート制限
      const float step = ARM_SLEW_DPS * dt;
      cur_[a].yaw += constrain(t.yaw - cur_[a].yaw, -step, step);
      cur_[a].pitch += constrain(t.pitch - cur_[a].pitch, -step, step);
      cur_[a].elbow += constrain(t.elbow - cur_[a].elbow, -step, step);
      // 地面ガード (実姿勢側): pitch と elbow は独立にスルーするため、目標側の
      // ガードだけでは遷移中の実姿勢が床を割り得る (レビューで数値再現済み)。
      // スルー後の実角度にも同じガードを通し、出力値そのものを保証する
      guard = 0;
      while (ARM_UPPER_MM * sinf(cur_[a].pitch / D) +
                 ARM_REACH_MM * sinf((cur_[a].pitch + cur_[a].elbow) / D) > dmax &&
             cur_[a].pitch > ARM_PITCH_MIN && guard++ < 300)
        cur_[a].pitch -= 0.5f;
      // 折り畳み深追いガード (実姿勢側): 目標側と同じ理由でスルー後の実角度
      // にも通す
      guard = 0;
      while (ARM_UPPER_MM * cosf(cur_[a].pitch / D) +
                 ARM_REACH_MM * cosf((cur_[a].pitch + cur_[a].elbow) / D) < -5.0f &&
             cur_[a].pitch > ARM_PITCH_MIN && guard++ < 300)
        cur_[a].pitch -= 0.5f;
      // 相互接触/折り畳みクランプ (実姿勢側): 目標側だけの補正では pitch/elbow
      // のスルー遅延中に cur_.yaw が一時的にクランプ角を超過する (レビューで
      // 最大 -1.8mm の両手すれ違い侵入を数値再現)。地面ガードと同様に
      // スルー後の実角度で再評価し、出力値そのものを保証する
      {
        const float lat_max2 = ARM_MOUNT_X_MM - ARM_HAND_HALF_MM;
        const float planar_c2 = ARM_UPPER_MM * cosf(cur_[a].pitch / D)
                              + ARM_REACH_MM * cosf((cur_[a].pitch + cur_[a].elbow) / D);
        if (planar_c2 < -5.0f) {
          cur_[a].yaw = 0;
        } else if (cur_[a].yaw < 0 && planar_c2 > lat_max2) {
          // 【現在の定数では実質デッドコード】上の目標側クランプと同じ理由
          // (ARM_MOUNT_YAW_DEG(40°) > ARM_YAW_LIM(15°) のため常に
          // -ARM_YAW_LIM でフロアされる) で ymin2 は常に -ARM_YAW_LIM。
          const float ymin2 = fmaxf(-ARM_YAW_LIM,
              -ARM_MOUNT_YAW_DEG - asinf(lat_max2 / planar_c2) * D);
          if (cur_[a].yaw < ymin2) cur_[a].yaw = ymin2;
        }
      }

      // 脚×腕 連成クランプ (実姿勢側, 2026-07-31 追加): 目標側と同じ理由
      // (pitch/elbow は独立にスルーするため、目標側だけの補正では遷移中の
      // cur_ が危険域に取り残される — 本ファイル内の他クランプと同じ教訓)
      // でスルー後の実角度にも同じ判定を通し、出力値そのものを保証する
      if (legAng[a].yaw * ARM_LEG_YAW_SIGN[a] > ARM_LEG_YAW_GATE_DEG) {
        cur_[a].yaw = -ARM_YAW_LIM;
      }

      // 出力 (左腕はヨーをミラー。ピッチ/肘はサーボ取付が左右ミラー印刷
      // なので符号はそのまま — 実機で逆なら ARM_SIGN と組み合わせて調整)
      servos.writeDeg(ARM_CH[a][0], cur_[a].yaw * ARM_SIGN[a]);
      servos.writeDeg(ARM_CH[a][1], cur_[a].pitch);
      servos.writeDeg(ARM_CH[a][2], cur_[a].elbow - 45.0f);  // 中立=45°曲げ
    }
  }

 private:
  ArmTarget cur_[2];
  uint32_t waveStart_ = 0;
};
