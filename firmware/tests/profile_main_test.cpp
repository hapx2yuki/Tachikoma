#include <cassert>
#include <iostream>
#include <string>
#include "peripheral_probe.h"
#include "main.cpp"
static void tick(int n){for(int i=0;i<n;i++){fakeMillis()+=20;loop();}}
static void sustainStand(int n){for(int i=0;i<n;i++){pendingControl.stand=true;pendingControl.lastCmdMs=millis();tick(1);}}
#if TACHIKOMA_PRINT_FIRST_PROFILE
constexpr const char* EXPECTED_HARDWARE_PROFILE = "print-first";
#elif TACHIKOMA_STATUS_LEDS && TACHIKOMA_DFPLAYER
constexpr const char* EXPECTED_HARDWARE_PROFILE = "standard";
#elif !TACHIKOMA_STATUS_LEDS && !TACHIKOMA_DFPLAYER
constexpr const char* EXPECTED_HARDWARE_PROFILE = "reduced-peripherals";
#else
constexpr const char* EXPECTED_HARDWARE_PROFILE = "custom";
#endif
int main(int argc,char**){
 const bool missingPca=argc>1;
 if(missingPca)fakePCA[1].online=false;
 fakeMilliVolts()=1836;setup();loop();pwmEvents().clear();tick(30);
 for(const auto& e:pwmEvents())assert(e.us==0);
 assert(i2sConfig.sample_rate==AUDIO_SAMPLE_RATE && i2sConfig.bits_per_sample==32);
 assert(peripheralProbe.ledBegin==TACHIKOMA_STATUS_LEDS);
 assert(peripheralProbe.dfBegin==TACHIKOMA_DFPLAYER);
 JsonDocument doc;assert(!ws.messages.empty());assert(!deserializeJson(doc,ws.messages.back()));
 assert(doc["features"]["status_leds"].as<bool>()==STATUS_LEDS_ENABLED);
 assert(doc["features"]["dfplayer"].as<bool>()==DFPLAYER_ENABLED);
 assert(doc["features"]["i2s_audio"].as<bool>());
 assert(doc["features"]["audio_volume_percent"].as<int>()==TACHIKOMA_AUDIO_VOLUME_PERCENT);
 assert(doc["features"]["print_first_profile"].as<bool>()==PRINT_FIRST_PROFILE_ENABLED);
#if TACHIKOMA_PRINT_FIRST_PROFILE
 assert(doc["features"]["print_first_profile_status"].as<std::string>()==PRINT_FIRST_PROFILE_STATUS);
 assert(doc["features"]["print_first_profile_adopted"].as<bool>()==PRINT_FIRST_PROFILE_ADOPTED);
 assert(doc["features"]["body_h_min_mm"].as<float>()==PRINT_FIRST_BODY_H);
 assert(doc["features"]["body_h_max_mm"].as<float>()==PRINT_FIRST_BODY_H);
 assert(doc["features"]["body_h_default_mm"].as<float>()==PRINT_FIRST_BODY_H);
#else
 assert(doc["features"]["print_first_profile_status"].as<std::string>()=="INACTIVE");
 assert(!doc["features"]["print_first_profile_adopted"].as<bool>());
 assert(doc["features"]["body_h_min_mm"].as<float>()==BODY_H_MIN);
 assert(doc["features"]["body_h_max_mm"].as<float>()==BODY_H_MAX);
 assert(doc["features"]["body_h_default_mm"].as<float>()==BODY_H_DEF);
#endif
 // Keep the expected label independent from HARDWARE_PROFILE itself. This
 // catches peripheral-only builds being mislabeled as the walking profile.
 assert(doc["profile"].as<std::string>()==EXPECTED_HARDWARE_PROFILE);
 assert(std::string(HARDWARE_PROFILE)==EXPECTED_HARDWARE_PROFILE);
 assert(doc["i2c"].as<bool>()==!missingPca);
 AsyncWebServerRequest r;r.params.emplace("n",AsyncWebParameter("2"));r.params.emplace("confirm",AsyncWebParameter("1"));server.routes.at("/play")(&r);
 assert(r.code==(DFPLAYER_ENABLED?200:409));
 if(!DFPLAYER_ENABLED)assert(r.body.find("I2S voice remains available")!=std::string::npos);
 AsyncWebServerRequest missing;server.routes.at("/play")(&missing);
 assert(missing.code==400);
 for(const char* invalid : {"abc", "0", "-1", "10000"}){
   AsyncWebServerRequest bad;bad.params.emplace("n",AsyncWebParameter(invalid));
   server.routes.at("/play")(&bad);assert(bad.code==400);
 }
 tick(10);assert(peripheralProbe.dfPlay==(DFPLAYER_ENABLED?2:0));
 if(missingPca){
   pendingControl.stand=true;pendingControl.ptt=true;pendingControl.lastCmdMs=millis();
   tick(1);assert(!servos.ready() && servos.faulted());
   assert(!wsAudio.messages.empty() && wsAudio.messages.back().find("ptt_start")!=std::string::npos);
   std::cout<<"PASS: missing PCA keeps servo fault latched while I2S/PTT can be tested on the bench\n";
   return 0;
 }
#ifdef CALIBRATION_MODE
 AsyncWebServerRequest missingCal;server.routes.at("/cal")(&missingCal);
 assert(missingCal.code==400);
 AsyncWebServerRequest unsupportedCal;unsupportedCal.params.emplace("ch",AsyncWebParameter("12"));unsupportedCal.params.emplace("us",AsyncWebParameter("1500"));server.routes.at("/cal")(&unsupportedCal);
 assert(unsupportedCal.code==409);
 int calLow=0,calHigh=0;assert(servos.calibrationPulseRange(0,calLow,calHigh));
 AsyncWebServerRequest rangeCal;rangeCal.params.emplace("ch",AsyncWebParameter("0"));rangeCal.params.emplace("us",AsyncWebParameter("499"));server.routes.at("/cal")(&rangeCal);
 assert(rangeCal.code==400);
 AsyncWebServerRequest selectCal;selectCal.params.emplace("ch",AsyncWebParameter("0"));selectCal.params.emplace("us",AsyncWebParameter("1500"));selectCal.params.emplace("confirm",AsyncWebParameter("1"));server.routes.at("/cal")(&selectCal);
 assert(selectCal.code==200);pwmEvents().clear();tick(20);
 assert(!pwmEvents().empty());for(const auto& e:pwmEvents())if(e.us>0)assert(e.board==0&&e.ch==0);
 AsyncWebServerRequest stopCal;stopCal.params.emplace("stop",AsyncWebParameter("1"));server.routes.at("/cal")(&stopCal);
 assert(stopCal.code==200);pwmEvents().clear();tick(2);for(const auto& e:pwmEvents())assert(e.us==0);
 std::cout<<"PASS: calibration route requires ch+us, rejects unused/out-of-range values, and drives one axis only\n";
 return 0;
#endif
 // 本流の異常停止を省部品でも弱めない。手動起動後、低電圧をラッチさせる。
 sustainStand(180);assert(servos.ready());
 fakeMilliVolts()=0;tick(500);assert(peri.cutout());assert(!servos.ready());
 fakeMilliVolts()=1836;tick(150);assert(peri.cutout() && !servos.ready());
 std::cout<<"PASS: actual setup/loop telemetry and /play match profile; I2S remains; boot off and battery-loss latch remain\n";
}
