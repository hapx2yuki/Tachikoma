#pragma once
#include "HardwareSerial.h"
class DFRobotDFPlayerMini {
 public:
  bool begin(HardwareSerial&,bool,bool){++peripheralProbe.dfBegin;return true;}
  void volume(int){}
  void playMp3Folder(int){++peripheralProbe.dfPlay;}
};
