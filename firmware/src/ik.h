#pragma once
#include <math.h>
#include "config.h"

// 脚 IK: 脚ローカル座標 (原点=ヨー軸, +X=脚radial外向き, +Z=上) の
// 足先目標 → 関節角 (deg)。
//   yaw   : 0 = 取付方位そのまま, +で CCW (上から見て)
//   pitch : 0 = femur 水平, +で足先が下がる (機構の中立と一致)
//   knee  : 0 = tibia が femur に対し垂直, +で足先が体側へ折れる
// 戻り値: 到達可能なら true

struct JointAngles { float yaw, pitch, knee; };

inline bool legIK(float x, float y, float z, JointAngles& out) {
  const float yaw = atan2f(y, x);
  const float r = sqrtf(x * x + y * y) - COXA_LEN;
  const float d = -z;  // 下向き正
  const float L1 = FEMUR_LEN, L2 = TIBIA_LEN;

  const float dist2 = r * r + d * d;
  const float dist = sqrtf(dist2);
  if (dist >= (L1 + L2) * 0.995f || dist <= fabsf(L1 - L2) * 1.02f) return false;

  // 膝角: femur と tibia のなす角 beta (cos 定理)。機構零点は 90°
  float cb = (dist2 - L1 * L1 - L2 * L2) / (2.0f * L1 * L2);
  cb = fminf(1.0f, fmaxf(-1.0f, cb));
  const float beta = acosf(cb);          // 0(伸び切り)〜pi(折り畳み)
  // 股ピッチ: 目標方向角 - 内角
  const float alpha = atan2f(d, r) - atan2f(L2 * sinf(beta), L1 + L2 * cosf(beta));

  out.yaw = yaw * 57.29578f;
  out.pitch = alpha * 57.29578f;
  out.knee = (beta - 1.5707963f) * 57.29578f;

  if (fabsf(out.yaw) > LIM_YAW) return false;
  if (out.pitch < LIM_PITCH_UP || out.pitch > LIM_PITCH_DN) return false;
  if (fabsf(out.knee) > LIM_KNEE) return false;
  return true;
}

// 順運動学 (検証用): 関節角 → 足先座標
inline void legFK(const JointAngles& a, float& x, float& y, float& z) {
  const float yaw = a.yaw / 57.29578f;
  const float pitch = a.pitch / 57.29578f;
  const float beta = a.knee / 57.29578f + 1.5707963f;
  const float r = COXA_LEN + FEMUR_LEN * cosf(pitch) + TIBIA_LEN * cosf(pitch + beta);
  const float d = FEMUR_LEN * sinf(pitch) + TIBIA_LEN * sinf(pitch + beta);
  x = r * cosf(yaw);
  y = r * sinf(yaw);
  z = -d;
}
