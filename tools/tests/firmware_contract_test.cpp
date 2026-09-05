#include <Arduino.h>
#include <cassert>
#include <iostream>
#include <limits>
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
  Servos servos;servos.begin();
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
  for (int i=0;i<40;++i) { fakeMillis()+=100; peri.tick(false); }
  assert(peri.cutout());
  servos.enableAll();for(int i=0;i<33;++i){fakeMillis()+=100;servos.softStart();}
  pwmEvents().clear();servos.calibrateUs(2500,peri.cutout());
  assert(pwmEvents().size()==32);for(const auto& event:pwmEvents())assert(event.us==0);
  pwmEvents().clear();servos.softStart();servos.allUs(2500);assert(pwmEvents().empty());
  std::cout << "PASS: real low-voltage latch stops calibration PWM and cannot restart itself\n";
  Servos calibration;calibration.begin();calibration.enableAll();
  for(int i=0;i<33;++i){fakeMillis()+=100;calibration.softStart();}
  pwmEvents().clear();calibration.calibrateUs(1500,false,false);
  assert(pwmEvents().size()==32);for(const auto& event:pwmEvents())assert(event.us==0);
  pwmEvents().clear();calibration.calibrateUs(1500,false,true);assert(pwmEvents().empty());
  fakeMillis()+=100;calibration.calibrateUs(1500,false,true);
  assert(pwmEvents().size()==2 && pwmEvents()[0].ch==0 && pwmEvents()[1].ch==0);
  std::cout << "PASS: calibration also obeys rest and re-enable uses sequential start\n";
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
