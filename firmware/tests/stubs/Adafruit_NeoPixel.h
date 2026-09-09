#pragma once
#include "Arduino.h"
#include "peripheral_probe.h"
constexpr int NEO_GRB=0, NEO_KHZ800=0;
class Adafruit_NeoPixel {
 public:
  Adafruit_NeoPixel(int,int,int){}
  void begin(){++peripheralProbe.ledBegin;}
  void setBrightness(int){}
  void show(){++peripheralProbe.ledShow;}
  int Color(int,int,int){return 0;}
  void setPixelColor(int,int){}
};
