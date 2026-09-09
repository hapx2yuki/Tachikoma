#include <cassert>
#include <iostream>
#define CALIBRATION_MODE
#include "main.cpp"
static void tick(int n=1){for(int i=0;i<n;i++){fakeMillis()+=20;loop();}}
int main(){
 fakeMilliVolts()=0;setup();loop();tick(120);assert(!servos.ready());
 for(auto& e:pwmEvents())assert(e.us==0);
 AsyncWebServerRequest missing;server.routes.at("/cal")(&missing);assert(missing.code==400);
 AsyncWebServerRequest select;select.params.emplace("ch",AsyncWebParameter("0"));select.params.emplace("us",AsyncWebParameter("1500"));select.params.emplace("confirm",AsyncWebParameter("1"));server.routes.at("/cal")(&select);assert(select.code==200);
 pwmEvents().clear();tick(120);for(auto& e:pwmEvents())if(e.us>0)assert(e.board==0&&e.ch==0);
 pendingControl.stand=false;tick();assert(!servos.ready());pwmEvents().clear();tick(50);assert(pwmEvents().empty());
 pendingControl.stand=true;cal_ch=0;cal_us=1500;tick(120);assert(!servos.ready());
 fakeMilliVolts()=1836;tick(100);fakeMilliVolts()=0;tick(200);assert(peri.cutout()&&!servos.ready());
 for(auto& board:fakePCA)for(int t:board.ticks)assert(t==4096);
 std::cout<<"PASS: actual calibration setup/loop permits USB neutral test, obeys rest/restart, and stops on sensed battery loss\n";
}
