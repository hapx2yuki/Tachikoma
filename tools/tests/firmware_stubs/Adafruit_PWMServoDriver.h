#pragma once
#include "Arduino.h"
#include "Wire.h"
class Adafruit_PWMServoDriver {int address=0x40;public:
 Adafruit_PWMServoDriver(){} Adafruit_PWMServoDriver(int a):address(a){}
 bool begin(){return fakePCA[address-0x40].online;}
 void setOscillatorFrequency(int){} uint32_t getOscillatorFrequency(){return 25000000;}
 void setPWMFreq(int hz){fakePCA[address-0x40].prescale=uint8_t(25000000/(hz*4096.0)+.5-1);fakePCA[address-0x40].mode=0x20;}
 void writeMicroseconds(int ch,int us){pwmEvents().push_back({address-0x40,ch,double(us),millis()});}
 uint8_t setPWM(int ch,int,int off){auto& p=fakePCA[address-0x40];if(!p.online)return 1;if(p.failNextPwm){p.failNextPwm=false;return 1;}p.ticks[ch]=off;pwmEvents().push_back({address-0x40,ch,off==4096?0:off*double(p.prescale+1)/25,millis()});return 0;}
};
