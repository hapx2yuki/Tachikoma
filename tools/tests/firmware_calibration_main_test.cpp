#include <cassert>
#include <iostream>
#define CALIBRATION_MODE
#include "main.cpp"
static void tick(int n=1){for(int i=0;i<n;i++){fakeMillis()+=20;loop();}}
int main(){
 fakeMilliVolts()=0;setup();loop();tick(120);assert(servos.ready());
 for(auto& e:pwmEvents())assert(e.us==0||(e.us>1495&&e.us<=1500));
 pendingControl.stand=false;tick();assert(!servos.ready());pwmEvents().clear();tick(50);assert(pwmEvents().empty());
 pendingControl.stand=true;cal_us=2500;tick(120);assert(servos.ready());
 fakeMilliVolts()=1836;tick(100);fakeMilliVolts()=0;tick(200);assert(peri.cutout()&&!servos.ready());
 for(auto& board:fakePCA)for(int t:board.ticks)assert(t==4096);
 std::cout<<"PASS: actual calibration setup/loop permits USB neutral test, obeys rest/restart, and stops on sensed battery loss\n";
}
