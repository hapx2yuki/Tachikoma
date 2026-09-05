#include <Arduino.h>
#include <cassert>
#include <iostream>
#include "servos.h"
#include "peripherals.h"
#include "control.h"
static void start(Servos& s){s.enableAll();for(int n=0;n<33;n++){fakeMillis()+=100;s.softStart();}}
int main(){
 ControlState c;assert(!c.stand);std::cout<<"PASS: normal boot requires an explicit start command\n";
 Peripherals p;p.begin();fakeMilliVolts()=1836;for(int i=0;i<20;i++){fakeMillis()+=100;p.tick(false);}assert(!p.cutout());
 fakeMilliVolts()=0;for(int i=0;i<40;i++){fakeMillis()+=100;p.tick(false);}assert(p.cutout()&&p.lowBattery());
 fakeMilliVolts()=1836;for(int i=0;i<50;i++){fakeMillis()+=100;p.tick(false);}assert(p.cutout());
 Peripherals usb;usb.begin();fakeMilliVolts()=0;for(int i=0;i<100;i++){fakeMillis()+=100;usb.tick(false);}assert(!usb.cutout());
 std::cout<<"PASS: detected battery loss latches after 3s, recovery cannot re-arm, USB-only calibration remains available\n";
 fakePCA[1].online=false;Servos absent;absent.begin();start(absent);assert(absent.faulted()&&!absent.ready());for(auto& b:fakePCA)for(int t:b.ticks)assert(t==0||t==4096);
 fakePCA[1].online=true;absent.serviceFault();start(absent);assert(!absent.ready());
 std::cout<<"PASS: absent PCA at boot blocks both boards and cannot auto-restart on reconnection\n";
 Servos runtime;runtime.begin();start(runtime);assert(runtime.ready());
 for(int ch:{CH_HEAD,EYE_CH[1],27,28,29,30,31})assert(fakePCA[ch/16].ticks[ch%16]==4096);
 const int before=fakePCA[0].ticks[0];fakePCA[0].failNextPwm=true;runtime.writeDeg(0,30);assert(runtime.faulted()&&!runtime.ready());
 for(auto& b:fakePCA)for(int t:b.ticks)assert(t==4096);start(runtime);assert(!runtime.ready());
 std::cout<<"PASS: one failed PWM write immediately full-offs both boards and latches fault\n";
 Servos lost;lost.begin();start(lost);fakePCA[1].online=false;lost.writeDeg(16,20);assert(lost.faulted());for(int t:fakePCA[0].ticks)assert(t==4096);
 fakePCA[1].online=true;fakeMillis()+=100;lost.serviceFault();for(int t:fakePCA[1].ticks)assert(t==4096);assert(!lost.ready());
 std::cout<<"PASS: healthy PCA stops during bus loss, failed full-off is retried when other PCA reconnects\n";
 Servos reset;reset.begin();start(reset);fakePCA[1].mode=0x01;fakePCA[1].prescale=0x1e;fakeMillis()+=100;reset.serviceFault();assert(reset.faulted());
 std::cout<<"PASS: unexpected PCA configuration reset is detected and stops output\n";
 assert(Wire.timeout==5);
}
