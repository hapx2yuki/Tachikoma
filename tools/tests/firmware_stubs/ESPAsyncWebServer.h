#pragma once
#include "Arduino.h"
enum AwsEventType {WS_EVT_CONNECT,WS_EVT_DISCONNECT,WS_EVT_DATA};
constexpr int WS_TEXT=1,WS_BINARY=2;
struct AwsFrameInfo {bool final=true;size_t index=0,len=0;int opcode=WS_TEXT;};
class AsyncWebSocketClient {uint32_t id_;public:bool closed=false; AsyncWebSocketClient(uint32_t id):id_(id){} uint32_t id(){return id_;} void close(){closed=true;} void text(const char*){} };
class AsyncWebSocket {public:std::vector<uint8_t> binary;AsyncWebSocket(const char* path=""){} template<class T>void onEvent(T){} void cleanupClients(){} void textAll(const String&){} int count(){return 1;} void textAll(const char*){} void binaryAll(uint8_t* data,size_t n){binary.insert(binary.end(),data,data+n);} };

constexpr int HTTP_GET=1,HTTP_POST=2;
class AsyncWebParameter {public:String value(){return "";}};
class AsyncWebServerRequest {public:bool hasParam(const char*,bool=false){return false;} AsyncWebParameter* getParam(const char*,bool=false){static AsyncWebParameter p;return &p;}template<class T>void send(int,const char*,T){}};
class AsyncWebServer {public:AsyncWebServer(int){} void addHandler(AsyncWebSocket*){} template<class T>void on(const char*,int,T){} void begin(){}};
