#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPAsyncWebServer.h>
#include <driver/i2s.h>
#include <atomic>
#include <cassert>
#include <iostream>
#define private public
#include "audio.h"
#undef private
int main(){
 Audio a;AsyncWebSocket ws;a.begin(&ws);
 const int32_t values[]={-32768,-12345,-1,0,1,12345,32767};
 std::vector<uint8_t> input;
 for(int32_t v:values){const auto raw=(uint16_t)v;input.push_back(raw);input.push_back(raw>>8);}
 a.handleControl("{\"type\":\"tts_begin\"}",20);a.pushPlayback(input.data(),input.size());
 std::vector<int32_t> output;int calls=0;
 fakeI2sWrite=[&](uint8_t* d,size_t n,size_t* written){
  *written=++calls==1?4:n;
  for(size_t i=0;i<*written;i+=4){
   assert(d[i]==0 && d[i+1]==0);uint16_t raw=d[i+2]|(uint16_t(d[i+3])<<8);
   output.push_back(raw<0x8000u?raw:int32_t(raw)-0x10000);
  }
  return calls==1?ESP_ERR_TIMEOUT:ESP_OK;
 };
 a.step();a.step();assert(output.size()==7);
 for(size_t i=0;i<7;++i)assert(output[i]==values[i]*TACHIKOMA_AUDIO_VOLUME_PERCENT/100);
 std::cout<<"PASS: PCM gain "<<TACHIKOMA_AUDIO_VOLUME_PERCENT<<"% preserves sign/full-scale/sample order across partial writes\n";
}
