#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <string>
#include <vector>
#include <mutex>
using std::isfinite;
using std::min;
using std::max;
inline uint32_t& fakeMillis() { static uint32_t t=0; return t; }
inline uint32_t millis() { return fakeMillis(); }
inline uint32_t micros() { return fakeMillis()*1000; }
inline void randomSeed(uint32_t){} inline uint32_t esp_random(){return 0;}
#define PROGMEM
struct __FlashStringHelper;
template<class V, class L, class H> V constrain(V v,L l,H h){ return v<l?l:(v>h?h:v); }
struct portMUX_TYPE {std::mutex value;};
#define portMUX_INITIALIZER_UNLOCKED {}
#define portENTER_CRITICAL(x) ((x)->value.lock())
#define portEXIT_CRITICAL(x) ((x)->value.unlock())
struct String:std::string {using std::string::string;using std::string::operator+=;size_t write(uint8_t c){push_back(char(c));return 1;}size_t write(const uint8_t* p,size_t n){append((const char*)p,n);return n;}long toInt()const{return strtol(c_str(),nullptr,10);} String& operator+=(int n){append(std::to_string(n));return *this;} String(int n):std::string(std::to_string(n)){} String operator+(int n)const {return String((std::string(*this)+std::to_string(n)).c_str());}};

inline long random(long maximum) {return maximum/2;}
inline long random(long minimum,long maximum) {return (minimum+maximum)/2;}
inline void analogReadResolution(int){} inline void analogSetPinAttenuation(int,int){}
inline int& fakeMilliVolts(){static int v=1500;return v;} inline int analogReadMilliVolts(int){return fakeMilliVolts();}
constexpr int ADC_11db=0, SERIAL_8N1=0;
struct SerialStub {void begin(int){}void println(const char*){} template<class... A>void printf(const char*,A...){}}; inline SerialStub Serial;
constexpr int pdPASS=1;
inline int pdMS_TO_TICKS(int ms){return ms;} inline void vTaskDelay(int){}
inline int xTaskCreatePinnedToCore(void(*)(void*),const char*,int,void*,int,void*,int){return pdPASS;}
