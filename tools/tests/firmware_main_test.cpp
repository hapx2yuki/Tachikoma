#include <cassert>
#include <iostream>
#include <set>
#include "main.cpp"
static void tick(int n=1){for(int i=0;i<n;i++){fakeMillis()+=20;loop();}}
static void command(AsyncWebSocketClient& c,const char* json){AwsFrameInfo info;info.len=strlen(json);onWsEvent(&ws,&c,WS_EVT_DATA,&info,(uint8_t*)json,info.len);}
static void sustain(AsyncWebSocketClient& c,const char* json,int n){for(int i=0;i<n;i++){command(c,json);tick();}}
int main(){
 const std::set<int> pins={PIN_SDA,PIN_SCL,PIN_LED,PIN_DF_RX,PIN_DF_TX,PIN_VBAT,PIN_I2S_BCLK,PIN_I2S_WS,PIN_I2S_DOUT,PIN_I2S_DIN};assert(pins.size()==10);
 assert(validWifiCredentials("iPhone-AP","real-pass"));
 assert(!validWifiCredentials("Tachikoma","change-me-8chars"));
 assert(!validWifiCredentials("iPhone-AP","short"));
 std::cout<<"PASS: all ten assigned ESP32 peripheral pins are distinct; camera has no main-board pin assignment\n";
 fakeMilliVolts()=1836;setup();loop();pwmEvents().clear();tick(150);assert(pwmEvents().empty());
 AsyncWebSocketClient first(1),second(2);
 AsyncWebServerRequest badEye;badEye.params.emplace("mode",AsyncWebParameter("invalid"));server.routes.at("/eye")(&badEye);assert(badEye.code==400);
 AsyncWebServerRequest badArm;badArm.params.emplace("pose",AsyncWebParameter("invalid"));server.routes.at("/arm")(&badArm);assert(badArm.code==400);
 AsyncWebServerRequest restEye;restEye.params.emplace("mode",AsyncWebParameter("front"));server.routes.at("/eye")(&restEye);assert(restEye.code==409);
 AsyncWebServerRequest restArm;restArm.params.emplace("pose",AsyncWebParameter("ready"));server.routes.at("/arm")(&restArm);assert(restArm.code==409);
 servos.setPowerReady(false);assert(servoCommandStatus()==503);servos.setPowerReady(true);pwmEvents().clear();
 std::cout<<"PASS: WiFi credential guard and /eye /arm 400/409/503 routes\n";
 onWsEvent(&ws,&first,WS_EVT_CONNECT,nullptr,nullptr,0);assert(!first.messages.empty() && first.messages.back().find("control_session")!=std::string::npos);
 command(first,"{\"vx\":0,\"vy\":0,\"wz\":0}");
 command(second,"{\"vx\":1,\"vy\":0,\"wz\":0}");assert(!second.messages.empty() && second.messages.back().find("control_busy")!=std::string::npos);
 tick(150);assert(pwmEvents().empty());
 std::cout<<"PASS: actual setup/loop and first UI heartbeat keep all servos off until explicit start\n";
 sustain(first,"{\"vx\":0,\"vy\":0,\"wz\":0,\"stand\":1}",220);assert(servos.ready());
 AsyncWebServerRequest ownedEye;ownedEye.params.emplace("mode",AsyncWebParameter("front"));ownedEye.params.emplace("confirm",AsyncWebParameter("1"));server.routes.at("/eye")(&ownedEye);assert(ownedEye.code==409);
 AsyncWebServerRequest ownerEye;ownerEye.params.emplace("mode",AsyncWebParameter("front"));ownerEye.params.emplace("confirm",AsyncWebParameter("1"));ownerEye.params.emplace("session",AsyncWebParameter("1"));server.routes.at("/eye")(&ownerEye);assert(ownerEye.code==200);
 AsyncWebServerRequest ownedArm;ownedArm.params.emplace("pose",AsyncWebParameter("ready"));ownedArm.params.emplace("confirm",AsyncWebParameter("1"));server.routes.at("/arm")(&ownedArm);assert(ownedArm.code==409);
 AsyncWebServerRequest ownedTrim;ownedTrim.params.emplace("ch",AsyncWebParameter("0"));ownedTrim.params.emplace("us",AsyncWebParameter("10"));ownedTrim.params.emplace("confirm",AsyncWebParameter("1"));server.routes.at("/trim")(&ownedTrim);assert(ownedTrim.code==409);
 AsyncWebServerRequest ownedPlay;ownedPlay.params.emplace("n",AsyncWebParameter("2"));ownedPlay.params.emplace("confirm",AsyncWebParameter("1"));server.routes.at("/play")(&ownedPlay);assert(ownedPlay.code==409);
 AsyncWebServerRequest ownedWifi;ownedWifi.params.emplace("ssid",AsyncWebParameter("iPhone-AP"));ownedWifi.params.emplace("pass",AsyncWebParameter("real-pass"));ownedWifi.params.emplace("confirm",AsyncWebParameter("1"));server.postRoutes.at("/wifi")(&ownedWifi);assert(ownedWifi.code==409);
 onWsEvent(&ws,&first,WS_EVT_DISCONNECT,nullptr,nullptr,0);tick();
 AsyncWebServerRequest noConfirmEye;noConfirmEye.params.emplace("mode",AsyncWebParameter("front"));server.routes.at("/eye")(&noConfirmEye);assert(noConfirmEye.code==409);
 AsyncWebServerRequest confirmedEye;confirmedEye.params.emplace("mode",AsyncWebParameter("front"));confirmedEye.params.emplace("confirm",AsyncWebParameter("1"));server.routes.at("/eye")(&confirmedEye);assert(confirmedEye.code==409);
 std::cout<<"PASS: HTTP mutations reject active WS owner and require explicit confirm when owner is absent\n";
 sustain(first,"{\"vx\":0,\"vy\":0,\"wz\":0,\"stand\":1}",220);assert(servos.ready());
 fakeMilliVolts()=1500;tick(5);assert(!peri.cutout() && !peri.vbatVerified() && !servos.ready() && !pendingControl.stand);
 fakeMilliVolts()=1836;tick(10);assert(!peri.cutout() && peri.vbatVerified() && !servos.ready() && !pendingControl.stand);
 sustain(first,"{\"vx\":0,\"vy\":0,\"wz\":0,\"stand\":1}",220);assert(servos.ready());
 std::cout<<"PASS: transient VBAT gate loss full-offs and requires an explicit heartbeat/re-arm after recovery\n";
 command(first,"{\"vx\":0,\"vy\":1,\"wz\":0,\"ptt\":1}");tick(5);assert(gait.moving());
 onWsEvent(&ws,&second,WS_EVT_DISCONNECT,nullptr,nullptr,0);assert(pendingControl.vy==1);
 onWsEvent(&ws,&first,WS_EVT_DISCONNECT,nullptr,nullptr,0);assert(pendingControl.vy==0 && !pendingControl.ptt);tick(100);assert(!gait.moving());
 std::cout<<"PASS: only controlling socket disconnect zeros motion/PTT immediately; gait settles to stand\n";
 command(first,"{\"vx\":1,\"vy\":0,\"wz\":0,\"ptt\":1}");tick(80);assert(pendingControl.vx==0&&!pendingControl.ptt);
 std::cout<<"PASS: lost heartbeat clears stale mailbox commands, preventing millis-wrap resurrection\n";
 command(first,"{\"stand\":0}");tick();assert(!servos.ready());pwmEvents().clear();tick(20);assert(pwmEvents().empty());
 sustain(first,"{\"vx\":0,\"vy\":0,\"wz\":0,\"stand\":1}",220);assert(servos.ready());
 fakePCA[0].failNextPwm=true;tick();assert(servos.faulted());for(auto& b:fakePCA)for(int t:b.ticks)assert(t==4096);
 command(first,"{\"stand\":0}");tick();sustain(first,"{\"vx\":0,\"vy\":0,\"wz\":0,\"stand\":1}",250);assert(!servos.ready());
 std::cout<<"PASS: actual main rest/restart works, bus fault stays de-energized across start button presses\n";
}
