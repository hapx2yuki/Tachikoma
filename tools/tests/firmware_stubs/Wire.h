#pragma once
#include "Arduino.h"
#include <array>
struct PwmEvent {int board,ch;double us;uint32_t ms;};
inline std::vector<PwmEvent>& pwmEvents(){static std::vector<PwmEvent> events;return events;}
struct FakePCA {bool online=true;bool failNextPwm=false;uint8_t mode=0x20,prescale=121;std::array<int,16> ticks{};};
inline FakePCA fakePCA[2];
class TwoWire {int board=0;uint8_t reg=0;std::vector<uint8_t> bytes;public:
 int timeout=50;void begin(int,int){} void setTimeOut(int v){timeout=v;}
 void beginTransmission(uint8_t address){board=address-0x40;bytes.clear();}
 size_t write(uint8_t value){bytes.push_back(value);return 1;}
 uint8_t endTransmission(bool=true){if(board<0||board>1||!fakePCA[board].online)return 2;if(bytes.empty())return 0;reg=bytes[0];if(bytes.size()==2&&reg==0xFD&&bytes[1]==0x10){for(int ch=0;ch<16;ch++){fakePCA[board].ticks[ch]=4096;pwmEvents().push_back({board,ch,0,millis()});}}return 0;}
 uint8_t requestFrom(uint8_t,uint8_t n){return fakePCA[board].online?n:0;}
 int read(){return reg==0?fakePCA[board].mode:fakePCA[board].prescale;}
};inline TwoWire Wire;
