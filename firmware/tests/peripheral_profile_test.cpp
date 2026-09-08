#include <Arduino.h>
#include <cassert>
#include <iostream>
#include "peripheral_probe.h"
#include "peripherals.h"
#ifndef TACHIKOMA_STATUS_LEDS
#define TACHIKOMA_STATUS_LEDS 1
#endif
#ifndef TACHIKOMA_DFPLAYER
#define TACHIKOMA_DFPLAYER 1
#endif
int main(){
 fakeMilliVolts()=1836;
 Peripherals p;p.begin();
 if(peripheralProbe.ledBegin!=TACHIKOMA_STATUS_LEDS ||
    peripheralProbe.serialBegin!=TACHIKOMA_DFPLAYER ||
    peripheralProbe.dfBegin!=TACHIKOMA_DFPLAYER){
   std::cerr<<"FAIL: disabled peripheral initialized: LED="<<peripheralProbe.ledBegin
            <<" UART="<<peripheralProbe.serialBegin<<" DF="<<peripheralProbe.dfBegin<<"\n";
   return 1;
 }
 p.queueTrack(2);fakeMillis()=100;p.tick(false);
 assert(peripheralProbe.dfPlay==TACHIKOMA_DFPLAYER);
 assert(p.vbat()>7.0f && !p.cutout());
 fakeMilliVolts()=0;
 for(int i=0;i<90;++i){fakeMillis()+=100;p.tick(false);}
 assert(p.lowBattery() && p.cutout());
 fakeMilliVolts()=1836;
 for(int i=0;i<90;++i){fakeMillis()+=100;p.tick(false);}
 assert(p.cutout());
 if(!TACHIKOMA_STATUS_LEDS)assert(peripheralProbe.ledShow==0);
 std::cout<<"PASS: profile peripheral calls and ADC loss/latch are independent\n";
}
