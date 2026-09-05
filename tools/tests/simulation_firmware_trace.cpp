// 実際のfirmwareヘッダーをホスト実行し、Python側の写しとは独立に出力する。
#include <cstdint>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <string>
#include <algorithm>
template<class T> T constrain(T x, T a, T b) { return std::min(b, std::max(a,x)); }
#include "../../firmware/src/leg_output.h"
int main() {
  Gait gait;
  LegOutput output;
  float dt, vx, vy, wz;
  std::cout << std::setprecision(10);
  std::string line;
  while (std::getline(std::cin,line)) {
    std::istringstream input(line);
    if (!(input >> dt >> vx >> vy >> wz)) return 2;
    float height=BODY_H_DEF; input >> height; gait.bodyH=height;
    LegCmd target[4] = {};
    gait.update(dt, vx, vy, wz, target);
    output.update(dt, target);
    std::cout << gait.phase() << ' ' << gait.moving();
    for(int leg=0;leg<4;++leg)
      std::cout << ' ' << target[leg].ang.yaw << ' ' << target[leg].ang.pitch << ' ' << target[leg].ang.knee;
    for(int leg=0;leg<4;++leg)
      std::cout << ' ' << output.angle(leg).yaw << ' ' << output.angle(leg).pitch << ' ' << output.angle(leg).knee;
    std::cout << '\n';
  }
}
