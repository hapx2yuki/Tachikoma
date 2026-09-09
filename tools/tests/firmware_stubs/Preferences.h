#pragma once
#include "Arduino.h"
#include <map>
#include <functional>
inline std::map<std::string,short> preferenceValues;
inline std::function<void()> preferenceWriteHook;
class Preferences {public:
 void begin(const char*,bool){} void end(){} String getString(const char*,const char* value){return String(value);} void putString(const char*,const String&){}
 short getShort(const char* key,short value){auto it=preferenceValues.find(key);return it==preferenceValues.end()?value:it->second;}
 void putShort(const char* key,short value){preferenceValues[key]=value;if(preferenceWriteHook)preferenceWriteHook();}
};
