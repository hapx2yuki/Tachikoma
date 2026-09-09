#pragma once
#include "Arduino.h"
#include <functional>
#include <map>
enum AwsEventType {WS_EVT_CONNECT,WS_EVT_DISCONNECT,WS_EVT_DATA};
constexpr int WS_TEXT=1,WS_BINARY=2;
struct AwsFrameInfo {bool final=true;size_t index=0,len=0;int opcode=WS_TEXT;};
class AsyncWebSocketClient {
 uint32_t id_;
 public:
  bool closed=false;std::vector<std::string> messages;
  AsyncWebSocketClient(uint32_t id):id_(id){} uint32_t id(){return id_;}
  void close(){closed=true;}
  void text(const char* value){messages.emplace_back(value ? value : "");}
};
class AsyncWebSocket {public:std::vector<uint8_t> binary;AsyncWebSocket(const char* path=""){} template<class T>void onEvent(T){} void cleanupClients(){} void textAll(const String&){} int count(){return 1;} void textAll(const char*){} void binaryAll(uint8_t* data,size_t n){binary.insert(binary.end(),data,data+n);} };

constexpr int HTTP_GET=1,HTTP_POST=2;
class AsyncWebParameter {String value_;public:AsyncWebParameter(const char* s=""):value_(s){}String value(){return value_;}};
class AsyncWebServerRequest {
 public:
  int code=0;std::string body;std::map<std::string,AsyncWebParameter> params;
  bool hasParam(const char* n,bool=false){return params.count(n)>0;}
  AsyncWebParameter* getParam(const char* n,bool=false){return &params.at(n);}
  void send(int status,const char*,const char* value){code=status;body=value;}
  void send(int status,const char*,const String& value){code=status;body=value;}
  void send(int status,const char*,const __FlashStringHelper*){code=status;}
};
class AsyncWebServer {
 public:
  std::map<std::string,std::function<void(AsyncWebServerRequest*)>> routes;
  std::map<std::string,std::function<void(AsyncWebServerRequest*)>> postRoutes;
  AsyncWebServer(int){} void addHandler(AsyncWebSocket*){}
  template<class T>void on(const char* path,int method,T callback){
    if(method==HTTP_GET) routes[path]=callback;
    else if(method==HTTP_POST) postRoutes[path]=callback;
  }
  void begin(){}
};
