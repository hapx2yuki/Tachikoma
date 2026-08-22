#pragma once
#include <math.h>
#include "config.h"
#include "ik.h"

// クロール歩容エンジン (重心シフト付き)。
// 入力: vx, vy (-1..1, ボディ座標。+Y前), wz (-1..1 旋回), 体高 h
// - 各脚は位相オフセット付きサイクル: 接地(DUTY)で後方へ掃引、遊脚で前方へ復帰
// - 遊脚は常に 1 本。その間ボディ重心を遊脚の反対方向へ SWAY_MM 寄せて
//   支持三角形の安定マージンを確保する (レビュー指摘対応)
// - 停止指令時は次の「全脚接地の瞬間」(四半位相境界) まで進めてから静止する

struct LegCmd { JointAngles ang; bool ok; };

class Gait {
 public:
  float bodyH = BODY_H_DEF;

  void update(float dt, float vx, float vy, float wz, LegCmd out[4]) {
    const float mag = fminf(1.0f, sqrtf(vx * vx + vy * vy) + fabsf(wz));
    if (mag > 0.05f) {
      phase_ = fmodf(phase_ + dt / CYCLE_T, 1.0f);
      holding_ = false;
    } else if (!holding_) {
      // 停止指令: 次の四半境界 (遊脚が接地し全脚接地になる瞬間) で止める
      const float q = ceilf(phase_ / 0.25f + 1e-4f) * 0.25f;
      const float next = phase_ + dt / CYCLE_T;
      if (next >= q) { phase_ = fmodf(q, 1.0f); holding_ = true; }
      else { phase_ = next; }
    }

    // 重心シフト: 各脚の遊脚窓 (前後 SWAY_LEAD 拡張) に sin 窓を掛けて合成。
    // 離地の瞬間に既にシフトが乗るよう、窓は遊脚区間に先行して立ち上がる
    float swayX = 0, swayY = 0;
    if (!holding_) {
      const float swingLen = 1.0f - DUTY;
      const float winLen = swingLen + 2.0f * SWAY_LEAD;
      for (int leg = 0; leg < 4; leg++) {
        const float p = fmodf(phase_ + PHASE_OFF[leg], 1.0f);
        // 遊脚窓開始 = DUTY - SWAY_LEAD。wrap を考慮した窓内位置
        float u = p - (DUTY - SWAY_LEAD);
        if (u < -0.5f) u += 1.0f;
        if (u < 0 || u > winLen) continue;
        const float m = STANCE_DEG[leg] / 57.29578f;
        const float nx = LEG_ORIGIN[leg][0] + STANCE_R * cosf(m);
        const float ny = LEG_ORIGIN[leg][1] + STANCE_R * sinf(m);
        const float nn = sqrtf(nx * nx + ny * ny);
        const float k = SWAY_MM * sinf(3.14159265f * u / winLen);
        swayX += -nx / nn * k;
        swayY += -ny / nn * k;
      }
    }

    for (int leg = 0; leg < 4; leg++) {
      const float mountRad = LEG_MOUNT_DEG[leg] / 57.29578f;
      // 中立足先は STANCE_DEG 方位 (取付方位 + 中立ヨー ±12°, v3 実物ポーズ)
      const float stanceRad = STANCE_DEG[leg] / 57.29578f;
      const float nx = LEG_ORIGIN[leg][0] + STANCE_R * cosf(stanceRad);
      const float ny = LEG_ORIGIN[leg][1] + STANCE_R * sinf(stanceRad);

      // 併進 + 旋回を合成した 1 周期分の変位 (ボディ座標)
      const float turn = wz * MAX_TURN_DEG / 57.29578f;
      const float tx = nx * cosf(turn) - ny * sinf(turn) - nx;
      const float ty = nx * sinf(turn) + ny * cosf(turn) - ny;
      float sx = vx * MAX_STEP + tx;
      float sy = vy * MAX_STEP + ty;
      // 合成歩幅をワークスペース内に収める
      const float sn = sqrtf(sx * sx + sy * sy);
      if (sn > MAX_STEP) { sx *= MAX_STEP / sn; sy *= MAX_STEP / sn; }

      const float p = fmodf(phase_ + PHASE_OFF[leg], 1.0f);
      float dx, dy, dz;
      if (p < DUTY) {  // 接地: +s/2 → -s/2 (ボディを前へ送る)
        const float t = p / DUTY;
        dx = sx * (0.5f - t);
        dy = sy * (0.5f - t);
        dz = 0;
      } else {         // 遊脚: -s/2 → +s/2, サイン持ち上げ
        const float t = (p - DUTY) / (1.0f - DUTY);
        dx = sx * (t - 0.5f);
        dy = sy * (t - 0.5f);
        dz = STEP_H * sinf(3.14159265f * t);
      }

      // 重心シフト: ボディが +sway へ動く = ボディ座標の足先は -sway
      const float fx = nx + dx - swayX - LEG_ORIGIN[leg][0];
      const float fy = ny + dy - swayY - LEG_ORIGIN[leg][1];
      float lx = fx * cosf(-mountRad) - fy * sinf(-mountRad);
      float ly = fx * sinf(-mountRad) + fy * cosf(-mountRad);
      const float lz = -bodyH + dz;

      // ワークスペース射影: 膝リミット対応の距離円環内へ平面クランプ
      // (v3: 遊脚の反対側 66° 隣の脚が sway で外側へ押される。内側 (折り畳み)
      // も対称にガードする。config.h 参照)
      {
        const float dd = -lz;
        const float rr = sqrtf(lx * lx + ly * ly);
        if (dd < D_KNEE_MAX) {
          const float rmax = COXA_LEN + sqrtf(D_KNEE_MAX * D_KNEE_MAX - dd * dd);
          if (rr > rmax) { lx *= rmax / rr; ly *= rmax / rr; }
        }
        if (dd < D_KNEE_MIN && rr > 0.1f) {
          const float rmin = COXA_LEN + sqrtf(D_KNEE_MIN * D_KNEE_MIN - dd * dd);
          if (rr < rmin) { lx *= rmin / rr; ly *= rmin / rr; }
        }
      }

      out[leg].ok = legIK(lx, ly, lz, out[leg].ang);
      if (!out[leg].ok) {  // 到達不能時は中立へフォールバック (成否も反映)
        const float d = stanceRad - mountRad;
        out[leg].ok = legIK(STANCE_R * cosf(d), STANCE_R * sinf(d), -bodyH,
                            out[leg].ang);
      }
      if (!out[leg].ok) {
        // 二重失敗: 直近の成功角を保持 (legIK はリミット超過でも角度を書く
        // ため、失敗値をそのまま出力しない — レビュー指摘)。未初期化なら
        // 各関節をソフトリミットへ constrain して範囲だけ保証する
        //
        // ⚠ 2026-07-31 QA 指摘: tools/check_shin_arm_leg.py の pk_reachable()
        // (D_KNEE_MIN/MAX の円環条件による「歩容コマンドとして到達可能な
        // (pitch,knee) 集合」の閉形式判定) は、上の平常経路 (単一 legIK 成功
        // または中立フォールバック成功) が出力する角度にのみ厳密に成立する。
        // この else 分岐 (二重失敗、かつ lastOkValid_ が false — 起動直後
        // 最初の成功フレームより前の極めて限定的なエッジケースのみ到達)
        // は D_KNEE_MIN/MAX の円環条件を経由せず各関節を個別に constrain()
        // するため、この分岐が発火した場合に限り pk_reachable() の前提が
        // 理論上崩れ得る。実用上のリスクはほぼゼロ (通常構成のロボットで
        // STANCE 中立ターゲット自体が legIK 失敗することは想定されず、
        // lastOkValid_ も起動後最初の成功フレームで即 true になる) だが、
        // 「歩容コマンドとして構造的に出力され得ない」という主張はこの
        // 分岐についてのみ形式的な証明ではない点に留意 (pk_reachable() の
        // 閉形式導出自体は sim_gait.leg_fk() とのグリッド突合せで独立検証済み)。
        if (lastOkValid_[leg]) {
          out[leg].ang = lastOk_[leg];
        } else {
          out[leg].ang.yaw = constrain(out[leg].ang.yaw, -LIM_YAW, LIM_YAW);
          out[leg].ang.pitch = constrain(out[leg].ang.pitch,
                                         LIM_PITCH_UP, LIM_PITCH_DN);
          out[leg].ang.knee = constrain(out[leg].ang.knee, -LIM_KNEE, LIM_KNEE);
        }
      }
      // 45° ペア内側ヨーの安全クランプ 1: 単側 (通常歩容では発火しない)。
      // ヨーのみ書換えても pitch/knee は同じ (r,z) の解のまま = クランプ後も
      // 自己整合な到達姿勢 (足先が同半径のまま方位だけずれる)。接地 z は
      // 変わらないため安全側。sim_gait [2b] が歩容中の非発火を検証する
      if (out[leg].ang.yaw * YAW_IN_SIGN[leg] > LIM_YAW_IN) {
        out[leg].ang.yaw = LIM_YAW_IN * YAW_IN_SIGN[leg];
      }
      // 後脚のポッド側ヨー制限 (v3: ポッドが脚高さの後方に接続)
      if (YAW_POD_SIGN[leg] != 0 &&
          out[leg].ang.yaw * YAW_POD_SIGN[leg] > LIM_YAW_POD) {
        out[leg].ang.yaw = LIM_YAW_POD * YAW_POD_SIGN[leg];
      }
    }

    // 45° ペア内側ヨーの安全クランプ 2: ペア同時内側の和 (config.h 参照)
    const int pairIdx[2][2] = {{FR, RR}, {FL, RL}};
    for (int p = 0; p < 2; p++) {
      const int a = pairIdx[p][0], b = pairIdx[p][1];
      const float ia = out[a].ang.yaw * YAW_IN_SIGN[a];
      const float ib = out[b].ang.yaw * YAW_IN_SIGN[b];
      if (ia > 0 && ib > 0 && ia + ib > LIM_YAW_IN_SUM) {
        const float k = LIM_YAW_IN_SUM / (ia + ib);
        out[a].ang.yaw = ia * k * YAW_IN_SIGN[a];
        out[b].ang.yaw = ib * k * YAW_IN_SIGN[b];
      }
    }

    // 直近の成功角を保存 (二重失敗時の保持用)
    for (int leg = 0; leg < 4; leg++) {
      if (out[leg].ok) {
        lastOk_[leg] = out[leg].ang;
        lastOkValid_[leg] = true;
      }
    }
  }

  float phase() const { return phase_; }  // 腕スイング同期用

 private:
  float phase_ = 0;
  bool holding_ = true;  // 起動直後は静止
  JointAngles lastOk_[4];          // 直近の IK 成功角 (二重失敗時の保持)
  bool lastOkValid_[4] = {false, false, false, false};
};
