#pragma once
#include "gait.h"

// 実際に PCA へ出す脚角。歩容の目標値と区別して、連成ガードにも渡す。
class LegOutput {
 public:
  void reset() {
    for (int leg = 0; leg < 4; ++leg) current_[leg] = {0, 0, 0};
  }
  void update(float dt, const LegCmd target[4]) {
    const float step = LEG_SLEW_DPS * dt;
    for (int leg = 0; leg < 4; ++leg) {
      current_[leg].yaw += constrain(target[leg].ang.yaw-current_[leg].yaw, -step, step);
      current_[leg].pitch += constrain(target[leg].ang.pitch-current_[leg].pitch, -step, step);
      current_[leg].knee += constrain(target[leg].ang.knee-current_[leg].knee, -step, step);
    }
    clampLegYaw(current_);
  }
  const JointAngles& angle(int leg) const { return current_[leg]; }
  // 目標・出力のどちらかが危険側なら腕を退避。進入・退出の両遷移を覆う。
  JointAngles armGuard(int arm, const LegCmd target[4]) const {
    const int leg = arm == 0 ? FR : FL;
    JointAngles result = current_[leg];
    if (target[leg].ang.yaw * ARM_LEG_YAW_SIGN[arm] >
        result.yaw * ARM_LEG_YAW_SIGN[arm]) result.yaw = target[leg].ang.yaw;
    return result;
  }
 private:
  JointAngles current_[4] = {};
};
