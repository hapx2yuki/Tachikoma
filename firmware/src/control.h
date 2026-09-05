#pragma once
#include <ArduinoJson.h>
#include <cerrno>
#include <cstdlib>
#include "arms.h"
#include "eyes.h"

struct ControlState {
  float vx = 0, vy = 0, wz = 0, h = BODY_H_DEF;
  #ifdef CALIBRATION_MODE
  bool stand = true;  // ホーンを外した校正専用ビルドのみ自動中立出力
#else
  bool stand = false;  // 再起動やブラウザ再読込で勝手に通電しない
#endif
  bool led = true, ptt = false, mirror = true;
  ArmTarget arm;
  int armAction = 0;  // 0=なし、1=明示姿勢、2=wave
  int eyeMode = Eyes::KYORO;
  uint32_t lastCmdMs = 0;
};

// 呼出側がロックした指令メールボックスを一度に更新する。
inline bool updateControlFromJson(JsonDocument& doc, ControlState& state, uint32_t now) {
  if (!doc.is<JsonObject>()) return false;
  // JSON の巨大指数や文字列を角度へ暗黙変換させない。フレーム単位で拒否する。
  for (const char* key : {"vx", "vy", "wz", "h", "ay", "ap", "ae"}) {
    if (!doc[key].isNull() &&
        (!doc[key].is<float>() || !isfinite(doc[key].as<float>()))) return false;
  }
  for (const char* key : {"stand", "led", "ptt", "amir"}) {
    if (!doc[key].isNull() && !doc[key].is<int>() && !doc[key].is<bool>()) return false;
  }
  if (!doc["vx"].isNull()) state.vx = constrain(doc["vx"].as<float>(), -1.0f, 1.0f);
  if (!doc["vy"].isNull()) state.vy = constrain(doc["vy"].as<float>(), -1.0f, 1.0f);
  if (!doc["wz"].isNull()) state.wz = constrain(doc["wz"].as<float>(), -1.0f, 1.0f);
  if (!doc["h"].isNull()) state.h = constrain(doc["h"].as<float>(), BODY_H_MIN, BODY_H_MAX);
  if (!doc["stand"].isNull()) state.stand = doc["stand"].as<bool>();
  if (!doc["led"].isNull()) state.led = doc["led"].as<bool>();
  if (!doc["ptt"].isNull()) state.ptt = doc["ptt"].as<bool>();
  if (!doc["ay"].isNull()) state.arm.yaw = constrain(doc["ay"].as<float>(), -ARM_YAW_LIM, ARM_YAW_LIM);
  if (!doc["ap"].isNull()) state.arm.pitch = constrain(doc["ap"].as<float>(), ARM_PITCH_MIN, ARM_PITCH_MAX);
  if (!doc["ae"].isNull()) state.arm.elbow = constrain(doc["ae"].as<float>(), ARM_ELBOW_MIN, ARM_ELBOW_MAX);
  if (!doc["amir"].isNull()) state.mirror = doc["amir"].as<bool>();
  if (!doc["ay"].isNull() || !doc["ap"].isNull() || !doc["ae"].isNull()) state.armAction = 1;
  // 歩行指令の全 3 成分があるフレームだけを heartbeat と扱う。
  if (!doc["vx"].isNull() && !doc["vy"].isNull() && !doc["wz"].isNull())
    state.lastCmdMs = now;
  return true;
}

// String::toInt() は変換失敗を 0 にするため、校正では 500us 側へ飛ぶ。
inline bool parseControlInteger(const char* value, int low, int high, int& out) {
  if (!value || !*value) return false;
  char* end = nullptr;
  errno = 0;
  const long parsed = strtol(value, &end, 10);
  if (errno == ERANGE || end == value || *end != '\0' || parsed < low || parsed > high)
    return false;
  out = static_cast<int>(parsed);
  return true;
}
