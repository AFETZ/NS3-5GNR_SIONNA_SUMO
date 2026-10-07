#ifndef SUMOSENSOR_H
#define SUMOSENSOR_H

#include "ns3/ldm-utils.h"
#include "ns3/phPoints.h"
#include "ns3/core-module.h"
#include "ns3/traci-client.h"
#include "ns3/vdpTraci.h"
#include "ns3/LDM.h"
#include <unordered_map>
#include <vector>
#include <random>
#include <shared_mutex>
#include <fstream>
#include <boost/geometry.hpp>

namespace ns3 {

  typedef boost::geometry::model::point<double, 2, boost::geometry::cs::cartesian> point_type;

  using polygon_type = boost::geometry::model::polygon<point_type>;
  using linestring_type = boost::geometry::model::linestring<point_type>;

  /**
   * \ingroup automotive
   * \brief This class implements a sensor that detects vehicles in its vicinity for a given SUMO vehicle.
   *
   * This class provides capabilities for detecting vehicles in the vicinity of a SUMO vehicle.
   */
  class SUMOSensor : public Object
  {
  public:
    /**
     * @brief Construct a new SUMOSensor object.
     */
    SUMOSensor();
    ~SUMOSensor();

    /**
     * @brief Set the station ID.
     *
     * @param id The station ID.
     */
    void setStationID(std::string id){
      m_id=id;m_stationID=std::stol(id.substr (3));
      // Stable, disjoint streams: independent of object creation/event order.
      const int64_t stream = 100000 + static_cast<int64_t>(m_stationID) * 8;
      m_distanceNoise->SetStream(stream);
      m_angleNoise->SetStream(stream + 1);
      m_speedNoise->SetStream(stream + 2);
    }
    /**
     * @brief Set the TraCI client.
     *
     * @param client The TraCI client.
     */
    void setTraCIclient(Ptr<TraciClient> client){
      m_client=client;
      m_event_updateDetectedObjects = Simulator::Schedule(MilliSeconds (100),&SUMOSensor::updateDetectedObjects,this);
    }
    /**
     * @brief Set the VDP object.
     *
     * @param vdp The VDP object.
     */
    void setVDP(VDP* vdp) {m_vdp=vdp;}
    /**
     * @brief Set the sensor perception range. (Default = 50 meters)
     *
     * @param sensorRange The sensor range.
     */
    void setSensorRange(double sensorRange){m_sensorRange = sensorRange;}
    /**
     * @brief Get the detected objects and update the LDM.
     *
     */
    void updateDetectedObjects();

    // Local sensor output is independent of CAM/CPM classification in the LDM.
    const std::vector<vehicleData_t>& getLocalDetections() const { return m_localDetections; }
    void setLogFile(const std::string& path) {
      m_sensorLog.open(path, std::ofstream::trunc);
      if (!m_sensorLog) { NS_FATAL_ERROR("Cannot open sensor log: " << path); }
      m_sensorLog << "time_s,vehicle_id,object_id,x_m,y_m,speed_mps,heading_deg,range_m\n";
    }

    /**
     * @brief Set the LDM object.
     * @param ldm
     */
    void setLDM(Ptr<LDM> ldm){m_LDM = ldm;}
    libsumo::TraCIPosition boost2TraciPos(point_type point_type);

    void cleanup();

  private:
        //Compute defining points of a vehicle with StationID id
        vehiclePoints_t adjust(std::string id);
        //Create gaussian noise for distance sensor measurements
        double distance_noise();

        //TraCI client pointer
        Ptr<TraciClient> m_client; //!< TraCI client

        uint64_t m_stationID;
        std::string m_id;
        VDP* m_vdp;

        Ptr<LDM> m_LDM;

        EventId m_event_updateDetectedObjects;

        double m_sensorRange; ///! Sensor range in meters

        const double m_mean = 0.0; ///! Mean of the perception's noise
        const double m_stddev_distance = 0.5; ///! Standard deviation of the perception's distance noise
        const double m_stddev_angle = 0.2; ///! Standard deviation of the perception's angle noise
        const double m_stddev_speed = 0.2; ///! Standard deviation of the perception's speed noise
        double m_avg_dwell = 0.0;
        int m_dwell_count = 0;

        std::vector<vehicleData_t> m_localDetections;
        std::ofstream m_sensorLog;
        Ptr<NormalRandomVariable> m_distanceNoise;
        Ptr<NormalRandomVariable> m_angleNoise;
        Ptr<NormalRandomVariable> m_speedNoise;
  };
}
#endif // SUMOSENSOR_H
