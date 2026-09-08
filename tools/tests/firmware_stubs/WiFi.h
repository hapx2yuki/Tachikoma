#pragma once
#include "Arduino.h"
constexpr int WIFI_AP_STA=1,WL_CONNECTED=1,WL_NO_SSID_AVAIL=2,
              WL_CONNECT_FAILED=3,WL_CONNECTION_LOST=4,WL_DISCONNECTED=5;
struct FakeIP {String toString(){return "0.0.0.0";}};
struct FakeWiFi {
 bool apOk=true;
 void mode(int){}
 void setAutoReconnect(bool){}
 bool softAP(const char*,const char*){return apOk;}
 void begin(const char*,const char*){}
 int status(){return 0;}
 FakeIP localIP(){return {};}
};
inline FakeWiFi WiFi;
