#pragma once
#include "Arduino.h"
constexpr int WIFI_AP_STA=1,WL_CONNECTED=1;
struct FakeIP {String toString(){return "0.0.0.0";}};
struct FakeWiFi {void mode(int){} void setAutoReconnect(bool){} void softAP(const char*,const char*){} void begin(const char*,const char*){} int status(){return 0;} FakeIP localIP(){return {};}};inline FakeWiFi WiFi;
