#pragma once
struct FakeMDNS{bool begin(const char*){return true;}void addService(const char*,const char*,int){}};inline FakeMDNS MDNS;
