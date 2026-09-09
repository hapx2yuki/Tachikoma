#pragma once
#include "peripheral_probe.h"
class HardwareSerial {public:HardwareSerial(int){} void begin(int,int,int,int){++peripheralProbe.serialBegin;}};
