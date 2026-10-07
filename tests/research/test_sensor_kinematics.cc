#include "../../src/automotive/model/utilities/sensor-kinematics.h"
#include <cassert>
#include <cmath>
int main() {
  const double headings[] = {0, 90, 180, 270};
  const double vx[] = {0, 12.34, 0, -12.34};
  const double vy[] = {12.34, 0, -12.34, 0};
  for (int i=0; i<4; ++i) {
    auto v = ns3::SumoVelocityXY(12.34, headings[i]);
    assert(std::abs(v.first-vx[i]) < 1e-10);
    assert(std::abs(v.second-vy[i]) < 1e-10);
  }
  assert(ns3::SensorCentiUnits(0.37)==37);
  assert(ns3::SensorCentiUnits(-0.37)==-37);
  assert(ns3::SensorCentiUnits(12.345)==1235);
}
