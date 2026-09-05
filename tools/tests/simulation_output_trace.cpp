// 実Gait/LegOutput/Arms/Servosを使用。PCAスタブは50Hz量子化後の実パルスを記録。
#include <Arduino.h>
#include <iostream>
#include <iomanip>
#include "leg_output.h"
#include "arms.h"
float angle(int ch) {auto& p=fakePCA[ch/16];return (p.ticks[ch%16]*double(p.prescale+1)/25-1500)*DEG_RANGE/(US_MAX-US_MIN);}
bool enabled(int ch){return fakePCA[ch/16].ticks[ch%16]!=4096;}
int main(int argc,char** argv){
 Gait gait;LegOutput legs;Arms arms;Servos servos;servos.begin();servos.enableAll();arms.setPose(ARM_POSE_READY);
 bool sequential=argc>1 && std::string(argv[1])=="sequential";
 float dt,vx,vy,wz,h;
 auto update=[&](float step,float x,float y,float z,float height){
   LegCmd target[4]={};gait.bodyH=height;gait.update(step,x,y,z,target);legs.update(step,target);
   for(int l=0;l<4;++l){auto a=legs.angle(l);servos.writeJoint(l,0,a.yaw);servos.writeJoint(l,1,a.pitch);servos.writeJoint(l,2,a.knee);}
   JointAngles guard[2]={legs.armGuard(0,target),legs.armGuard(1,target)};
   arms.update(step,servos,gait.moving(),gait.phase(),height,guard);
 };
 if(!sequential){for(int i=0;i<33;++i){fakeMillis()+=100;servos.softStart();}if(argc<2 || std::string(argv[1])=="ready")for(int i=0;i<100;++i)update(.02,0,0,0,BODY_H_DEF);}
 std::cout<<std::setprecision(10);
 while(std::cin>>dt>>vx>>vy>>wz>>h){
  fakeMillis()+=uint32_t(dt*1000+.5);servos.softStart();if(servos.ready())update(dt,vx,vy,wz,h);
  std::cout<<gait.phase()<<' '<<gait.moving()<<' '<<servos.ready();
  for(int l=0;l<4;++l)for(int j=0;j<3;++j)std::cout<<' '<<angle(PCA_CH[l][j])*JOINT_SIGN[l][j];
  for(int a=0;a<2;++a)for(int j=0;j<3;++j)std::cout<<' '<<(j==0?angle(ARM_CH[a][j])*ARM_SIGN[a]:angle(ARM_CH[a][j])+(j==2?45:0));
  for(int e:{0,2})std::cout<<' '<<angle(EYE_CH[e]);
  for(int l=0;l<4;++l)for(int j=0;j<3;++j)std::cout<<' '<<enabled(PCA_CH[l][j]);
  for(int a=0;a<2;++a)for(int j=0;j<3;++j)std::cout<<' '<<enabled(ARM_CH[a][j]);
  for(int e:{0,2})std::cout<<' '<<enabled(EYE_CH[e]);
  std::cout<<'\n';pwmEvents().clear();
 }
}
