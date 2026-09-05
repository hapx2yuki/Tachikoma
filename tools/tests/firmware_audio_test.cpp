#include <Arduino.h>
#include <ArduinoJson.h>
#include <ESPAsyncWebServer.h>
#include <driver/i2s.h>
#include <atomic>
#include <cassert>
#include <cstring>
#include <iostream>
#define private public
#include "audio.h"
#undef private
int main(){
  Audio audio; AsyncWebSocket ws;audio.begin(&ws);
  assert(i2sConfig.bits_per_sample==32 && i2sConfig.bits_per_chan==32);
  assert(2*i2sConfig.bits_per_chan==64);
  std::cout<<"PASS: I2S uses DMA32 to avoid mono16 FIFO reordering; 64 BCLK per WS frame\n";
  AsyncWebSocketClient first(1),second(2);
  audio.onEvent(&ws,&first,WS_EVT_CONNECT,nullptr,nullptr,0);
  const char* begin="{\"type\":\"tts_begin\"}";
  audio.handleControl(begin,strlen(begin));
  audio.onEvent(&ws,&second,WS_EVT_CONNECT,nullptr,nullptr,0);
  assert(second.closed);
  audio.onEvent(&ws,&second,WS_EVT_DISCONNECT,nullptr,nullptr,0);
  assert(audio.playing_);
  std::cout<<"PASS: rejected second connection cannot reset the active playback\n";
  std::vector<uint8_t> source(20000),out(16384);
  for(size_t i=0;i<source.size();i+=2){source[i]=0x34;source[i+1]=0x12;}
  audio.pushPlayback(source.data(),source.size());
  size_t n=audio.popPlayback(out.data(),out.size());
  assert(n==16382 && !(n%2));
  for(size_t i=0;i<n;i+=2) assert(out[i]==0x34 && out[i+1]==0x12);
  audio.pushPlayback(source.data(),640);n=audio.popPlayback(out.data(),639);
  assert(n==638);for(size_t i=0;i<n;i+=2)assert(out[i]==0x34&&out[i+1]==0x12);
  audio.flushRing(); audio.pushPlayback(source.data(),639);assert(audio.popPlayback(out.data(),640)==0);
  std::cout<<"PASS: overflow/wrap/read sizes retain PCM16 sample boundaries; odd frames rejected\n";
  // 実 step() で 640 bytes のうち 128 bytes だけ書けた timeout を注入。
  audio.flushRing();audio.handleControl(begin,strlen(begin));
  audio.pushPlayback(source.data(),640);
  std::vector<uint8_t> played;int calls=0;
  fakeI2sWrite=[&](uint8_t* data,size_t length,size_t* written){
    *written=++calls==1?256:length;
    for(size_t i=0;i<*written;i+=4){assert(data[i]==0 && data[i+1]==0);played.push_back(data[i+2]);played.push_back(data[i+3]);}
    return calls==1?ESP_ERR_TIMEOUT:ESP_OK;
  };
  audio.step();assert(audio.txOffset_==128 && audio.txCount_==640);
  audio.step();assert(played.size()==640);
  assert(std::equal(played.begin(),played.end(),source.begin()));
  audio.handleControl("{\"type\":\"tts_end\"}",18);
  audio.step();assert(audio.playing_);fakeMillis()+=Audio::DMA_DRAIN_MS;audio.step();assert(!audio.playing_);
  std::cout<<"PASS: partial I2S writes preserve remaining PCM; drain waits until DMA tail is played\n";
  audio.handleControl(begin,strlen(begin));const uint32_t old=audio.generation_;
  audio.handleControl(begin,strlen(begin));audio.pushPlayback(source.data(),640);
  audio.resetPlayback(old);assert(audio.playing_);
  assert(audio.popPlayback(out.data(),640,old)==0);
  assert(audio.popPlayback(out.data(),640)==640);
  fakeI2sWrite=nullptr;
  audio.resetPlayback();audio.setPtt(true);
  uint32_t samples[]={0x12345600u,0x80000000u,0xffff0000u,0x7fff0000u};
  fakeI2sRead=[&](void* data,size_t,size_t* read){memcpy(data,samples,sizeof(samples));*read=sizeof(samples);return ESP_OK;};
  audio.step();fakeI2sRead=nullptr;
  const std::vector<uint8_t> expected={0x34,0x12,0x00,0x80,0xff,0xff,0xff,0x7f};
  assert(ws.binary==expected);
  std::cout<<"PASS: signed DMA32 samples become PCM16 in the original time order\n";
  std::cout<<"PASS: old playback completion/error cannot reset or consume a newly begun session\n";
  audio.handleControl(begin,strlen(begin));audio.pushPlayback(source.data(),640);
  fakeI2sWrite=[](uint8_t*,size_t,size_t* written){*written=0;return ESP_ERR_TIMEOUT;};
  audio.step();fakeMillis()+=1000;audio.step();assert(!audio.playing_);
  fakeI2sWrite=nullptr;
  std::cout<<"PASS: stalled I2S output aborts playback instead of permanently blocking PTT\n";
  audio.onEvent(&ws,&first,WS_EVT_DISCONNECT,nullptr,nullptr,0);
  assert(!audio.playing_ && audio.popPlayback(out.data(),640)==0 && audio.clearDma_);
  std::cout<<"PASS: owner disconnect clears playback; I2S reset is deferred to audio task\n";
}
