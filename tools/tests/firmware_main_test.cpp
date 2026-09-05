#include <cassert>
#include <iostream>
#include <set>
#include "main.cpp"
static void tick(int n=1){for(int i=0;i<n;i++){fakeMillis()+=20;loop();}}
static void command(AsyncWebSocketClient& c,const char* json){AwsFrameInfo info;info.len=strlen(json);onWsEvent(&ws,&c,WS_EVT_DATA,&info,(uint8_t*)json,info.len);}
int main(){
 const std::set<int> pins={PIN_SDA,PIN_SCL,PIN_LED,PIN_DF_RX,PIN_DF_TX,PIN_VBAT,PIN_I2S_BCLK,PIN_I2S_WS,PIN_I2S_DOUT,PIN_I2S_DIN};assert(pins.size()==10);
 std::cout<<"PASS: all ten assigned ESP32 peripheral pins are distinct; camera has no main-board pin assignment\n";
 fakeMilliVolts()=1836;setup();loop();pwmEvents().clear();tick(150);assert(pwmEvents().empty());
 AsyncWebSocketClient first(1),second(2);
 command(first,"{\"vx\":0,\"vy\":0,\"wz\":0}");tick(150);assert(pwmEvents().empty());
 std::cout<<"PASS: actual setup/loop and first UI heartbeat keep all servos off until explicit start\n";
 command(first,"{\"stand\":1}");tick(120);assert(servos.ready());
 command(first,"{\"vx\":0,\"vy\":1,\"wz\":0,\"ptt\":1}");tick(5);assert(gait.moving());
 onWsEvent(&ws,&second,WS_EVT_DISCONNECT,nullptr,nullptr,0);assert(pendingControl.vy==1);
 onWsEvent(&ws,&first,WS_EVT_DISCONNECT,nullptr,nullptr,0);assert(pendingControl.vy==0 && !pendingControl.ptt);tick(100);assert(!gait.moving());
 std::cout<<"PASS: only controlling socket disconnect zeros motion/PTT immediately; gait settles to stand\n";
 command(first,"{\"vx\":1,\"vy\":0,\"wz\":0,\"ptt\":1}");tick(80);assert(pendingControl.vx==0&&!pendingControl.ptt);
 std::cout<<"PASS: lost heartbeat clears stale mailbox commands, preventing millis-wrap resurrection\n";
 command(first,"{\"stand\":0}");tick();assert(!servos.ready());pwmEvents().clear();tick(20);assert(pwmEvents().empty());
 command(first,"{\"stand\":1}");tick(120);assert(servos.ready());
 fakePCA[0].failNextPwm=true;tick();assert(servos.faulted());for(auto& b:fakePCA)for(int t:b.ticks)assert(t==4096);
 command(first,"{\"stand\":0}");tick();command(first,"{\"stand\":1}");tick(150);assert(!servos.ready());
 std::cout<<"PASS: actual main rest/restart works, bus fault stays de-energized across start button presses\n";
}
