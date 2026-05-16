/* -*- Mode:C++; c-file-style:"gnu"; indent-tabs-mode:nil; -*- */
/*
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License version 2 as
 * published by the Free Software Foundation;
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

 * Created by:
 *  Marco Malinverno, Politecnico di Torino (marco.malinverno1@gmail.com)
 *  Francesco Raviglione, Politecnico di Torino (francescorav.es483@gmail.com)
 *  Carlos Mateo Risma Carletti, Politecnico di Torino (carlosrisma@gmail.com)
*/

#include "emergencyVehicleAlert.h"

#include "ns3/CAM.h"
#include "ns3/DENM.h"
#include "ns3/socket.h"
#include "ns3/network-module.h"
#include "ns3/gn-utils.h"
#include <algorithm>
#include <cctype>
#include <cmath>
#include <iomanip>
#include <limits>
#include <sstream>
#include <vector>

#define DEG_2_RAD(val) ((val)*M_PI/180.0)

namespace ns3
{

  NS_LOG_COMPONENT_DEFINE("emergencyVehicleAlert");

  NS_OBJECT_ENSURE_REGISTERED(emergencyVehicleAlert);
  constexpr uint16_t CAM_PORT = 2001;
  constexpr uint16_t CPM_PORT = 2009;
  std::vector<emergencyVehicleAlert*> emergencyVehicleAlert::s_active_instances;

  namespace
  {
    std::string
    TrimCopy (const std::string& input)
    {
      size_t begin = 0;
      while (begin < input.size () &&
             std::isspace (static_cast<unsigned char> (input[begin])))
        {
          ++begin;
        }
      size_t end = input.size ();
      while (end > begin &&
             std::isspace (static_cast<unsigned char> (input[end - 1])))
        {
          --end;
        }
      return input.substr (begin, end - begin);
    }

    std::vector<std::string>
    SplitByDelimiter (const std::string& input, char delimiter)
    {
      std::vector<std::string> parts;
      std::stringstream ss (input);
      std::string item;
      while (std::getline (ss, item, delimiter))
        {
          parts.push_back (item);
        }
      return parts;
    }

    bool
    TryParseFiniteDouble (const std::string& token, double& valueOut)
    {
      try
        {
          size_t consumed = 0;
          const double parsed = std::stod (token, &consumed);
          if (consumed != token.size () || !std::isfinite (parsed))
            {
              return false;
            }
          valueOut = parsed;
          return true;
        }
      catch (const std::exception&)
        {
          return false;
        }
    }

    bool
    TryParseStationIdToken (const std::string& token, int64_t& stationIdOut)
    {
      std::string normalized = TrimCopy (token);
      if (normalized.empty ())
        {
          return false;
        }
      if (normalized.rfind ("veh", 0) == 0 && normalized.size () > 3)
        {
          normalized = normalized.substr (3);
        }
      try
        {
          size_t consumed = 0;
          const long parsed = std::stol (normalized, &consumed);
          if (consumed != normalized.size () || parsed < 0)
            {
              return false;
            }
          stationIdOut = static_cast<int64_t> (parsed);
          return true;
        }
      catch (const std::exception&)
        {
          return false;
        }
    }
  } // namespace

  // Function to compute the distance between two objects, given their Lon/Lat
  double appUtil_haversineDist(double lat_a, double lon_a, double lat_b, double lon_b) {
      // 12742000 is the mean Earth radius (6371 km) * 2 * 1000 (to convert from km to m)
      return 12742000.0*asin(sqrt(sin(DEG_2_RAD(lat_b-lat_a)/2)*sin(DEG_2_RAD(lat_b-lat_a)/2)+cos(DEG_2_RAD(lat_a))*cos(DEG_2_RAD(lat_b))*sin(DEG_2_RAD(lon_b-lon_a)/2)*sin(DEG_2_RAD(lon_b-lon_a)/2)));
  }

  // Function to compute the absolute difference between two angles (angles must be between -180 and 180)
  double appUtil_angDiff(double ang1, double ang2) {
      double angDiff;
      angDiff=ang1-ang2;

      if(angDiff>180)
      {
        angDiff-=360;
      }
      else if(angDiff<-180)
      {
        angDiff+=360;
      }
      return std::abs(angDiff);
  }

  TypeId
  emergencyVehicleAlert::GetTypeId (void)
  {
    static TypeId tid =
        TypeId ("ns3::emergencyVehicleAlert")
        .SetParent<Application> ()
        .SetGroupName ("Applications")
        .AddConstructor<emergencyVehicleAlert> ()
        .AddAttribute ("RealTime",
            "To compute properly timestamps",
            BooleanValue(false),
            MakeBooleanAccessor (&emergencyVehicleAlert::m_real_time),
            MakeBooleanChecker ())
        .AddAttribute ("IpAddr",
            "IpAddr",
            Ipv4AddressValue ("10.0.0.1"),
            MakeIpv4AddressAccessor (&emergencyVehicleAlert::m_ipAddress),
            MakeIpv4AddressChecker ())
        .AddAttribute ("PrintSummary",
            "To print summary at the end of simulation",
            BooleanValue(false),
            MakeBooleanAccessor (&emergencyVehicleAlert::m_print_summary),
            MakeBooleanChecker ())
        .AddAttribute ("CSV",
            "CSV log name",
            StringValue (),
            MakeStringAccessor (&emergencyVehicleAlert::m_csv_name),
            MakeStringChecker ())
        .AddAttribute ("Model",
            "Physical and MAC layer communication model",
            StringValue (""),
            MakeStringAccessor (&emergencyVehicleAlert::m_model),
            MakeStringChecker ())
        .AddAttribute ("Client",
            "TraCI client for SUMO",
            PointerValue (0),
            MakePointerAccessor (&emergencyVehicleAlert::m_client),
            MakePointerChecker<TraciClient> ())
        .AddAttribute ("MetricSupervisor",
            "Metric Supervisor to compute metrics according to 3GPP TR36.885 V14.0.0 page 70",
            PointerValue (0),
            MakePointerAccessor (&emergencyVehicleAlert::m_metric_supervisor),
            MakePointerChecker<MetricSupervisor> ())
        .AddAttribute ("SendCAM",
            "To enable/disable the transmission of CAM messages",
            BooleanValue(true),
            MakeBooleanAccessor (&emergencyVehicleAlert::m_send_cam),
            MakeBooleanChecker ())
        .AddAttribute ("SendCPM",
           "To enable/disable the transmission of CPM messages",
           BooleanValue(true),
           MakeBooleanAccessor (&emergencyVehicleAlert::m_send_cpm),
           MakeBooleanChecker ())
        .AddAttribute ("RxDropProbCam",
           "Probability to drop received CAM messages at the application level",
           DoubleValue (0.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_rx_drop_prob_cam),
           MakeDoubleChecker<double> (0.0, 1.0))
        .AddAttribute ("RxDropProbCpm",
           "Probability to drop received CPM messages at the application level",
           DoubleValue (0.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_rx_drop_prob_cpm),
           MakeDoubleChecker<double> (0.0, 1.0))
        .AddAttribute ("RxDropProbPhyCam",
           "Probability to drop received CAM messages before upper layers (PHY/MAC emulation)",
           DoubleValue (0.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_rx_drop_prob_phy_cam),
           MakeDoubleChecker<double> (0.0, 1.0))
        .AddAttribute ("RxDropProbPhyCpm",
           "Probability to drop received CPM messages before upper layers (PHY/MAC emulation)",
           DoubleValue (0.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_rx_drop_prob_phy_cpm),
           MakeDoubleChecker<double> (0.0, 1.0))
        .AddAttribute ("DropTriggeredReactionEnable",
           "If true, a dropped CAM/CPM may still trigger evasive reaction. "
           "If false, drop events produce strict drop_decision_no_action only.",
           BooleanValue (false),
           MakeBooleanAccessor (&emergencyVehicleAlert::m_drop_triggered_reaction_enable),
           MakeBooleanChecker ())
        .AddAttribute ("TargetLossProfileEnable",
           "Enable per-vehicle RX drop override profile",
           BooleanValue (false),
           MakeBooleanAccessor (&emergencyVehicleAlert::m_target_loss_profile_enable),
           MakeBooleanChecker ())
        .AddAttribute ("TargetLossVehicleId",
           "Vehicle id for per-vehicle RX drop override",
           StringValue ("veh9"),
           MakeStringAccessor (&emergencyVehicleAlert::m_target_loss_vehicle_id),
           MakeStringChecker ())
        .AddAttribute ("TargetLossRxDropProbCam",
           "Per-vehicle application-level CAM drop probability override",
           DoubleValue (1.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_target_loss_rx_drop_prob_cam),
           MakeDoubleChecker<double> (0.0, 1.0))
        .AddAttribute ("TargetLossRxDropProbCpm",
           "Per-vehicle application-level CPM drop probability override",
           DoubleValue (0.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_target_loss_rx_drop_prob_cpm),
           MakeDoubleChecker<double> (0.0, 1.0))
        .AddAttribute ("TargetLossRxDropProbPhyCam",
           "Per-vehicle PHY-level CAM drop probability override",
           DoubleValue (1.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_target_loss_rx_drop_prob_phy_cam),
           MakeDoubleChecker<double> (0.0, 1.0))
        .AddAttribute ("TargetLossRxDropProbPhyCpm",
           "Per-vehicle PHY-level CPM drop probability override",
           DoubleValue (0.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_target_loss_rx_drop_prob_phy_cpm),
           MakeDoubleChecker<double> (0.0, 1.0))
        .AddAttribute ("PerVehiclePrrProfile",
           "Comma/semicolon-separated per-vehicle PHY profile entries: "
           "vehId:rxDropPhyCam[:equivTxPowerDbm[:targetPrr[:rxDropPhyCpm]]]",
           StringValue (std::string ()),
           MakeStringAccessor (&emergencyVehicleAlert::m_per_vehicle_prr_profile),
           MakeStringChecker ())
        .AddAttribute ("CamSilenceDropInferenceEnable",
           "Infer CAM loss events from sequence/time gaps instead of synthetic RxDropProbPhy",
           BooleanValue (false),
           MakeBooleanAccessor (&emergencyVehicleAlert::m_cam_silence_drop_inference_enable),
           MakeBooleanChecker ())
        .AddAttribute ("CamSilenceFocusTxId",
           "StationId whose CAM stream is used for silence-based PHY loss inference",
           IntegerValue (2),
           MakeIntegerAccessor (&emergencyVehicleAlert::m_cam_silence_focus_tx_id),
           MakeIntegerChecker<int64_t> ())
        .AddAttribute ("CamSilenceExpectedPeriodMs",
           "Expected CAM period [ms] for silence-based loss inference",
           DoubleValue (600.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_cam_silence_expected_period_ms),
           MakeDoubleChecker<double> (1.0))
        .AddAttribute ("CamSilenceTimeoutS",
           "No-CAM timeout [s] after which a loss event is inferred",
           DoubleValue (0.95),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_cam_silence_timeout_s),
           MakeDoubleChecker<double> (0.01))
        .AddAttribute ("CamSilenceBootstrapTimeS",
           "Start evaluating silence-based loss inference only after this simulation time [s]",
           DoubleValue (2.2),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_cam_silence_bootstrap_time_s),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("ReactionDistanceThreshold",
           "Distance threshold [m] to trigger CAM-based evasive action",
           DoubleValue (75.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_distance_threshold),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("ReactionHeadingThreshold",
           "Heading difference threshold [deg] to trigger CAM-based evasive action",
           DoubleValue (45.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_heading_threshold),
           MakeDoubleChecker<double> (0.0, 180.0))
        .AddAttribute ("ReactionTargetLane",
           "Target lane index for CAM-based evasive lane change",
           IntegerValue (0),
           MakeIntegerAccessor (&emergencyVehicleAlert::m_reaction_target_lane),
           MakeIntegerChecker<int> (0))
        .AddAttribute ("ReactionSpeedFactorTargetLane",
           "Speed factor applied if vehicle is already in ReactionTargetLane",
           DoubleValue (0.5),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_reaction_speed_factor_target_lane),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("ReactionSpeedFactorOtherLane",
           "Speed factor applied if vehicle changes to ReactionTargetLane",
           DoubleValue (1.5),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_reaction_speed_factor_other_lane),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("ReactionActionDurationS",
           "Duration [s] for temporary lane/speed adaptation after CAM trigger",
           DoubleValue (3.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_reaction_action_duration_s),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("CpmReactionDistanceThreshold",
           "Distance threshold [m] to consider a CPM object for evasive control",
           DoubleValue (60.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_cpm_distance_threshold),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("CpmReactionTtcThresholdS",
           "TTC threshold [s] to trigger CPM-based evasive control",
           DoubleValue (3.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_cpm_ttc_threshold_s),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("SensorReactionEnable",
           "Enable local sensor-based evasive control from locally detected POs",
           BooleanValue (false),
           MakeBooleanAccessor (&emergencyVehicleAlert::m_sensor_reaction_enable),
           MakeBooleanChecker ())
        .AddAttribute ("SensorReactionDistanceThresholdM",
           "Distance threshold [m] to consider local sensor detections for evasive control",
           DoubleValue (20.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_sensor_reaction_distance_threshold_m),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("SensorReactionTtcThresholdS",
           "TTC threshold [s] to trigger local sensor-based evasive control",
           DoubleValue (1.5),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_sensor_reaction_ttc_threshold_s),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("SensorReactionPeriodMs",
           "Polling period [ms] for evaluating local sensor hazards",
           DoubleValue (100.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_sensor_reaction_period_ms),
           MakeDoubleChecker<double> (1.0))
        .AddAttribute ("SensorRangeM",
           "Local SUMO sensor range [m]",
           DoubleValue (50.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_sensor_range_m),
           MakeDoubleChecker<double> (1.0))
        .AddAttribute ("SensorReactionFocusVehicleId",
           "Optional vehicle id filter for local sensor reaction (e.g., veh2). Empty = any detected object",
           StringValue (std::string ()),
           MakeStringAccessor (&emergencyVehicleAlert::m_sensor_reaction_focus_vehicle_id),
           MakeStringChecker ())
        .AddAttribute ("ReactionActionCooldownS",
           "Minimum interval [s] between consecutive evasive control actions",
           DoubleValue (0.5),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_control_action_cooldown_s),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("ReactionForceLaneChangeEnable",
           "Force lane change requests by temporarily disabling autonomous lane-change logic",
           BooleanValue (false),
           MakeBooleanAccessor (&emergencyVehicleAlert::m_reaction_force_lane_change_enable),
           MakeBooleanChecker ())
        .AddAttribute ("CrashModeEnable",
           "Enable crash-test mode: after repeated drop_decision_no_action, force unsafe speed control",
           BooleanValue (false),
           MakeBooleanAccessor (&emergencyVehicleAlert::m_crash_mode_enable),
           MakeBooleanChecker ())
        .AddAttribute ("CrashModeVehicleId",
           "Optional vehicle id filter for crash-test mode (empty = all non-emergency vehicles)",
           StringValue (std::string ()),
           MakeStringAccessor (&emergencyVehicleAlert::m_crash_mode_vehicle_id),
           MakeStringChecker ())
        .AddAttribute ("CrashModeNoActionThreshold",
           "How many consecutive drop_decision_no_action events trigger crash-test mode",
           UintegerValue (5),
           MakeUintegerAccessor (&emergencyVehicleAlert::m_crash_mode_no_action_threshold),
           MakeUintegerChecker<uint32_t> (1))
        .AddAttribute ("CrashModeForceSpeedMps",
           "Forced speed [m/s] while crash-test mode is active",
           DoubleValue (32.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_crash_mode_force_speed_mps),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("CrashModeDurationS",
           "How long [s] crash-test mode keeps forced speed and disabled speed safety",
           DoubleValue (4.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_crash_mode_duration_s),
           MakeDoubleChecker<double> (0.1))
        .AddAttribute ("CrashModeMinTimeS",
           "Do not activate crash-test mode before this simulation time [s]",
           DoubleValue (0.0),
           MakeDoubleAccessor (&emergencyVehicleAlert::m_crash_mode_min_time_s),
           MakeDoubleChecker<double> (0.0))
        .AddAttribute ("V2xAwarenessJunctionEnable",
           "When enabled, non-emergency vehicles start without junction right-of-way "
           "awareness (SUMO speedMode bit 3 = off). The first received CAM from an "
           "emergency vehicle grants junction awareness (bit 3 = on). No CAM received "
           "means the vehicle never yields — outcome depends entirely on channel quality.",
           BooleanValue (false),
           MakeBooleanAccessor (&emergencyVehicleAlert::m_v2x_awareness_junction_enable),
           MakeBooleanChecker ());
        return tid;
  }

  emergencyVehicleAlert::emergencyVehicleAlert ()
  {
    NS_LOG_FUNCTION(this);
    m_client = nullptr;
    m_print_summary = true;
    m_already_print = false;
    m_send_cam = true;
    m_send_cpm = true;

    m_denm_sent = 0;
    m_cam_received = 0;
    m_cpm_received = 0;
    m_cam_dropped_app = 0;
    m_cpm_dropped_app = 0;
    m_cam_dropped_phy = 0;
    m_cpm_dropped_phy = 0;
    m_denm_received = 0;
    m_control_actions = 0;
    m_denm_intertime = 0;
    m_rx_drop_prob_cam = 0.0;
    m_rx_drop_prob_cpm = 0.0;
    m_rx_drop_prob_phy_cam = 0.0;
    m_rx_drop_prob_phy_cpm = 0.0;
    m_drop_triggered_reaction_enable = false;
    m_target_loss_profile_enable = false;
    m_target_loss_vehicle_id = "veh9";
    m_target_loss_rx_drop_prob_cam = 1.0;
    m_target_loss_rx_drop_prob_cpm = 0.0;
    m_target_loss_rx_drop_prob_phy_cam = 1.0;
    m_target_loss_rx_drop_prob_phy_cpm = 0.0;
    m_target_loss_profile_applied = false;
    m_per_vehicle_prr_profile.clear ();
    m_per_vehicle_prr_profile_applied = false;
    m_profile_equiv_tx_power_dbm = std::numeric_limits<double>::quiet_NaN ();
    m_profile_target_prr = std::numeric_limits<double>::quiet_NaN ();
    m_cam_silence_drop_inference_enable = false;
    m_cam_silence_focus_tx_id = 2;
    m_cam_silence_expected_period_ms = 600.0;
    m_cam_silence_timeout_s = 0.95;
    m_cam_silence_bootstrap_time_s = 2.2;
    m_cam_silence_seen_any = false;
    m_cam_silence_last_msg_seq = -1;
    m_cam_silence_next_msg_seq = -1;
    m_cam_silence_last_emitted_seq = -1;
    m_cam_silence_last_rx_time_s = -1.0;
    m_cam_silence_last_inferred_time_s = -1.0;
    m_drop_rv = CreateObject<UniformRandomVariable> ();
    m_drop_rv->SetAttribute ("Min", DoubleValue (0.0));
    m_drop_rv->SetAttribute ("Max", DoubleValue (1.0));

    m_distance_threshold = 75; // Distance used in GeoNet to determine the radius of the circumference arounf the emergency vehicle where the DENMs are valid
    m_heading_threshold = 45; // Max heading angle difference between the normal vehicles and the emergenecy vehicle, that triggers a reaction in the normal vehicles
    m_reaction_target_lane = 0;
    m_reaction_speed_factor_target_lane = 0.5;
    m_reaction_speed_factor_other_lane = 1.5;
    m_reaction_action_duration_s = 3.0;
    m_cpm_distance_threshold = 60.0;
    m_cpm_ttc_threshold_s = 3.0;
    m_sensor_reaction_enable = false;
    m_sensor_reaction_distance_threshold_m = 20.0;
    m_sensor_reaction_ttc_threshold_s = 1.5;
    m_sensor_reaction_period_ms = 100.0;
    m_sensor_range_m = 50.0;
    m_sensor_reaction_focus_vehicle_id.clear ();
    m_sensor_reaction_focus_station_id = -1;
    m_control_action_cooldown_s = 0.5;
    m_reaction_force_lane_change_enable = false;
    m_last_control_action_s = -1e9;
    m_crash_mode_enable = false;
    m_crash_mode_vehicle_id.clear ();
    m_crash_mode_no_action_threshold = 5;
    m_crash_mode_force_speed_mps = 32.0;
    m_crash_mode_duration_s = 4.0;
    m_crash_mode_min_time_s = 0.0;
    m_drop_no_action_streak = 0;
    m_crash_mode_active = false;
    m_v2x_awareness_junction_enable = false;
    m_v2x_junction_awareness_granted = false;
  }

  emergencyVehicleAlert::~emergencyVehicleAlert ()
  {
    NS_LOG_FUNCTION(this);
  }

  void
  emergencyVehicleAlert::DoDispose (void)
  {
    NS_LOG_FUNCTION(this);
    Application::DoDispose ();
  }

  bool
  emergencyVehicleAlert::TryGetPerVehiclePrrProfile (const std::string& vehicleId,
                                                     PerVehiclePrrProfile& outProfile) const
  {
    outProfile = PerVehiclePrrProfile ();
    if (vehicleId.empty () || m_per_vehicle_prr_profile.empty ())
      {
        return false;
      }

    std::string normalizedProfiles = m_per_vehicle_prr_profile;
    std::replace (normalizedProfiles.begin (), normalizedProfiles.end (), ';', ',');
    const std::vector<std::string> entries = SplitByDelimiter (normalizedProfiles, ',');
    for (const std::string& rawEntry : entries)
      {
        const std::string entry = TrimCopy (rawEntry);
        if (entry.empty ())
          {
            continue;
          }

        const std::vector<std::string> fields = SplitByDelimiter (entry, ':');
        if (fields.size () < 2)
          {
            NS_LOG_WARN ("Ignoring malformed PerVehiclePrrProfile entry '" << entry << "'");
            continue;
          }

        const std::string entryVehicleId = TrimCopy (fields[0]);
        if (entryVehicleId != vehicleId)
          {
            continue;
          }

        double dropProbCam = 0.0;
        if (!TryParseFiniteDouble (TrimCopy (fields[1]), dropProbCam))
          {
            NS_LOG_WARN ("Invalid CAM PHY drop probability in PerVehiclePrrProfile entry '" << entry << "'");
            return false;
          }
        outProfile.rx_drop_prob_phy_cam = std::min (1.0, std::max (0.0, dropProbCam));

        if (fields.size () >= 3)
          {
            double equivDbm = 0.0;
            if (TryParseFiniteDouble (TrimCopy (fields[2]), equivDbm))
              {
                outProfile.equiv_tx_power_dbm = equivDbm;
              }
          }

        if (fields.size () >= 4)
          {
            double targetPrr = 0.0;
            if (TryParseFiniteDouble (TrimCopy (fields[3]), targetPrr))
              {
                outProfile.target_prr = std::min (1.0, std::max (0.0, targetPrr));
              }
          }

        if (fields.size () >= 5)
          {
            double dropProbCpm = 0.0;
            if (TryParseFiniteDouble (TrimCopy (fields[4]), dropProbCpm))
              {
                outProfile.rx_drop_prob_phy_cpm = std::min (1.0, std::max (0.0, dropProbCpm));
              }
          }
        return true;
      }

    return false;
  }

  void
  emergencyVehicleAlert::StartApplication (void)
  {
    NS_LOG_FUNCTION(this);

    /*
     * In this example, the vehicle can be either of type "passenger" or of type "emergency" (see cars.rou.xml in SUMO folder inside examples/sumo_files_v2v_map)
     * All the vehicles broadcast CAM messages. When a "passenger" car receives a CAM from an "emergency" vehicle, it checks the distance between them and
     * the difference in heading, and if it considers it to be close, it takes proper actions to facilitate the takeover maneuver.
     */

    /* Save the vehicles informations */
    m_id = m_client->GetVehicleId (this->GetNode ());
    m_type = m_client->TraCIAPI::vehicle.getVehicleClass (m_id);
    m_max_speed = m_client->TraCIAPI::vehicle.getMaxSpeed (m_id);
    if (std::find (s_active_instances.begin (), s_active_instances.end (), this) == s_active_instances.end ())
      {
        s_active_instances.push_back (this);
      }
    if (m_drop_rv != nullptr && m_id.size () > 3)
      {
        m_drop_rv->SetStream (std::stol (m_id.substr (3)));
      }

    m_target_loss_profile_applied = false;
    m_per_vehicle_prr_profile_applied = false;
    m_profile_equiv_tx_power_dbm = std::numeric_limits<double>::quiet_NaN ();
    m_profile_target_prr = std::numeric_limits<double>::quiet_NaN ();
    if (m_target_loss_profile_enable &&
        !m_target_loss_vehicle_id.empty () &&
        m_id == m_target_loss_vehicle_id)
      {
        m_rx_drop_prob_cam = m_target_loss_rx_drop_prob_cam;
        m_rx_drop_prob_cpm = m_target_loss_rx_drop_prob_cpm;
        m_rx_drop_prob_phy_cam = m_target_loss_rx_drop_prob_phy_cam;
        m_rx_drop_prob_phy_cpm = m_target_loss_rx_drop_prob_phy_cpm;
        m_target_loss_profile_applied = true;
        std::cout << "TARGET-LOSS-PROFILE,id=" << m_id
                  << ",rx_drop_prob_cam=" << m_rx_drop_prob_cam
                  << ",rx_drop_prob_cpm=" << m_rx_drop_prob_cpm
                  << ",rx_drop_prob_phy_cam=" << m_rx_drop_prob_phy_cam
                  << ",rx_drop_prob_phy_cpm=" << m_rx_drop_prob_phy_cpm
                  << std::endl;
      }

    PerVehiclePrrProfile profileOverride;
    if (TryGetPerVehiclePrrProfile (m_id, profileOverride))
      {
        m_rx_drop_prob_phy_cam = profileOverride.rx_drop_prob_phy_cam;
        if (std::isfinite (profileOverride.rx_drop_prob_phy_cpm))
          {
            m_rx_drop_prob_phy_cpm = profileOverride.rx_drop_prob_phy_cpm;
          }
        m_profile_equiv_tx_power_dbm = profileOverride.equiv_tx_power_dbm;
        m_profile_target_prr = profileOverride.target_prr;
        m_per_vehicle_prr_profile_applied = true;
        std::cout << "PER-VEHICLE-PRR-PROFILE,id=" << m_id
                  << ",rx_drop_prob_phy_cam=" << m_rx_drop_prob_phy_cam
                  << ",rx_drop_prob_phy_cpm=" << m_rx_drop_prob_phy_cpm;
        if (std::isfinite (m_profile_equiv_tx_power_dbm))
          {
            std::cout << ",equiv_tx_power_dbm=" << m_profile_equiv_tx_power_dbm;
          }
        if (std::isfinite (m_profile_target_prr))
          {
            std::cout << ",target_prr=" << m_profile_target_prr;
          }
        std::cout << std::endl;
      }

    m_cam_silence_seen_any = false;
    m_cam_silence_last_msg_seq = -1;
    m_cam_silence_next_msg_seq = -1;
    m_cam_silence_last_emitted_seq = -1;
    m_cam_silence_last_rx_time_s = -1.0;
    m_cam_silence_last_inferred_time_s = -1.0;
    m_pending_cam_tx_time_s.clear ();
    Simulator::Cancel (m_cam_silence_watchdog_ev);
    if (m_cam_silence_drop_inference_enable && m_type != "emergency")
      {
        const double periodS = std::max (0.01, m_cam_silence_expected_period_ms / 1000.0);
        m_cam_silence_watchdog_ev =
          Simulator::Schedule (Seconds (periodS), &emergencyVehicleAlert::CamSilenceWatchdogTick, this);
      }

    VDP* traci_vdp = new VDPTraCI(m_client,m_id);

    //Create LDM and sensor object
    m_LDM = CreateObject<LDM>();
    m_LDM->setStationID(m_id);
    m_LDM->setTraCIclient(m_client);
    m_LDM->setVDP(traci_vdp);

    m_sensor = CreateObject<SUMOSensor>();
    m_sensor->setStationID(m_id);
    m_sensor->setTraCIclient(m_client);
    m_sensor->setVDP(traci_vdp);
    m_sensor->setLDM (m_LDM);
    m_sensor->setSensorRange (std::max (1.0, m_sensor_range_m));

    m_sensor_reaction_focus_station_id = -1;
    if (!m_sensor_reaction_focus_vehicle_id.empty ())
      {
        int64_t parsedStationId = -1;
        if (TryParseStationIdToken (m_sensor_reaction_focus_vehicle_id, parsedStationId))
          {
            m_sensor_reaction_focus_station_id = parsedStationId;
          }
        else
          {
            NS_LOG_WARN ("Invalid SensorReactionFocusVehicleId '" << m_sensor_reaction_focus_vehicle_id
                                                                  << "' for " << m_id
                                                                  << ", ignoring local focus filter.");
          }
      }
    Simulator::Cancel (m_sensor_watchdog_ev);
    if (m_sensor_reaction_enable && m_type != "emergency")
      {
        const double periodS = std::max (0.01, m_sensor_reaction_period_ms / 1000.0);
        m_sensor_watchdog_ev =
          Simulator::Schedule (Seconds (periodS), &emergencyVehicleAlert::SensorWatchdogTick, this);
      }

    // Create new BTP and GeoNet objects and set them in DENBasicService and CABasicService
    m_btp = CreateObject <btp>();
    m_geoNet = CreateObject <GeoNet>();
    m_geoNet->SetAttribute ("RxDropProbPhyCam", DoubleValue (m_rx_drop_prob_phy_cam));
    m_geoNet->SetAttribute ("RxDropProbPhyCpm", DoubleValue (m_rx_drop_prob_phy_cpm));
    m_geoNet->setRxPhyDropCallback (
      std::bind (&emergencyVehicleAlert::LogPhyDropEvent, this, std::placeholders::_1));

    if(m_metric_supervisor!=nullptr)
    {
      m_geoNet->setMetricSupervisor(m_metric_supervisor);
    }

    m_btp->setGeoNet(m_geoNet);
    m_denService.setBTP(m_btp);
    m_caService.setBTP(m_btp);
    m_cpService.setBTP(m_btp);
    m_caService.setLDM(m_LDM);
    m_cpService.setLDM(m_LDM);

    /* Create the Sockets for TX and RX */
    TypeId tid;
    if(m_model=="80211p")
      tid = TypeId::LookupByName ("ns3::PacketSocketFactory");
    else if(m_model=="cv2x" || m_model=="nrv2x")
      tid = TypeId::LookupByName ("ns3::UdpSocketFactory");
    else
      NS_FATAL_ERROR ("No communication model set - check simulation script - valid models: '80211p' or 'lte'");
    m_socket = Socket::CreateSocket (GetNode (), tid);

    if(m_model=="80211p")
    {
        /* Bind the socket to local address */
        PacketSocketAddress local = getGNAddress(GetNode ()->GetDevice (0)->GetIfIndex (),
                                                GetNode ()->GetDevice (0)->GetAddress () );
        if (m_socket->Bind (local) == -1)
        {
          NS_FATAL_ERROR ("Failed to bind client socket for BTP + GeoNetworking (802.11p)");
        }
        // Set the socketAddress for broadcast
        PacketSocketAddress remote = getGNAddress(GetNode ()->GetDevice (0)->GetIfIndex (),
                                                GetNode ()->GetDevice (0)->GetBroadcast () );
        m_socket->Connect (remote);
    }
    else // m_model=="cv2x"
    {
        /* The C-V2X model requires the socket to be bind to "any" IPv4 address, and to be connected to the
         * IP address of the transmitting node. Then, the model will take care of broadcasting the packets.
        */
        if (m_socket->Bind (InetSocketAddress (Ipv4Address::GetAny (), 19)) == -1)
        {
          NS_FATAL_ERROR ("Failed to bind client socket for C-V2X");
        }
        m_socket->Connect (InetSocketAddress(m_ipAddress,19));
    }

    /* Set Station Type in DENBasicService */
    StationType_t stationtype;
    if (m_type=="passenger")
      stationtype = StationType_passengerCar;
    else if (m_type=="emergency"){
      stationtype = StationType_specialVehicle;
      m_LDM->enablePolygons (); // Uncomment to enable detected object polygon visualization for this specific vehicle
      }
    else
      stationtype = StationType_unknown;

    libsumo::TraCIColor connected;
    connected.r=0;connected.g=225;connected.b=255;connected.a=255;
    if (m_per_vehicle_prr_profile_applied)
      {
        if (std::isfinite (m_profile_target_prr))
          {
            if (m_profile_target_prr < 0.2)
              {
                connected.r = 255;
                connected.g = 0;
                connected.b = 255;
              }
            else if (m_profile_target_prr < 0.8)
              {
                connected.r = 255;
                connected.g = 165;
                connected.b = 0;
              }
            else
              {
                connected.r = 0;
                connected.g = 225;
                connected.b = 255;
              }
          }
        else
          {
            connected.r = 0;
            connected.g = 225;
            connected.b = 255;
          }
      }
    else if (m_target_loss_profile_applied)
      {
        connected.r = 255;
        connected.g = 0;
        connected.b = 255;
      }
    m_client->TraCIAPI::vehicle.setColor (m_id, connected);

    /* V2X-awareness junction control:
     * Non-emergency vehicles start without junction right-of-way awareness
     * (speedMode bit 3 off).  The vehicle does not know about approaching
     * priority traffic until it receives a CAM.  If the channel is good,
     * the first CAM arrives quickly and awareness is granted.  If the
     * channel is degraded, no CAM arrives, and the vehicle enters the
     * intersection without yielding — the outcome emerges from channel
     * quality, not from a hardcoded threshold. */
    m_v2x_junction_awareness_granted = false;
    if (m_v2x_awareness_junction_enable && m_type != "emergency")
      {
        // 23 = default(31) minus bit 3(8): all safety except junction right-of-way
        m_client->TraCIAPI::vehicle.setSpeedMode (m_id, 23);
        LogControlEvent ("v2x_junction_awareness_pending",
                         -1, -1, static_cast<uint64_t> (-1),
                         -1.0, -1.0, -1, -1, -1.0);
      }

    /* Set sockets, callback and station properties in DENBasicService */
    m_denService.setSocketTx (m_socket);
    m_denService.setSocketRx (m_socket);
    m_denService.setStationProperties (std::stol(m_id.substr (3)), (long)stationtype);
    m_denService.addDENRxCallback (std::bind(&emergencyVehicleAlert::receiveDENM,this,std::placeholders::_1,std::placeholders::_2));
    m_denService.setRealTime (m_real_time);

    /* Set sockets, callback, station properties and TraCI VDP in CABasicService */
    m_caService.setSocketTx (m_socket);
    m_caService.setSocketRx (m_socket);
    m_caService.setStationProperties (std::stol(m_id.substr (3)), (long)stationtype);
    m_caService.addCARxCallbackExtended (std::bind(&emergencyVehicleAlert::receiveCAM,this,std::placeholders::_1,std::placeholders::_2,std::placeholders::_3,std::placeholders::_4,std::placeholders::_5));
    m_caService.addCATxCallback (std::bind(&emergencyVehicleAlert::logCamTx,this,std::placeholders::_1));
    m_caService.setRealTime (m_real_time);

    /* Set sockets, callback, station properties and TraCI VDP in CPBasicService */
    m_cpService.setSocketTx (m_socket);
    m_cpService.setSocketRx (m_socket);
    m_cpService.setStationProperties (std::stol(m_id.substr (3)), (long)stationtype);
    m_cpService.addCPRxCallback (std::bind(&emergencyVehicleAlert::receiveCPM,this,std::placeholders::_1,std::placeholders::_2));
    m_cpService.setRealTime (m_real_time);
    m_cpService.setTraCIclient (m_client);

    /* IF CPMv1 facility is needed
    m_cpService_v1.setBTP (m_btp);
    m_cpService_v1.setLDM(m_LDM);
    m_cpService_v1.setSocketTx (m_socket);
    m_cpService_v1.setSocketRx (m_socket);
    m_cpService_v1.setVDP(traci_vdp);
    m_cpService_v1.setTraCIclient(m_client);
    m_cpService_v1.setRealTime(m_real_time);
    m_cpService_v1.setStationProperties(std::stol(m_id.substr (3)), (long)stationtype);
    m_cpService_v1.addCPRxCallback(std::bind(&emergencyVehicleAlert::receiveCPMV1,this,std::placeholders::_1,std::placeholders::_2));
    m_cpService_v1.startCpmDissemination ();
    */

    /* Set TraCI VDP for GeoNet object */
    m_caService.setVDP(traci_vdp);
    m_denService.setVDP(traci_vdp);
    m_cpService.setVDP(traci_vdp);

    /* Schedule CAM dissemination */
    if(m_send_cam == true)
    {
      // Old desync code kept just for reference
      // It may lead to nodes not being desynchronized properly in specific situations in which
      // Simulator::Now().GetNanoSeconds () returns the same seed for multiple nodes
      // std::srand(Simulator::Now().GetNanoSeconds ());
      // double desync = ((double)std::rand()/RAND_MAX);

      Ptr<UniformRandomVariable> desync_rvar = CreateObject<UniformRandomVariable> ();
      desync_rvar->SetAttribute ("Min", DoubleValue (0.0));
      desync_rvar->SetAttribute ("Max", DoubleValue (1.0));
      double desync = desync_rvar->GetValue ();

      m_caService.startCamDissemination(desync);
    }

    /* Schedule CPM dissemination */
    if(m_send_cpm == true)
    {
      m_cpService.startCpmDissemination ();
    }

    if (!m_csv_name.empty ())
    {
      m_csv_ofstream_cam.open (m_csv_name+"-"+m_id+"-CAM.csv",std::ofstream::trunc);
      m_csv_ofstream_cam << "messageId,camId,timestamp,latitude,longitude,heading,speed,acceleration" << std::endl;
      m_csv_ofstream_msg.open (m_csv_name+"-"+m_id+"-MSG.csv",std::ofstream::trunc);
      m_csv_ofstream_msg << "vehicle_id,msg_seq,tx_t_s,rx_t_s,rx_ok,msg_type,tx_id,rx_id,cam_gdt_ms,pkt_uid" << std::endl;
      m_csv_ofstream_ctrl.open (m_csv_name+"-"+m_id+"-CTRL.csv",std::ofstream::trunc);
      m_csv_ofstream_ctrl << "time_s,vehicle_id,event_type,source_id,msg_seq,pkt_uid,distance_m,heading_diff_deg,lane_before,lane_after,target_speed_mps" << std::endl;
      m_csv_ofstream_phy.open (m_csv_name+"-"+m_id+"-PHY.csv",std::ofstream::trunc);
      m_csv_ofstream_phy << "time_s,vehicle_id,msg_type,tx_id,sinr_dB,snr_dB,rssi_dBm,rsrp_dBm,pkt_size,distance_m,rx_ok" << std::endl;
      m_csv_ofstream_profile.open (m_csv_name+"-"+m_id+"-PROFILE.csv",std::ofstream::trunc);
      m_csv_ofstream_profile
        << "vehicle_id,profile_source,rx_drop_prob_phy_cam,rx_drop_prob_phy_cpm,equiv_tx_power_dbm,target_prr,expected_prr_if_only_profile"
        << std::endl;
      std::string profileSource = "global_defaults";
      if (m_per_vehicle_prr_profile_applied)
        {
          profileSource = "per_vehicle_prr_profile";
        }
      else if (m_target_loss_profile_applied)
        {
          profileSource = "target_loss_profile";
        }
      m_csv_ofstream_profile << m_id << "," << profileSource << ","
                             << m_rx_drop_prob_phy_cam << "," << m_rx_drop_prob_phy_cpm << ",";
      if (std::isfinite (m_profile_equiv_tx_power_dbm))
        {
          m_csv_ofstream_profile << m_profile_equiv_tx_power_dbm;
        }
      m_csv_ofstream_profile << ",";
      if (std::isfinite (m_profile_target_prr))
        {
          m_csv_ofstream_profile << m_profile_target_prr;
        }
      m_csv_ofstream_profile << "," << (1.0 - m_rx_drop_prob_phy_cam) << std::endl;
    }
  }

  void
  emergencyVehicleAlert::StopApplication ()
  {
    NS_LOG_FUNCTION(this);
    auto it = std::find (s_active_instances.begin (), s_active_instances.end (), this);
    if (it != s_active_instances.end ())
      {
        s_active_instances.erase (it);
      }
    Simulator::Cancel(m_speed_ev);
    Simulator::Cancel(m_cam_silence_watchdog_ev);
    Simulator::Cancel(m_sensor_watchdog_ev);
    Simulator::Cancel(m_crash_mode_restore_ev);
    Simulator::Cancel(m_send_cam_ev);
    Simulator::Cancel(m_update_denm_ev);

    uint64_t cam_sent, cpm_sent;

    if (!m_csv_name.empty ())
    {
      m_csv_ofstream_cam.close ();
      if (m_csv_ofstream_msg.is_open ())
        {
          m_csv_ofstream_msg.close ();
        }
      if (m_csv_ofstream_ctrl.is_open ())
        {
          m_csv_ofstream_ctrl.close ();
        }
      if (m_csv_ofstream_profile.is_open ())
        {
          m_csv_ofstream_profile.close ();
        }
      if (m_csv_ofstream_phy.is_open ())
        {
          m_csv_ofstream_phy.close ();
        }
    }

    cam_sent = m_caService.terminateDissemination ();
    cpm_sent = m_cpService.terminateDissemination ();
    m_denService.cleanup();
    m_LDM->cleanup();
    m_sensor->cleanup();

    if (m_print_summary && !m_already_print)
    {
      m_cam_dropped_phy = static_cast<int> (m_geoNet->GetCamDroppedPhy ());
      m_cpm_dropped_phy = static_cast<int> (m_geoNet->GetCpmDroppedPhy ());
      std::cout << "INFO-" << m_id
                << ",CAM-SENT:" << cam_sent
                << ",CAM-RECEIVED:" << m_cam_received
                << ",CAM-DROPPED-APP:" << m_cam_dropped_app
                << ",CAM-DROPPED-PHY:" << m_cam_dropped_phy
                << ",CPM-SENT: " << cpm_sent
                << ",CPM-RECEIVED: " << m_cpm_received
                << ",CPM-DROPPED-APP:" << m_cpm_dropped_app
                << ",CPM-DROPPED-PHY:" << m_cpm_dropped_phy
                << ",CONTROL-ACTIONS:" << m_control_actions
                << std::endl;
      m_already_print=true;
    }
  }

  void
  emergencyVehicleAlert::StopApplicationNow ()
  {
    NS_LOG_FUNCTION(this);
    StopApplication ();
  }

  void
  emergencyVehicleAlert::PublishCamTxEvent (long txId, long msgSeq, double txTimeS)
  {
    for (auto* app : s_active_instances)
      {
        if (app != nullptr)
          {
            app->OnGlobalCamTxEvent (txId, msgSeq, txTimeS);
          }
      }
  }

  void
  emergencyVehicleAlert::OnGlobalCamTxEvent (long txId, long msgSeq, double txTimeS)
  {
    if (!m_cam_silence_drop_inference_enable || (!m_crash_mode_enable && !m_v2x_awareness_junction_enable) || m_type == "emergency")
      {
        return;
      }
    if (txTimeS < std::max (0.0, m_cam_silence_bootstrap_time_s))
      {
        return;
      }
    if (m_cam_silence_focus_tx_id >= 0 && txId != m_cam_silence_focus_tx_id)
      {
        return;
      }
    if (m_id.size () > 3)
      {
        const long selfStationId = std::stol (m_id.substr (3));
        if (selfStationId == txId)
          {
            return;
          }
      }
    m_pending_cam_tx_time_s[{txId, msgSeq}] = txTimeS;
  }

  void
  emergencyVehicleAlert::logCamTx (asn1cpp::Seq<CAM> cam)
  {
    long cam_gdt_ms = asn1cpp::getField(cam->cam.generationDeltaTime,long);
    long tx_id = asn1cpp::getField(cam->header.stationId,long);
    double now_s = Simulator::Now ().GetSeconds ();
    if (!m_csv_name.empty () && m_csv_ofstream_msg.is_open ())
      {
        m_csv_ofstream_msg << m_id << "," << cam_gdt_ms << "," << now_s
                           << ",," << 0 << ",CAM," << tx_id << ",," << cam_gdt_ms << "," << -1 << std::endl;
      }
    PublishCamTxEvent (tx_id, cam_gdt_ms, now_s);
  }

  void
  emergencyVehicleAlert::LogControlEvent (const std::string& eventType,
                                          long txId,
                                          long msgSeq,
                                          uint64_t packetUid,
                                          double distanceMeters,
                                          double headingDiffDeg,
                                          int laneBefore,
                                          int laneAfter,
                                          double speedTarget)
  {
    if (m_csv_name.empty () || !m_csv_ofstream_ctrl.is_open ())
      {
        return;
      }
    m_csv_ofstream_ctrl << Simulator::Now ().GetSeconds ()
                        << "," << m_id
                        << "," << eventType
                        << "," << txId
                        << "," << msgSeq
                        << "," << packetUid
                        << "," << distanceMeters
                        << "," << headingDiffDeg
                        << "," << laneBefore
                        << "," << laneAfter
                        << "," << speedTarget
                        << std::endl;
  }

  void
  emergencyVehicleAlert::LogPhyDropEvent (const GeoNet::RxPhyDropInfo& dropInfo)
  {
    if (dropInfo.btpDestPort == CAM_PORT)
      {
        ++m_cam_dropped_phy;
      }
    else if (dropInfo.btpDestPort == CPM_PORT)
      {
        ++m_cpm_dropped_phy;
      }

    long rx_id = 0;
    if (m_id.size () > 3)
      {
        rx_id = std::stol (m_id.substr (3));
      }

    std::string dropType = "OTHER_DROP_PHY";
    if (dropInfo.btpDestPort == CAM_PORT)
      {
        dropType = "CAM_DROP_PHY";
      }
    else if (dropInfo.btpDestPort == CPM_PORT)
      {
        dropType = "CPM_DROP_PHY";
      }

    if (!m_csv_name.empty () && m_csv_ofstream_msg.is_open ())
      {
        m_csv_ofstream_msg << m_id << "," << dropInfo.msgSeq << ",,"
                           << Simulator::Now ().GetSeconds ()
                           << "," << 0 << "," << dropType << ","
                           << dropInfo.txStationId << "," << rx_id << "," << dropInfo.msgSeq
                           << "," << dropInfo.packetUid << std::endl;
      }

    bool applyDropReaction = m_drop_triggered_reaction_enable &&
                             (m_type != "emergency") &&
                             (dropInfo.btpDestPort == CAM_PORT || dropInfo.btpDestPort == CPM_PORT);
    if (applyDropReaction)
      {
        const std::string reactionType =
          (dropInfo.btpDestPort == CAM_PORT) ? "cam_drop_reaction" : "cpm_drop_reaction";
        bool applied = ApplyEvasiveControl (reactionType,
                                            dropInfo.txStationId,
                                            dropInfo.msgSeq,
                                            dropInfo.packetUid,
                                            -1.0,
                                            -1.0);
        if (!applied)
          {
            LogControlEvent ("drop_decision_no_action",
                             dropInfo.txStationId,
                             dropInfo.msgSeq,
                             dropInfo.packetUid,
                             -1.0,
                             -1.0,
                             -1,
                             -1,
                             -1.0);
            MaybeTriggerCrashModeOnNoActionDrop (dropInfo.txStationId, dropInfo.msgSeq, dropInfo.packetUid);
          }
      }
    else
      {
        LogControlEvent ("drop_decision_no_action",
                         dropInfo.txStationId,
                         dropInfo.msgSeq,
                         dropInfo.packetUid,
                         -1.0,
                         -1.0,
                         -1,
                         -1,
                         -1.0);
        MaybeTriggerCrashModeOnNoActionDrop (dropInfo.txStationId, dropInfo.msgSeq, dropInfo.packetUid);
      }
  }

  void
  emergencyVehicleAlert::MaybeTriggerCrashModeOnNoActionDrop (long txId, long msgSeq, uint64_t packetUid)
  {
    if (!m_crash_mode_enable || m_type == "emergency")
      {
        return;
      }
    if (Simulator::Now ().GetSeconds () < std::max (0.0, m_crash_mode_min_time_s))
      {
        return;
      }
    if (!m_crash_mode_vehicle_id.empty () && m_id != m_crash_mode_vehicle_id)
      {
        return;
      }

    ++m_drop_no_action_streak;
    if (m_crash_mode_active)
      {
        return;
      }
    if (m_drop_no_action_streak < std::max (1u, m_crash_mode_no_action_threshold))
      {
        return;
      }

    const double forcedSpeed = std::max (0.0, m_crash_mode_force_speed_mps);
    const double activeDuration = std::max (0.1, m_crash_mode_duration_s);
    try
      {
        // 0 disables speed safety checks for TraCI speed commands.
        m_client->TraCIAPI::vehicle.setSpeedMode (m_id, 0);
        // 0 disables autonomous lane changes; needed for deterministic collision tests.
        m_client->TraCIAPI::vehicle.setLaneChangeMode (m_id, 0);
        m_client->TraCIAPI::vehicle.setSpeed (m_id, forcedSpeed);
      }
    catch (const std::exception &e)
      {
        NS_LOG_WARN ("Crash mode activation failed for '" << m_id << "': " << e.what ());
        return;
      }

    libsumo::TraCIColor crashColor;
    crashColor.r = 255;
    crashColor.g = 0;
    crashColor.b = 0;
    crashColor.a = 255;
    m_client->TraCIAPI::vehicle.setColor (m_id, crashColor);
    m_crash_mode_active = true;
    LogControlEvent ("crash_mode_forced_speed",
                     txId,
                     msgSeq,
                     packetUid,
                     -1.0,
                     -1.0,
                     -1,
                     -1,
                     forcedSpeed);

    Simulator::Remove (m_crash_mode_restore_ev);
    m_crash_mode_restore_ev = Simulator::Schedule (
      Seconds (activeDuration),
      [this] ()
      {
        try
          {
            // 31 restores default SUMO speed checks.
            m_client->TraCIAPI::vehicle.setSpeedMode (m_id, 31);
            // 1621 is SUMO default lane change mode.
            m_client->TraCIAPI::vehicle.setLaneChangeMode (m_id, 1621);
            m_client->TraCIAPI::vehicle.setSpeed (m_id, -1.0);
          }
        catch (const std::exception &e)
          {
            NS_LOG_WARN ("Crash mode restore failed for '" << m_id << "': " << e.what ());
          }
        m_crash_mode_active = false;
        m_drop_no_action_streak = 0;
        LogControlEvent ("crash_mode_restore",
                         -1,
                         -1,
                         static_cast<uint64_t> (-1),
                         -1.0,
                         -1.0,
                         -1,
                         -1,
                         -1.0);
      });
  }

  void
  emergencyVehicleAlert::GrantJunctionAwareness ()
  {
    if (!m_v2x_awareness_junction_enable || m_v2x_junction_awareness_granted)
      {
        return;
      }
    m_v2x_junction_awareness_granted = true;

    // First CAM from emergency vehicle received — the vehicle now knows about
    // the approaching priority traffic.  Restore full SUMO speed mode (bit 3 on).
    try
      {
        m_client->TraCIAPI::vehicle.setSpeedMode (m_id, 31);
      }
    catch (const std::exception& e)
      {
        NS_LOG_WARN ("GrantJunctionAwareness speedMode restore failed for '"
                     << m_id << "': " << e.what ());
      }
    LogControlEvent ("v2x_junction_awareness_granted",
                     -1, -1, static_cast<uint64_t> (-1),
                     -1.0, -1.0, -1, -1, -1.0);
  }

  bool
  emergencyVehicleAlert::ApplyEvasiveControl (const std::string& eventType,
                                              long txId,
                                              long msgSeq,
                                              uint64_t packetUid,
                                              double distanceMeters,
                                              double headingDiffDeg)
  {
    double nowS = Simulator::Now ().GetSeconds ();
    if ((nowS - m_last_control_action_s) < std::max (0.0, m_control_action_cooldown_s))
      {
        return false;
      }

    int laneBefore = m_client->TraCIAPI::vehicle.getLaneIndex (m_id);
    int laneAfter = std::max (0, m_reaction_target_lane);
    double targetSpeedFactor =
      (laneBefore == laneAfter) ? m_reaction_speed_factor_target_lane : m_reaction_speed_factor_other_lane;
    double targetSpeed = m_max_speed * std::max (0.0, targetSpeedFactor);
    double actionDuration = std::max (0.1, m_reaction_action_duration_s);

    if (m_reaction_force_lane_change_enable)
      {
        // 0 disables autonomous lane-change logic, making command-driven lane changes deterministic.
        m_client->TraCIAPI::vehicle.setLaneChangeMode (m_id, 0);
      }
    m_client->TraCIAPI::vehicle.changeLane (m_id, laneAfter, actionDuration);
    m_client->TraCIAPI::vehicle.setMaxSpeed (m_id, targetSpeed);

    libsumo::TraCIColor adapted;
    if (laneBefore == laneAfter)
      {
        adapted.r=232;adapted.g=126;adapted.b=4;adapted.a=255;
      }
    else
      {
        adapted.r=0;adapted.g=128;adapted.b=80;adapted.a=255;
      }
    m_client->TraCIAPI::vehicle.setColor (m_id,adapted);
    m_control_actions++;
    m_last_control_action_s = nowS;
    m_drop_no_action_streak = 0;
    LogControlEvent (eventType,
                     txId,
                     msgSeq,
                     packetUid,
                     distanceMeters,
                     headingDiffDeg,
                     laneBefore,
                     laneAfter,
                     targetSpeed);

    Simulator::Remove(m_speed_ev);
    m_speed_ev = Simulator::Schedule (Seconds (actionDuration), &emergencyVehicleAlert::SetMaxSpeed, this);
    return true;
  }

  uint64_t
  emergencyVehicleAlert::BuildSyntheticPktUid (long txId, long msgSeq) const
  {
    const uint64_t tx = (txId >= 0) ? (static_cast<uint64_t> (txId) & 0xffffffffULL) : 0ULL;
    const uint64_t seq = (msgSeq >= 0) ? (static_cast<uint64_t> (msgSeq) & 0xffffffffULL) : 0ULL;
    // High bit marks IDs inferred from CAM silence (not ns-3 packet UIDs).
    return (1ULL << 63) | (tx << 32) | seq;
  }

  void
  emergencyVehicleAlert::EmitInferredCamDropNoAction (long txId, long msgSeq, const std::string& dropType)
  {
    if (m_cam_silence_last_emitted_seq >= 0 &&
        msgSeq <= static_cast<long> (m_cam_silence_last_emitted_seq))
      {
        return;
      }
    m_cam_silence_last_emitted_seq = msgSeq;

    const uint64_t pktUid = BuildSyntheticPktUid (txId, msgSeq);
    long rx_id = 0;
    if (m_id.size () > 3)
      {
        rx_id = std::stol (m_id.substr (3));
      }

    if (!m_csv_name.empty () && m_csv_ofstream_msg.is_open ())
      {
        m_csv_ofstream_msg << m_id << "," << msgSeq << ",,"
                           << Simulator::Now ().GetSeconds ()
                           << "," << 0 << "," << dropType << ","
                           << txId << "," << rx_id << "," << msgSeq
                           << "," << pktUid << std::endl;
      }

    LogControlEvent ("drop_decision_no_action",
                     txId,
                     msgSeq,
                     pktUid,
                     -1.0,
                     -1.0,
                     -1,
                     -1,
                     -1.0);
    MaybeTriggerCrashModeOnNoActionDrop (txId, msgSeq, pktUid);
  }

  void
  emergencyVehicleAlert::EvaluateCamSilenceInference (long txId, long msgSeq)
  {
    if (!m_cam_silence_drop_inference_enable || (!m_crash_mode_enable && !m_v2x_awareness_junction_enable) || m_type == "emergency")
      {
        return;
      }
    if (m_cam_silence_focus_tx_id >= 0 && txId != m_cam_silence_focus_tx_id)
      {
        return;
      }

    const double nowS = Simulator::Now ().GetSeconds ();
    const auto key = std::make_pair (txId, msgSeq);
    m_pending_cam_tx_time_s.erase (key);
    m_cam_silence_seen_any = true;
    m_cam_silence_last_msg_seq = static_cast<int64_t> (msgSeq);
    m_cam_silence_last_rx_time_s = nowS;
  }

  void
  emergencyVehicleAlert::CamSilenceWatchdogTick ()
  {
    if (!m_cam_silence_drop_inference_enable || (!m_crash_mode_enable && !m_v2x_awareness_junction_enable) || m_type == "emergency")
      {
        return;
      }

    const double nowS = Simulator::Now ().GetSeconds ();
    const double periodS = std::max (0.01, m_cam_silence_expected_period_ms / 1000.0);
    const double timeoutS = std::max (periodS, m_cam_silence_timeout_s);
    if (m_cam_silence_timeout_s > 0.0 && nowS >= std::max (0.0, m_cam_silence_bootstrap_time_s))
      {
        std::vector<std::pair<long, long>> expired;
        expired.reserve (m_pending_cam_tx_time_s.size ());
        for (const auto& it : m_pending_cam_tx_time_s)
          {
            if ((nowS - it.second) >= timeoutS)
              {
                expired.push_back (it.first);
              }
          }
        for (const auto& key : expired)
          {
            EmitInferredCamDropNoAction (key.first, key.second, "CAM_DROP_PHY_INFERRED");
            m_pending_cam_tx_time_s.erase (key);
          }
      }

    m_cam_silence_watchdog_ev =
      Simulator::Schedule (Seconds (periodS), &emergencyVehicleAlert::CamSilenceWatchdogTick, this);
  }

  void
  emergencyVehicleAlert::SensorWatchdogTick ()
  {
    const double periodS = std::max (0.01, m_sensor_reaction_period_ms / 1000.0);
    if (!m_sensor_reaction_enable || m_type == "emergency" || m_LDM == nullptr || m_client == nullptr)
      {
        return;
      }

    std::vector<LDM::returnedVehicleData_t> localDetections;
    const bool hasDetections = m_LDM->getAllPOs (localDetections);

    bool hazardDetected = false;
    double minHazardDistance = std::numeric_limits<double>::infinity ();
    double minHazardTtc = std::numeric_limits<double>::infinity ();
    long hazardSourceId = -1;
    long hazardMsgSeq = -1;
    if (hasDetections)
      {
        const double egoHeadingDeg = m_client->TraCIAPI::vehicle.getAngle (m_id);
        const double egoSpeed = m_client->TraCIAPI::vehicle.getSpeed (m_id);
        const double distanceThreshold = std::max (0.0, m_sensor_reaction_distance_threshold_m);
        const double ttcThreshold = std::max (0.0, m_sensor_reaction_ttc_threshold_s);
        const double egoHeadingRad = DEG_2_RAD (egoHeadingDeg);
        const double egoVelX = egoSpeed * std::cos (egoHeadingRad);
        const double egoVelY = egoSpeed * std::sin (egoHeadingRad);

        for (auto& detection : localDetections)
          {
            auto& object = detection.vehData;
            if (!object.detected)
              {
                continue;
              }
            if (m_sensor_reaction_focus_station_id >= 0 &&
                static_cast<int64_t> (object.stationID) != m_sensor_reaction_focus_station_id)
              {
                continue;
              }

            double relPosX = 0.0;
            double relPosY = 0.0;
            if (object.xDistAbs.isAvailable () && object.yDistAbs.isAvailable ())
              {
                relPosX = static_cast<double> (object.xDistAbs.getData ()) / CENTI;
                relPosY = static_cast<double> (object.yDistAbs.getData ()) / CENTI;
              }
            else
              {
                libsumo::TraCIPosition egoPos = m_client->TraCIAPI::vehicle.getPosition (m_id);
                libsumo::TraCIPosition objectPos;
                std::string objectLookupId = object.ID;
                if (objectLookupId.rfind ("veh", 0) != 0 &&
                    objectLookupId.find_first_not_of ("0123456789") == std::string::npos)
                  {
                    objectLookupId = "veh" + objectLookupId;
                  }
                try
                  {
                    objectPos = m_client->TraCIAPI::vehicle.getPosition (objectLookupId);
                  }
                catch (const std::exception &e)
                  {
                    if (objectLookupId == object.ID)
                      {
                        NS_LOG_WARN ("Sensor reaction object position lookup failed for '"
                                     << object.ID << "': " << e.what ());
                        continue;
                      }
                    try
                      {
                        objectPos = m_client->TraCIAPI::vehicle.getPosition (object.ID);
                      }
                    catch (const std::exception &fallbackError)
                      {
                        NS_LOG_WARN ("Sensor reaction object position lookup failed for '"
                                     << objectLookupId << "' and '" << object.ID
                                     << "': " << fallbackError.what ());
                        continue;
                      }
                  }
                relPosX = objectPos.x - egoPos.x;
                relPosY = objectPos.y - egoPos.y;
              }

            const double distance = std::hypot (relPosX, relPosY);
            if (distance > distanceThreshold)
              {
                continue;
              }

            double objectVelX = 0.0;
            double objectVelY = 0.0;
            if (object.xSpeedAbs.isAvailable () && object.ySpeedAbs.isAvailable ())
              {
                objectVelX = static_cast<double> (object.xSpeedAbs.getData ()) / CENTI;
                objectVelY = static_cast<double> (object.ySpeedAbs.getData ()) / CENTI;
              }
            else
              {
                const double objectHeadingRad = DEG_2_RAD (object.heading);
                objectVelX = object.speed_ms * std::cos (objectHeadingRad);
                objectVelY = object.speed_ms * std::sin (objectHeadingRad);
              }

            const double relVelX = objectVelX - egoVelX;
            const double relVelY = objectVelY - egoVelY;
            const double rangeRate = ((relPosX * relVelX) + (relPosY * relVelY)) / distance;
            const double closingSpeed = -rangeRate;
            if (closingSpeed <= 1e-3)
              {
                continue;
              }
            const double ttc = distance / closingSpeed;
            if (ttc <= ttcThreshold && ttc < minHazardTtc)
              {
                hazardDetected = true;
                minHazardDistance = distance;
                minHazardTtc = ttc;
                hazardSourceId = static_cast<long> (object.stationID);
                hazardMsgSeq = static_cast<long> (object.camTimestamp / 1000);
              }
          }
      }

    if (hazardDetected)
      {
        ApplyEvasiveControl ("sensor_reaction",
                            hazardSourceId,
                            hazardMsgSeq,
                            static_cast<uint64_t> (-1),
                            minHazardDistance,
                            -1.0);
      }

    m_sensor_watchdog_ev =
      Simulator::Schedule (Seconds (periodS), &emergencyVehicleAlert::SensorWatchdogTick, this);
  }

  void
  emergencyVehicleAlert::receiveCAM (asn1cpp::Seq<CAM> cam, Address from, StationId_t my_stationID, StationType_t my_StationType, SignalInfo phy_info)
  {
    /* Implement CAM strategy here */
   (void) from;
   (void) my_stationID;
   (void) my_StationType;
   long tx_id = asn1cpp::getField (cam->header.stationId,long);
   long cam_gdt_ms = asn1cpp::getField (cam->cam.generationDeltaTime,long);
   long rx_id = 0;
   if (m_id.size () > 3)
     {
       rx_id = std::stol(m_id.substr (3));
     }

   if (m_rx_drop_prob_cam > 0.0 && m_drop_rv != nullptr && m_drop_rv->GetValue () < m_rx_drop_prob_cam)
     {
       m_cam_dropped_app++;
       if (!m_csv_name.empty () && m_csv_ofstream_msg.is_open ())
         {
           m_csv_ofstream_msg << m_id << "," << cam_gdt_ms << ",,"
                              << Simulator::Now ().GetSeconds ()
                              << "," << 0 << ",CAM_DROP_APP,"
                              << tx_id << "," << rx_id << "," << cam_gdt_ms << "," << -1 << std::endl;
         }
       return;
     }
   m_cam_received++;
   EvaluateCamSilenceInference (tx_id, cam_gdt_ms);

   /* If the CAM is received from an emergency vehicle, and the host vehicle is a "passenger" car, then process the CAM */
   if (asn1cpp::getField(cam->cam.camParameters.basicContainer.stationType,StationType_t)==StationType_specialVehicle && m_type!="emergency")
   {
     /* V2X-awareness: receiving a CAM from an emergency vehicle means we now
        know about it.  Grant junction right-of-way awareness (restores
        speedMode bit 3) so SUMO yields properly.  This is a one-time
        transition: once aware, always aware for this simulation. */
     GrantJunctionAwareness ();

     libsumo::TraCIPosition pos=m_client->TraCIAPI::vehicle.getPosition(m_id);
     pos=m_client->TraCIAPI::simulation.convertXYtoLonLat (pos.x,pos.y);
     double emergencyLat = asn1cpp::getField(cam->cam.camParameters.basicContainer.referencePosition.latitude,double)/DOT_ONE_MICRO;
     double emergencyLon = asn1cpp::getField(cam->cam.camParameters.basicContainer.referencePosition.longitude,double)/DOT_ONE_MICRO;
     double distance = appUtil_haversineDist (pos.y,pos.x, emergencyLat, emergencyLon);
     double headingDiff = appUtil_angDiff (m_client->TraCIAPI::vehicle.getAngle (m_id),
                                           (double)asn1cpp::getField(cam->cam.camParameters.highFrequencyContainer.choice.basicVehicleContainerHighFrequency.heading.headingValue,HeadingValue_t)/DECI);

     if (distance < m_distance_threshold && headingDiff < m_heading_threshold)
     {
       ApplyEvasiveControl ("cam_reaction", tx_id, cam_gdt_ms, static_cast<uint64_t> (-1), distance, headingDiff);
     }
   }

   if (!m_csv_name.empty ())
     {
       // messageId,camId,timestamp,latitude,longitude,heading,speed,acceleration
       m_csv_ofstream_cam << cam->header.messageId << "," << cam->header.stationId << ",";
       m_csv_ofstream_cam << cam->cam.generationDeltaTime << "," << asn1cpp::getField(cam->cam.camParameters.basicContainer.referencePosition.latitude,double)/DOT_ONE_MICRO << ",";
       m_csv_ofstream_cam << asn1cpp::getField(cam->cam.camParameters.basicContainer.referencePosition.longitude,double)/DOT_ONE_MICRO << "," ;
       m_csv_ofstream_cam << asn1cpp::getField(cam->cam.camParameters.highFrequencyContainer.choice.basicVehicleContainerHighFrequency.heading.headingValue,double)/DECI << "," << asn1cpp::getField(cam->cam.camParameters.highFrequencyContainer.choice.basicVehicleContainerHighFrequency.speed.speedValue,double)/CENTI << ",";
       m_csv_ofstream_cam << asn1cpp::getField(cam->cam.camParameters.highFrequencyContainer.choice.basicVehicleContainerHighFrequency.longitudinalAcceleration.value,double)/DECI << std::endl;
     }
   if (!m_csv_name.empty () && m_csv_ofstream_msg.is_open ())
     {
       m_csv_ofstream_msg << m_id << "," << cam_gdt_ms << ",," << Simulator::Now ().GetSeconds ()
                          << "," << 1 << ",CAM," << tx_id << "," << rx_id << "," << cam_gdt_ms << "," << -1 << std::endl;
     }

   // ── PHY-level metrics logging ──
   if (!m_csv_name.empty () && m_csv_ofstream_phy.is_open ())
     {
       double sinr_db = std::isnan (phy_info.sinr) ? std::numeric_limits<double>::quiet_NaN () : phy_info.sinr;
       double snr_db = std::isnan (phy_info.snr) ? std::numeric_limits<double>::quiet_NaN () : phy_info.snr;
       double rssi_dbm = std::isnan (phy_info.rssi) ? std::numeric_limits<double>::quiet_NaN () : phy_info.rssi;
       double rsrp_dbm = std::isnan (phy_info.rsrp) ? std::numeric_limits<double>::quiet_NaN () : phy_info.rsrp;

       // Compute distance to sender
       double distance_m = -1.0;
       try
         {
           libsumo::TraCIPosition myPos = m_client->TraCIAPI::vehicle.getPosition (m_id);
           myPos = m_client->TraCIAPI::simulation.convertXYtoLonLat (myPos.x, myPos.y);
           double senderLat = asn1cpp::getField (cam->cam.camParameters.basicContainer.referencePosition.latitude, double) / DOT_ONE_MICRO;
           double senderLon = asn1cpp::getField (cam->cam.camParameters.basicContainer.referencePosition.longitude, double) / DOT_ONE_MICRO;
           distance_m = appUtil_haversineDist (myPos.y, myPos.x, senderLat, senderLon);
         }
       catch (...)
         {
           distance_m = -1.0;
         }

       m_csv_ofstream_phy << std::fixed << std::setprecision (4)
                          << Simulator::Now ().GetSeconds () << ","
                          << m_id << ",CAM,"
                          << tx_id << ","
                          << sinr_db << ","
                          << snr_db << ","
                          << rssi_dbm << ","
                          << rsrp_dbm << ","
                          << phy_info.size << ","
                          << distance_m << ","
                          << 1 << std::endl;
     }

  }

  void
  emergencyVehicleAlert::receiveCPMV1 (asn1cpp::Seq<CPMV1> cpm, Address from)
  {
    long tx_id = asn1cpp::getField (cpm->header.stationId,long);
    long msg_seq = asn1cpp::getField (cpm->cpm.generationDeltaTime,long);
    long rx_id = 0;
    if (m_id.size () > 3)
      {
        rx_id = std::stol(m_id.substr (3));
      }
    if (m_rx_drop_prob_cpm > 0.0 && m_drop_rv != nullptr && m_drop_rv->GetValue () < m_rx_drop_prob_cpm)
      {
        m_cpm_dropped_app++;
        if (!m_csv_name.empty () && m_csv_ofstream_msg.is_open ())
          {
            m_csv_ofstream_msg << m_id << "," << msg_seq << ",,"
                               << Simulator::Now ().GetSeconds ()
                               << "," << 0 << ",CPM_DROP_APP,"
                               << tx_id << "," << rx_id << "," << msg_seq << "," << -1 << std::endl;
          }
        return;
      }
    m_cpm_received++;
    (void) from;

    bool cpmHazardDetected = false;
    double minHazardDistance = std::numeric_limits<double>::infinity ();
    double minHazardTtc = std::numeric_limits<double>::infinity ();
    const bool canApplyCpmControl = (m_type != "emergency");
    const double cpmDistanceThreshold = std::max (0.0, m_cpm_distance_threshold);
    const double cpmTtcThreshold = std::max (0.0, m_cpm_ttc_threshold_s);
    double egoLat = 0.0;
    double egoLon = 0.0;
    double egoSpeed = 0.0;
    if (canApplyCpmControl && cpmDistanceThreshold > 0.0 && cpmTtcThreshold > 0.0)
      {
        libsumo::TraCIPosition egoPos = m_client->TraCIAPI::vehicle.getPosition (m_id);
        egoPos = m_client->TraCIAPI::simulation.convertXYtoLonLat (egoPos.x, egoPos.y);
        egoLon = egoPos.x;
        egoLat = egoPos.y;
        egoSpeed = m_client->TraCIAPI::vehicle.getSpeed (m_id);
      }

    NS_LOG_INFO ("[" << Simulator::Now ().GetSeconds () << "] " << m_id
                 << " received a new CPMv1 from vehicle " << tx_id
                 << " with "
                 << asn1cpp::getField(cpm->cpm.cpmParameters.numberOfPerceivedObjects,long)
                 << " perceived objects.");
    //For every PO inside the CPM, if any
    bool POs_ok;
    auto PObjects = asn1cpp::getSeqOpt(cpm->cpm.cpmParameters.perceivedObjectContainer,PerceivedObjectContainer,&POs_ok);
    if (POs_ok)
      {
        int PObjects_size = asn1cpp::sequenceof::getSize(cpm->cpm.cpmParameters.perceivedObjectContainer);
        for(int i=0; i<PObjects_size;i++)
          {
            LDM::returnedVehicleData_t PO_data;
            auto PO_seq = asn1cpp::makeSeq(PerceivedObjectV1);
            PO_seq = asn1cpp::sequenceof::getSeq(cpm->cpm.cpmParameters.perceivedObjectContainer,PerceivedObjectV1,i);
            vehicleData_t translatedObject = translateCPMV1data (cpm, i);
            //If PO is already in local copy of vLDM
            if(m_LDM->lookup(asn1cpp::getField(PO_seq->objectID,long),PO_data) == LDM::LDM_OK)
              {
                  //Add the new perception to the LDM
                  std::vector<long> associatedCVs;
                  if (PO_data.vehData.associatedCVs.isAvailable ())
                    {
                      associatedCVs = PO_data.vehData.associatedCVs.getData ();
                    }
                  if(std::find(associatedCVs.begin(), associatedCVs.end (), asn1cpp::getField(cpm->header.stationId,long)) == associatedCVs.end ())
                    associatedCVs.push_back (asn1cpp::getField(cpm->header.stationId,long));
                  translatedObject.associatedCVs = OptionalDataItem<std::vector<long>>(associatedCVs);
                  m_LDM->insert (translatedObject);
              }
            else
              {
               //Translate CPM data to LDM format
               m_LDM->insert(translatedObject);
              }

            if (canApplyCpmControl && cpmDistanceThreshold > 0.0 && cpmTtcThreshold > 0.0)
              {
                double distance = appUtil_haversineDist (egoLat, egoLon, translatedObject.lat, translatedObject.lon);
                if (distance <= cpmDistanceThreshold)
                  {
                    double closingSpeed = egoSpeed - translatedObject.speed_ms;
                    if (closingSpeed > 0.1)
                      {
                        double ttc = distance / closingSpeed;
                        if (ttc <= cpmTtcThreshold && ttc < minHazardTtc)
                          {
                            cpmHazardDetected = true;
                            minHazardTtc = ttc;
                            minHazardDistance = distance;
                          }
                      }
                  }
              }
          }
      }

    if (cpmHazardDetected)
      {
        ApplyEvasiveControl ("cpm_reaction", tx_id, msg_seq, static_cast<uint64_t> (-1), minHazardDistance, -1.0);
      }
  }

  vehicleData_t
  emergencyVehicleAlert::translateCPMV1data (asn1cpp::Seq<CPMV1> cpm, int objectIndex)
  {
    vehicleData_t retval;
    auto PO_seq = asn1cpp::makeSeq(PerceivedObjectV1);
    using namespace boost::geometry::strategy::transform;
    PO_seq = asn1cpp::sequenceof::getSeq(cpm->cpm.cpmParameters.perceivedObjectContainer,PerceivedObjectV1,objectIndex);
    retval.detected = true;
    retval.stationID = asn1cpp::getField(PO_seq->objectID,long);
    retval.ID = std::to_string(retval.stationID);
    retval.vehicleLength = asn1cpp::getField(PO_seq->planarObjectDimension1->value,long);
    retval.vehicleWidth = asn1cpp::getField(PO_seq->planarObjectDimension2->value,long);
    retval.heading = asn1cpp::getField(cpm->cpm.cpmParameters.stationDataContainer->choice.originatingVehicleContainer.heading.headingValue,double)/10 +
                        asn1cpp::getField(PO_seq->yawAngle->value,double)/10;
    if (retval.heading > 360.0)
      retval.heading -= 360.0;

    retval.speed_ms = (double) (asn1cpp::getField(cpm->cpm.cpmParameters.stationDataContainer->choice.originatingVehicleContainer.speed.speedValue,long) +
                        asn1cpp::getField(PO_seq->xSpeed.value,long))/CENTI;

    double fromLon = asn1cpp::getField(cpm->cpm.cpmParameters.managementContainer.referencePosition.longitude,double)/DOT_ONE_MICRO;
    double fromLat = asn1cpp::getField(cpm->cpm.cpmParameters.managementContainer.referencePosition.latitude,double)/DOT_ONE_MICRO;


    libsumo::TraCIPosition objectPosition = m_client->TraCIAPI::simulation.convertLonLattoXY (fromLon,fromLat);

    point_type objPoint(asn1cpp::getField(PO_seq->xDistance.value,double)/CENTI,asn1cpp::getField(PO_seq->yDistance.value,double)/CENTI);
    double fromAngle = asn1cpp::getField(cpm->cpm.cpmParameters.stationDataContainer->choice.originatingVehicleContainer.heading.headingValue,double)/10;
    rotate_transformer<boost::geometry::degree, double, 2, 2> rotate(fromAngle-90);
    boost::geometry::transform(objPoint, objPoint, rotate);// Transform points to the reference (x,y) axises
    objectPosition.x += boost::geometry::get<0>(objPoint);
    objectPosition.y += boost::geometry::get<1>(objPoint);

    libsumo::TraCIPosition objectPosition2 = objectPosition;
    objectPosition = m_client->TraCIAPI::simulation.convertXYtoLonLat (objectPosition.x,objectPosition.y);

    retval.lon = objectPosition.x;
    retval.lat = objectPosition.y;

    point_type speedPoint(asn1cpp::getField(PO_seq->xSpeed.value,double)/CENTI,asn1cpp::getField(PO_seq->ySpeed.value,double)/CENTI);
    boost::geometry::transform(speedPoint, speedPoint, rotate);// Transform points to the reference (x,y) axises
    retval.speed_ms = asn1cpp::getField(cpm->cpm.cpmParameters.stationDataContainer->choice.originatingVehicleContainer.speed.speedValue,double)/CENTI + boost::geometry::get<0>(speedPoint);

    retval.camTimestamp = asn1cpp::getField(cpm->cpm.generationDeltaTime,long);
    retval.timestamp_us = Simulator::Now().GetMicroSeconds () - (asn1cpp::getField(PO_seq->timeOfMeasurement,long)*1000);
    retval.stationType = StationType_passengerCar;
    retval.perceivedBy.setData(asn1cpp::getField(cpm->header.stationId,long));
    retval.confidence = asn1cpp::getField(PO_seq->objectConfidence,long);
    return retval;

  }

  void
  emergencyVehicleAlert::receiveDENM (denData denm, Address from)
  {
    /* This is just a sample dummy receiveDENM function. The user can customize it to parse the content of a DENM when it is received. */
    (void) denm; // Contains the data received from the DENM
    (void) from; // Contains the address from which the DENM has been received
    NS_LOG_INFO ("Received a new DENM.");
  }

  void
  emergencyVehicleAlert::SetMaxSpeed ()
  {
    libsumo::TraCIColor normal;
    normal.r=0;normal.g=225;normal.b=255;normal.a=255;
    m_client->TraCIAPI::vehicle.setColor (m_id, normal);
    m_client->TraCIAPI::vehicle.setMaxSpeed (m_id, m_max_speed);
    if (m_reaction_force_lane_change_enable)
      {
        m_client->TraCIAPI::vehicle.setLaneChangeMode (m_id, 1621);
      }
  }
  void
  emergencyVehicleAlert::receiveCPM (asn1cpp::Seq<CollectivePerceptionMessage> cpm, Address from)
  {
    long tx_id = asn1cpp::getField (cpm->header.stationId,long);
    long msg_seq = asn1cpp::getField (cpm->payload.managementContainer.referenceTime,long);
    long rx_id = 0;
    if (m_id.size () > 3)
      {
        rx_id = std::stol(m_id.substr (3));
      }
    if (m_rx_drop_prob_cpm > 0.0 && m_drop_rv != nullptr && m_drop_rv->GetValue () < m_rx_drop_prob_cpm)
      {
        m_cpm_dropped_app++;
        if (!m_csv_name.empty () && m_csv_ofstream_msg.is_open ())
          {
            m_csv_ofstream_msg << m_id << "," << msg_seq << ",,"
                               << Simulator::Now ().GetSeconds ()
                               << "," << 0 << ",CPM_DROP_APP,"
                               << tx_id << "," << rx_id << "," << msg_seq << "," << -1 << std::endl;
          }
        return;
      }
    m_cpm_received++;
    (void) from;

    bool cpmHazardDetected = false;
    double minHazardDistance = std::numeric_limits<double>::infinity ();
    double minHazardTtc = std::numeric_limits<double>::infinity ();
    const bool canApplyCpmControl = (m_type != "emergency");
    const double cpmDistanceThreshold = std::max (0.0, m_cpm_distance_threshold);
    const double cpmTtcThreshold = std::max (0.0, m_cpm_ttc_threshold_s);
    double egoLat = 0.0;
    double egoLon = 0.0;
    double egoSpeed = 0.0;
    if (canApplyCpmControl && cpmDistanceThreshold > 0.0 && cpmTtcThreshold > 0.0)
      {
        libsumo::TraCIPosition egoPos = m_client->TraCIAPI::vehicle.getPosition (m_id);
        egoPos = m_client->TraCIAPI::simulation.convertXYtoLonLat (egoPos.x, egoPos.y);
        egoLon = egoPos.x;
        egoLat = egoPos.y;
        egoSpeed = m_client->TraCIAPI::vehicle.getSpeed (m_id);
      }

    //For every PO inside the CPM, if any
    //auto wrappedContainer = asn1cpp::makeSeq(WrappedCpmContainer);
    int wrappedContainer_size = asn1cpp::sequenceof::getSize(cpm->payload.cpmContainers);
    for (int i=0; i<wrappedContainer_size; i++)
      {
        auto wrappedContainer = asn1cpp::sequenceof::getSeq(cpm->payload.cpmContainers,WrappedCpmContainer,i);
        WrappedCpmContainer__containerData_PR present = asn1cpp::getField(wrappedContainer->containerData.present,WrappedCpmContainer__containerData_PR);
        if(present == WrappedCpmContainer__containerData_PR_PerceivedObjectContainer)
        {
          auto POcontainer = asn1cpp::getSeq(wrappedContainer->containerData.choice.PerceivedObjectContainer,PerceivedObjectContainer);
          int PObjects_size = asn1cpp::sequenceof::getSize(POcontainer->perceivedObjects);
          NS_LOG_INFO ("[" << Simulator::Now ().GetSeconds () << "] " << m_id
                       << " received a new CPMv2 from " << tx_id
                       << " with " << PObjects_size << " perceived objects.");
          for(int j=0; j<PObjects_size;j++)
              {
               LDM::returnedVehicleData_t PO_data;
               auto PO_seq = asn1cpp::makeSeq(PerceivedObject);
               PO_seq = asn1cpp::sequenceof::getSeq(POcontainer->perceivedObjects,PerceivedObject,j);
               vehicleData_t translatedObject = translateCPMdata (cpm, PO_seq, j);
               //If PO is already in local copy of vLDM
               if(m_LDM->lookup(asn1cpp::getField(PO_seq->objectId,long),PO_data) == LDM::LDM_OK)
                    {
                      //Add the new perception to the LDM
                      std::vector<long> associatedCVs;
                      if (PO_data.vehData.associatedCVs.isAvailable ())
                        {
                          associatedCVs = PO_data.vehData.associatedCVs.getData ();
                        }
                      if(std::find(associatedCVs.begin(), associatedCVs.end (), asn1cpp::getField(cpm->header.stationId,long)) == associatedCVs.end ())
                        associatedCVs.push_back (asn1cpp::getField(cpm->header.stationId,long));
                      translatedObject.associatedCVs = OptionalDataItem<std::vector<long>>(associatedCVs);
                      m_LDM->insert (translatedObject);
                    }
               else
                    {
                      //Translate CPM data to LDM format
                      m_LDM->insert(translatedObject);
                    }

               if (canApplyCpmControl && cpmDistanceThreshold > 0.0 && cpmTtcThreshold > 0.0)
                 {
                   double distance = appUtil_haversineDist (egoLat, egoLon, translatedObject.lat, translatedObject.lon);
                   if (distance <= cpmDistanceThreshold)
                     {
                       double closingSpeed = egoSpeed - translatedObject.speed_ms;
                       if (closingSpeed > 0.1)
                         {
                           double ttc = distance / closingSpeed;
                           if (ttc <= cpmTtcThreshold && ttc < minHazardTtc)
                             {
                               cpmHazardDetected = true;
                               minHazardTtc = ttc;
                               minHazardDistance = distance;
                             }
                         }
                     }
                 }
              }
        }
      }

    if (cpmHazardDetected)
      {
        ApplyEvasiveControl ("cpm_reaction", tx_id, msg_seq, static_cast<uint64_t> (-1), minHazardDistance, -1.0);
      }
  }
  vehicleData_t
  emergencyVehicleAlert::translateCPMdata (asn1cpp::Seq<CollectivePerceptionMessage> cpm,
                                           asn1cpp::Seq<PerceivedObject> object, int objectIndex)
  {
    vehicleData_t retval;
    retval.detected = true;
    retval.stationID = asn1cpp::getField(object->objectId,long);
    retval.ID = std::to_string(retval.stationID);
    retval.vehicleLength = asn1cpp::getField(object->objectDimensionX->value,long);
    retval.vehicleWidth = asn1cpp::getField(object->objectDimensionY->value,long);
    retval.heading = asn1cpp::getField(object->angles->zAngle.value,double) / DECI;
    retval.xSpeedAbs.setData (asn1cpp::getField(object->velocity->choice.cartesianVelocity.xVelocity.value,long));
    retval.ySpeedAbs.setData (asn1cpp::getField(object->velocity->choice.cartesianVelocity.yVelocity.value,long));
    retval.speed_ms = (sqrt (pow(retval.xSpeedAbs.getData(),2) +
                             pow(retval.ySpeedAbs.getData(),2)))/CENTI;

    libsumo::TraCIPosition fromPosition = m_client->TraCIAPI::simulation.convertLonLattoXY (asn1cpp::getField(cpm->payload.managementContainer.referencePosition.longitude,double)/DOT_ONE_MICRO,
                                                                                           asn1cpp::getField(cpm->payload.managementContainer.referencePosition.latitude,double)/DOT_ONE_MICRO);
    libsumo::TraCIPosition objectPosition = fromPosition;
    objectPosition.x += asn1cpp::getField(object->position.xCoordinate.value,long)/CENTI;
    objectPosition.y += asn1cpp::getField(object->position.yCoordinate.value,long)/CENTI;
    objectPosition = m_client->TraCIAPI::simulation.convertXYtoLonLat (objectPosition.x,objectPosition.y);
    retval.lon = objectPosition.x;
    retval.lat = objectPosition.y;

    retval.camTimestamp = asn1cpp::getField(cpm->payload.managementContainer.referenceTime,long);
    retval.timestamp_us = Simulator::Now().GetMicroSeconds () - (asn1cpp::getField(object->measurementDeltaTime,long)*1000);
    retval.stationType = StationType_passengerCar;
    retval.perceivedBy.setData(asn1cpp::getField(cpm->header.stationId,long));

        return retval;
  }


  }
