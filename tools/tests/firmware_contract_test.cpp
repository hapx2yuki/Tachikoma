#include <Arduino.h>
#include <cassert>
#include <cmath>
#include <iostream>
#include <limits>
#include <set>
#include <string>
#include "servos.h"
#include "control.h"
#include "leg_output.h"
#include "peripherals.h"

int main() {
  JointAngles result={1,2,3};
  assert(!legIK(std::numeric_limits<float>::quiet_NaN(),0,-115,result));
  assert(!legIK(100,0,-std::numeric_limits<float>::infinity(),result));
  assert(result.yaw==1 && result.pitch==2 && result.knee==3);
  std::cout << "PASS: IK rejects non-finite inputs without mutating output\n";
  int pulse=1500;
  for(const char* text:{"NaN", "", "1500junk", "500.0", "99999999999999999999", "499", "2501"}) {
    assert(!parseControlInteger(text,US_MIN,US_MAX,pulse) && pulse==1500);
  }
  assert(parseControlInteger("2500",US_MIN,US_MAX,pulse) && pulse==2500);
  std::cout << "PASS: calibration query validation cannot turn malformed text into a servo endpoint\n";
  ControlState c; c.stand=false; c.lastCmdMs=100;
  JsonDocument doc;
  deserializeJson(doc,"{\"ay\":5}");
  assert(updateControlFromJson(doc,c,200) && !c.stand && c.lastCmdMs==100);
  deserializeJson(doc,"{\"vx\":1,\"vy\":0,\"wz\":0,\"ap\":1e309,\"stand\":1}");
  assert(!updateControlFromJson(doc,c,300) && !c.stand && c.vx==0);
  deserializeJson(doc,"{\"vx\":1,\"vy\":0,\"wz\":0,\"ay\":\"nan\"}");
  assert(!updateControlFromJson(doc,c,300));
  deserializeJson(doc,"{\"vx\":2,\"vy\":0,\"wz\":0,\"h\":105,\"stand\":1,\"ptt\":1}");
  assert(updateControlFromJson(doc,c,400) && c.vx==1 && c.h==BODY_H_MIN && c.stand && c.ptt && c.lastCmdMs==400);
  std::cout << "PASS: malformed command rejection, atomic frame validation, partial updates do not re-enable\n";
  Servos servos;servos.begin();servos.setPowerReady(true);
  assert(pwmEvents().size()>=32);
  for(const auto& event:pwmEvents()) assert(event.us==0);
  pwmEvents().clear();servos.enableAll();
  fakeMillis()=99;servos.softStart();assert(pwmEvents().empty());
  fakeMillis()=100;servos.softStart();assert(pwmEvents().size()==1 && !servos.ready());
  const auto size=pwmEvents().size();servos.writeDeg(0,NAN);assert(pwmEvents().size()==size);
  for (int i=2;i<33;++i){fakeMillis()=i*100;servos.softStart();}
  assert(servos.ready() && pwmEvents().size()==20);
  for(size_t i=1;i<pwmEvents().size();++i) assert(pwmEvents()[i].ms-pwmEvents()[i-1].ms>=100);
  for(const auto& event:pwmEvents()) assert(event.board*16+event.ch!=25 && event.board*16+event.ch!=CH_HEAD);
  servos.disableAll();pwmEvents().clear();fakeMillis()+=1000;servos.softStart();servos.writeDeg(0,0);assert(pwmEvents().empty() && !servos.ready());
  std::cout << "PASS: boot full-off, actual sequential neutral pulses, unused camera channel, disabled latch\n";
  Peripherals peri; peri.begin();
  // A low ADC value before any in-range sample is an unverified/open input,
  // not a confirmed low battery.  Establish the battery first, then verify
  // the three-second cutout latch on a genuinely low reading.
  fakeMilliVolts()=1836;
  for (int i=0;i<5;++i) { fakeMillis()+=100; peri.tick(false); }
  assert(peri.vbatVerified() && !peri.cutout());
  assert(std::string(peri.vbatState()) == "VERIFIED");
  fakeMilliVolts()=0;
  for (int i=0;i<40;++i) { fakeMillis()+=100; peri.tick(false); }
  assert(peri.cutout() && !peri.vbatVerified());
  servos.enableAll();for(int i=0;i<33;++i){fakeMillis()+=100;servos.softStart();}
  pwmEvents().clear();assert(!servos.calibrateUs(0,1500,peri.cutout(),true,true));
  assert(pwmEvents().empty());
  std::cout << "PASS: real low-voltage latch stops the single-axis diagnostic PWM\n";
  Servos calibration;calibration.begin();calibration.setPowerReady(false);
  pwmEvents().clear();assert(!calibration.calibrateUs(0,1500,false,true,false));
  assert(pwmEvents().empty());
  int low=0,high=0;assert(calibration.calibrationPulseRange(0,low,high));
  assert(!calibration.calibrateUs(0,low-1,false,true,true));
  assert(!calibration.calibrateUs(12,1500,false,true,true));
  assert(pwmEvents().empty());
  pwmEvents().clear();assert(calibration.calibrateUs(0,1500,false,true,true));
  assert(pwmEvents().size()==33 && pwmEvents().back().ch==0 && pwmEvents().back().us>1490);
  for(int ch=1;ch<16;ch++) assert(fakePCA[0].ticks[ch]==4096);
  for(int ch=0;ch<16;ch++) if(ch!=0) assert(fakePCA[1].ticks[ch]==4096);
  std::cout << "PASS: calibration requires explicit diagnostic mode, one used axis, and axis-safe pulse range\n";
  std::set<int> usedChannels;
  for (int leg=0; leg<4; ++leg) for (int joint=0; joint<3; ++joint)
    usedChannels.insert(PCA_CH[leg][joint]);
  for (int arm=0; arm<2; ++arm) for (int joint=0; joint<3; ++joint)
    usedChannels.insert(ARM_CH[arm][joint]);
  usedChannels.insert(EYE_CH[0]); usedChannels.insert(EYE_CH[2]);
  assert(usedChannels.size()==20);
  for (int ch=0; ch<N_CH; ++ch) {
    int axisLow=0, axisHigh=0;
    assert(calibration.calibrationPulseRange(ch,axisLow,axisHigh)==
           (usedChannels.count(ch)!=0));
    if (usedChannels.count(ch)) assert(axisLow>=US_MIN+CAL_ENDPOINT_MARGIN_US &&
                                       axisHigh<=US_MAX-CAL_ENDPOINT_MARGIN_US &&
                                       axisLow<=axisHigh);
  }
  std::cout << "PASS: all 20 connected axes have a bounded calibration range; unused channels reject selection\n";

  // 校正範囲は論理関節角を実際の raw PWM へ写す契約そのものを表駆動で
  // 検査する。特に FR/RL の pitch・knee は JOINT_SIGN=-1 のため、符号を
  // 落とすと端点が左右反転してしまう。trim も通常出力と同じ zero へ加える。
  const int endpointLow = US_MIN + CAL_ENDPOINT_MARGIN_US;
  const int endpointHigh = US_MAX - CAL_ENDPOINT_MARGIN_US;
  auto checkCalibrationAxis = [&](int ch, float logicalMin, float logicalMax,
                                  int sign) {
    const float usPerDeg = (US_MAX - US_MIN) / DEG_RANGE;
    const float rawMinDeg = std::fmin(logicalMin * sign, logicalMax * sign);
    const float rawMaxDeg = std::fmax(logicalMin * sign, logicalMax * sign);
    const float zeroUs = (US_MIN + US_MAX) * 0.5f + calibration.trim(ch);
    const int mappedLow = (int)std::ceil(zeroUs + rawMinDeg * usPerDeg);
    const int mappedHigh = (int)std::floor(zeroUs + rawMaxDeg * usPerDeg);
    const int expectedLow = mappedLow > endpointLow ? mappedLow : endpointLow;
    const int expectedHigh = mappedHigh < endpointHigh ? mappedHigh : endpointHigh;
    int actualLow = 0, actualHigh = 0;
    assert(calibration.calibrationPulseRange(ch, actualLow, actualHigh));
    assert(actualLow == expectedLow && actualHigh == expectedHigh);

    // 両端値は許可され、選択軸以外は常に PCA の ALL_LED_OFF 状態にする。
    for (int pulse : {actualLow, actualHigh}) {
      pwmEvents().clear();
      assert(calibration.calibrateUs(ch, pulse, false, true, true));
      for (int board = 0; board < 2; ++board)
        for (int local = 0; local < 16; ++local) {
          const int global = board * 16 + local;
          if (global == ch) assert(fakePCA[board].ticks[local] != 4096);
          else assert(fakePCA[board].ticks[local] == 4096);
        }
      assert(!pwmEvents().empty() &&
             pwmEvents().back().board * 16 + pwmEvents().back().ch == ch);
    }
    // 端点の外側は拒否され、その時点で残留出力も全消灯する。
    pwmEvents().clear();
    assert(!calibration.calibrateUs(ch, actualLow - 1, false, true, true));
    for (const auto& board : fakePCA)
      for (int ticks : board.ticks) assert(ticks == 4096);
  };

  for (int leg = 0; leg < 4; ++leg) {
    checkCalibrationAxis(PCA_CH[leg][0], -LIM_YAW, LIM_YAW,
                         JOINT_SIGN[leg][0]);
    checkCalibrationAxis(PCA_CH[leg][1], LIM_PITCH_UP, LIM_PITCH_DN,
                         JOINT_SIGN[leg][1]);
    checkCalibrationAxis(PCA_CH[leg][2], -LIM_KNEE, LIM_KNEE,
                         JOINT_SIGN[leg][2]);
  }
  for (int arm = 0; arm < 2; ++arm) {
    checkCalibrationAxis(ARM_CH[arm][0], -ARM_YAW_LIM, ARM_YAW_LIM,
                         ARM_SIGN[arm]);
    checkCalibrationAxis(ARM_CH[arm][1], ARM_PITCH_MIN, ARM_PITCH_MAX, +1);
    checkCalibrationAxis(ARM_CH[arm][2], ARM_ELBOW_MIN - 45.0f,
                         ARM_ELBOW_MAX - 45.0f, +1);
  }
  checkCalibrationAxis(EYE_CH[0], -EYE_LIM, EYE_LIM, +1);
  checkCalibrationAxis(EYE_CH[2], -EYE_LIM, EYE_LIM, +1);
  // 非対称な FR pitch に trim を入れた場合も raw 範囲が同じ量だけ移動する。
  calibration.setTrim(PCA_CH[FR][1], 37);
  checkCalibrationAxis(PCA_CH[FR][1], LIM_PITCH_UP, LIM_PITCH_DN,
                       JOINT_SIGN[FR][1]);
  calibration.setTrim(PCA_CH[FR][1], 0);
  std::cout << "PASS: table-driven raw calibration mapping covers all signs, endpoints, trim, margin, and full-off\n";
  LegCmd target[4]={};for(auto& l:target)l.ok=true;
  target[FR].ang.yaw=35;target[FL].ang.yaw=-35;
  LegOutput output;
  for(int i=0;i<10;++i)output.update(.02f,target);
  target[FR].ang.yaw=0;target[FL].ang.yaw=0;
  output.update(.02f,target);
  assert(output.angle(FR).yaw>ARM_LEG_YAW_GATE_DEG);
  assert(output.armGuard(0,target).yaw>ARM_LEG_YAW_GATE_DEG);
  assert(-output.armGuard(1,target).yaw>ARM_LEG_YAW_GATE_DEG);
  // Actual Arms::update must keep both outputs at the retreat angle on the exit frame.
  servos.enableAll();for(int i=0;i<33;++i){fakeMillis()+=100;servos.softStart();}
  Arms arms;arms.setPose(ARM_POSE_READY);
  JointAngles guard[2]={output.armGuard(0,target),output.armGuard(1,target)};
  pwmEvents().clear();arms.update(.02f,servos,false,0,BODY_H_DEF,guard);
  for(int a=0;a<2;++a){ bool seen=false; for(const auto& event:pwmEvents())if(event.board*16+event.ch==ARM_CH[a][0]){seen=true;assert(std::abs(event.us-(1500-ARM_YAW_LIM*ARM_SIGN[a]*(US_MAX-US_MIN)/DEG_RANGE))<5.1);}assert(seen); }
  std::cout << "PASS: front leg slew exit keeps both arms in retreat (actual PWM output)\n";
  Gait gait;
  for(int i=0;i<10000;++i){gait.update(.02f,(i%13-6)/6.f,(i%19-9)/9.f,(i%7-3)/3.f,target);output.update(.02f,target);for(int l=0;l<4;++l){const auto& a=output.angle(l);assert(std::isfinite(a.yaw)&&std::isfinite(a.pitch)&&std::isfinite(a.knee));assert(a.yaw*YAW_IN_SIGN[l]<=LIM_YAW_IN+.001f);}}
  std::cout << "PASS: 10000 changing-command frames keep finite angles and leg yaw constraints\n";
  servos.setTrim(0,20); fakeMillis()+=2100;
  bool changed=false;
  preferenceWriteHook=[&](){if(!changed){changed=true;servos.setTrim(0,60);}};
  servos.persistTrims();
  assert(preferenceValues["t0"]==20 && servos.trim(0)==60);
  preferenceWriteHook=nullptr;fakeMillis()+=2100;servos.persistTrims();
  assert(preferenceValues["t0"]==60);
  std::cout << "PASS: trim update during NVS write remains pending and is saved next time\n";

}
