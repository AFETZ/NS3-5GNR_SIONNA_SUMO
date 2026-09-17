#ifndef SENSOR_KINEMATICS_H
#define SENSOR_KINEMATICS_H

#include <cmath>
#include <utility>

namespace ns3 {
// SUMO headings are degrees clockwise from north, not Cartesian polar angles.
inline std::pair<double, double>
SumoVelocityXY (double speed, double headingDegrees)
{
  const double angle = headingDegrees * std::acos (-1.0) / 180.0;
  return {speed * std::sin (angle), speed * std::cos (angle)};
}

inline long
SensorCentiUnits (double value)
{
  return std::lround (value * 100.0);
}
} // namespace ns3
#endif
