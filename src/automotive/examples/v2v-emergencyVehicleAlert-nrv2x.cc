/* -*-  Mode: C++; c-file-style: "gnu"; indent-tabs-mode:nil; -*- */
/*
 *   Copyright (c) 2020 Centre Tecnologic de Telecomunicacions de Catalunya (CTTC)
 *
 *   This program is free software; you can redistribute it and/or modify
 *   it under the terms of the GNU General Public License version 2 as
 *   published by the Free Software Foundation;
 *
 *   This program is distributed in the hope that it will be useful,
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *   GNU General Public License for more details.
 *
 *   You should have received a copy of the GNU General Public License
 *   along with this program; if not, write to the Free Software
 *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 *
 */

#include "ns3/carla-module.h"
//#include "ns3/automotive-module.h"
#include "ns3/emergencyVehicleAlert-helper.h"
#include "ns3/emergencyVehicleAlert.h"
#include "ns3/traci-module.h"
#include "ns3/config-store.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/applications-module.h"
#include "ns3/mobility-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/nr-module.h"
#include "ns3/lte-module.h"
#include "ns3/stats-module.h"
#include "ns3/config-store-module.h"
#include "ns3/sionna-helper.h"
#include "ns3/log.h"
#include "ns3/antenna-module.h"
#include "../../nr/examples/nr-v2x-examples/ue-mac-pscch-tx-output-stats.h"
#include "../../nr/examples/nr-v2x-examples/ue-mac-pssch-tx-output-stats.h"
#include "../../nr/examples/nr-v2x-examples/ue-phy-pscch-rx-output-stats.h"
#include "../../nr/examples/nr-v2x-examples/ue-phy-pssch-rx-output-stats.h"
#include "../../nr/examples/nr-v2x-examples/ue-rlc-rx-output-stats.h"
#include <iomanip>
#include "ns3/sumo_xml_parser.h"
#include "ns3/vehicle-visualizer-module.h"
#include "ns3/MetricSupervisor.h"


#include <unistd.h>
#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <functional>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <unordered_map>
#include <vector>
#include "ns3/core-module.h"


using namespace ns3;

NS_LOG_COMPONENT_DEFINE("v2v-nrv2x");

namespace
{

std::string
TrimCopy (const std::string& input)
{
  size_t begin = 0;
  while (begin < input.size () && std::isspace (static_cast<unsigned char> (input[begin])))
    {
      ++begin;
    }
  size_t end = input.size ();
  while (end > begin && std::isspace (static_cast<unsigned char> (input[end - 1])))
    {
      --end;
    }
  return input.substr (begin, end - begin);
}

std::vector<std::string>
SplitByDelim (const std::string& input, char delim)
{
  std::vector<std::string> out;
  std::stringstream ss (input);
  std::string item;
  while (std::getline (ss, item, delim))
    {
      out.push_back (item);
    }
  return out;
}

bool
TryParseFiniteDouble (const std::string& token, double& out)
{
  try
    {
      size_t consumed = 0;
      const double parsed = std::stod (token, &consumed);
      if (consumed != token.size () || !std::isfinite (parsed))
        {
          return false;
        }
      out = parsed;
      return true;
    }
  catch (const std::exception&)
    {
      return false;
    }
}

std::unordered_map<std::string, double>
ParsePerVehicleEquivalentDbm (const std::string& profile)
{
  std::unordered_map<std::string, double> out;
  if (profile.empty ())
    {
      return out;
    }

  std::string normalized = profile;
  std::replace (normalized.begin (), normalized.end (), ';', ',');
  for (const std::string& rawEntry : SplitByDelim (normalized, ','))
    {
      const std::string entry = TrimCopy (rawEntry);
      if (entry.empty ())
        {
          continue;
        }
      const std::vector<std::string> fields = SplitByDelim (entry, ':');
      if (fields.size () < 3)
        {
          continue;
        }
      const std::string vehicleId = TrimCopy (fields[0]);
      if (vehicleId.empty ())
        {
          continue;
        }
      const std::string equivDbmToken = TrimCopy (fields[2]);
      double equivDbm = 0.0;
      if (!TryParseFiniteDouble (equivDbmToken, equivDbm))
        {
          continue;
        }
      out[vehicleId] = equivDbm;
    }
  return out;
}

void
NotifySlPscchScheduling (UeMacPscchTxOutputStats* pscchStats,
                         const SlPscchUeMacStatParameters pscchStatsParams)
{
  pscchStats->Save (pscchStatsParams);
}

void
NotifySlPsschScheduling (UeMacPsschTxOutputStats* psschStats,
                         const SlPsschUeMacStatParameters psschStatsParams)
{
  psschStats->Save (psschStatsParams);
}

void
NotifySlPscchRx (UePhyPscchRxOutputStats* pscchStats,
                 const SlRxCtrlPacketTraceParams pscchStatsParams)
{
  pscchStats->Save (pscchStatsParams);
}

void
NotifySlPsschRx (UePhyPsschRxOutputStats* psschStats,
                 const SlRxDataPacketTraceParams psschStatsParams)
{
  psschStats->Save (psschStatsParams);
}

void
NotifySlRlcPduRx (UeRlcRxOutputStats* stats,
                  uint64_t imsi,
                  uint16_t rnti,
                  uint16_t txRnti,
                  uint8_t lcid,
                  uint32_t rxPduSize,
                  double delay)
{
  stats->Save (imsi, rnti, txRnti, lcid, rxPduSize, delay);
}

std::string
NormalizeOutputDir (std::string outputDir)
{
  if (!outputDir.empty () && outputDir.back () != '/')
    {
      outputDir.push_back ('/');
    }
  return outputDir;
}

} // namespace

/**
 * \brief Get sidelink bitmap from string
 * \param slBitMapString The sidelink bitmap string
 * \param slBitMapVector The vector passed to store the converted sidelink bitmap
 */
void
GetSlBitmapFromString (std::string slBitMapString, std::vector <std::bitset<1> > &slBitMapVector)
{
  static std::unordered_map<std::string, uint8_t> lookupTable =
  {
    { "0", 0 },
    { "1", 1 },
  };

  std::stringstream ss (slBitMapString);
  std::string token;
  std::vector<std::string> extracted;

  while (std::getline (ss, token, '|'))
    {
      extracted.push_back (token);
    }

  for (const auto & v : extracted)
    {
      if (lookupTable.find (v) == lookupTable.end ())
        {
          NS_FATAL_ERROR ("Bit type " << v << " not valid. Valid values are: 0 and 1");
        }
      slBitMapVector.push_back (lookupTable[v] & 0x01);
    }
}


int
main (int argc, char *argv[])
{

  std::string sumo_folder = "src/automotive/examples/sumo_files_v2v_map/";
  std::string mob_trace = "cars.rou.xml";
  std::string sumo_config ="src/automotive/examples/sumo_files_v2v_map/map.sumo.cfg";

  /*** 0.a App Options ***/
  bool verbose = true;
  bool realtime = false;
  bool sumo_gui = true;
  double sumo_updates = 0.01;
  uint16_t sumo_port = 3400;
  int64_t sumo_seed = 10;
  std::string csv_name;
  std::string csv_name_cumulative;
  std::string sumo_netstate_file_name;
  bool enable_official_sqlite = false;
  std::string simTag = "default";
  std::string outputDir = "./";
  bool vehicle_vis = false;

  int numberOfNodes;
  uint32_t nodeCounter = 0;

  double penetrationRate = 0.7;
  bool send_cam = true;
  bool send_cpm = true;
  bool sionna = false;
  std::string server_ip = "";
  bool local_machine = false;
  bool sionna_verbose = false;

  xmlDocPtr rou_xml_file;
  double m_baseline_prr = 150.0;
  bool m_metric_sup = false;
  double rx_drop_prob_cam = 0.0;
  double rx_drop_prob_cpm = 0.0;
  double rx_drop_prob_phy_cam = 0.0;
  double rx_drop_prob_phy_cpm = 0.0;
  bool drop_triggered_reaction_enable = false;
  bool target_loss_profile_enable = false;
  std::string target_loss_vehicle_id = "veh9";
  double target_loss_rx_drop_prob_cam = 1.0;
  double target_loss_rx_drop_prob_cpm = 0.0;
  double target_loss_rx_drop_prob_phy_cam = 1.0;
  double target_loss_rx_drop_prob_phy_cpm = 0.0;
  std::string per_vehicle_prr_profile = "";
  bool cam_silence_drop_inference_enable = false;
  int64_t cam_silence_focus_tx_id = 2;
  double cam_silence_expected_period_ms = 600.0;
  double cam_silence_timeout_s = 0.95;
  double cam_silence_bootstrap_time_s = 2.2;
  double cam_reaction_distance_m = 75.0;
  double cam_reaction_heading_deg = 45.0;
  int cam_reaction_target_lane = 0;
  double cam_reaction_speed_factor_target_lane = 0.5;
  double cam_reaction_speed_factor_other_lane = 1.5;
  double cam_reaction_action_duration_s = 3.0;
  double cpm_reaction_distance_m = 60.0;
  double cpm_reaction_ttc_s = 3.0;
  bool sensor_reaction_enable = false;
  double sensor_reaction_distance_m = 20.0;
  double sensor_reaction_ttc_s = 1.5;
  double sensor_reaction_period_ms = 100.0;
  double sensor_range_m = 50.0;
  std::string sensor_reaction_focus_vehicle_id = "";
  double reaction_action_cooldown_s = 0.5;
  bool reaction_force_lane_change_enable = false;
  bool crash_mode_enable = false;
  std::string crash_mode_vehicle_id = "veh9";
  uint32_t crash_mode_no_action_threshold = 5;
  double crash_mode_force_speed_mps = 32.0;
  double crash_mode_duration_s = 4.0;
  double crash_mode_min_time_s = 0.0;
  bool v2x_awareness_junction_enable = false;
  bool incident_enable = false;
  std::string incident_vehicle_id = "veh2";
  double incident_time_s = 12.0;
  double incident_stop_duration_s = 15.0;
  double incident_recover_max_speed_mps = -1.0;
  uint32_t incident_retry_max = 20;
  bool incident_setstop_enable = true;
  std::string sumo_collision_action = "";
  std::string sumo_collision_output = "";
  bool sumo_collision_check_junctions = true;
  double sumo_collision_stoptime_s = -1.0;


  // Simulation parameters.
  double simTime = 100.0;
  //Sidelink bearers activation time
  Time slBearersActivationTime = Seconds (2.0);


  // NR parameters. We will take the input from the command line, and then we
  // will pass them inside the NR module.
  double centralFrequencyBandSl = 5.89e9; // band n47  TDD //Here band is analogous to channel
  uint16_t bandwidthBandSl = 400; // NR sidelink units of 100 kHz: 400 = 40 MHz.
  double txPower = 23; //dBm
  std::string tddPattern = "UL|UL|UL|UL|UL|UL|UL|UL|UL|UL|";
  std::string slBitMap = "1|1|1|1|1|1|1|1|1|1";
  uint16_t numerologyBwpSl = 2;
  uint16_t slSensingWindow = 100; // T0 in ms
  uint16_t slSelectionWindow = 5; // T2min
  uint16_t slSubchannelSize = 10;
  uint16_t slMaxNumPerReserve = 3;
  double slProbResourceKeep = 0.0;
  uint16_t slMaxTxTransNumPssch = 5;
  uint16_t reservationPeriod = 20; // in ms
  bool enableSensing = false;
  uint16_t t1 = 2;
  uint16_t t2 = 81;
  int slThresPsschRsrp = -128;
  bool enableChannelRandomness = false;
  uint16_t channelUpdatePeriod = 500; //ms
  uint8_t mcs = 14;

  /*
   * From here, we instruct the ns3::CommandLine class of all the input parameters
   * that we may accept as input, as well as their description, and the storage
   * variable.
   */
  CommandLine cmd;

  /* Cmd Line option for application */
  cmd.AddValue ("realtime", "Use the realtime scheduler or not", realtime);
  cmd.AddValue ("sumo-gui", "Use SUMO gui or not", sumo_gui);
  cmd.AddValue ("sumo-updates", "SUMO granularity", sumo_updates);
  cmd.AddValue ("sumo-port", "TraCI TCP port for SUMO", sumo_port);
  cmd.AddValue ("sumo-seed", "Seed passed to SUMO", sumo_seed);
  cmd.AddValue ("sumo-folder","Position of sumo config files",sumo_folder);
  cmd.AddValue ("mob-trace", "Name of the mobility trace file", mob_trace);
  cmd.AddValue ("sumo-config", "Location and name of SUMO configuration file", sumo_config);
  cmd.AddValue ("csv-log", "Name of the CSV log file", csv_name);
  cmd.AddValue ("vehicle-visualizer", "Activate the web-based vehicle visualizer for ms-van3t", vehicle_vis);
  cmd.AddValue ("csv-log-cumulative", "Name of the CSV log file for the cumulative (average) PRR and latency data", csv_name_cumulative);
  cmd.AddValue ("netstate-dump-file", "Name of the SUMO netstate-dump file containing the vehicle-related information throughout the whole simulation", sumo_netstate_file_name);
  cmd.AddValue ("enable-official-sqlite",
                "Write official 5G-LENA NR V2X PHY/MAC/RLC traces into a SQLite DB",
                enable_official_sqlite);
  cmd.AddValue ("simTag",
                "Tag appended to the official 5G-LENA SQLite filename",
                simTag);
  cmd.AddValue ("outputDir",
                "Directory where the official 5G-LENA SQLite DB will be stored",
                outputDir);
  cmd.AddValue ("baseline", "Baseline for PRR calculation", m_baseline_prr);
  cmd.AddValue ("met-sup","Use the Metric supervisor or not",m_metric_sup);
  cmd.AddValue ("rx-drop-prob-cam", "Application-level probability to drop received CAM packets", rx_drop_prob_cam);
  cmd.AddValue ("rx-drop-prob-cpm", "Application-level probability to drop received CPM packets", rx_drop_prob_cpm);
  cmd.AddValue ("rx-drop-prob-phy-cam",
                "PHY/MAC-level probability to drop received CAM packets before upper layers",
                rx_drop_prob_phy_cam);
  cmd.AddValue ("rx-drop-prob-phy-cpm",
                "PHY/MAC-level probability to drop received CPM packets before upper layers",
                rx_drop_prob_phy_cpm);
  cmd.AddValue ("drop-triggered-reaction-enable",
                "If true, dropped CAM/CPM may still trigger evasive control; if false, drop implies no_action",
                drop_triggered_reaction_enable);
  cmd.AddValue ("target-loss-profile-enable",
                "Enable per-vehicle drop profile override (single impaired receiver)",
                target_loss_profile_enable);
  cmd.AddValue ("target-loss-vehicle-id",
                "Vehicle id that should receive the per-vehicle drop profile (e.g., veh9)",
                target_loss_vehicle_id);
  cmd.AddValue ("target-loss-rx-drop-prob-cam",
                "Per-vehicle app-level CAM drop probability override",
                target_loss_rx_drop_prob_cam);
  cmd.AddValue ("target-loss-rx-drop-prob-cpm",
                "Per-vehicle app-level CPM drop probability override",
                target_loss_rx_drop_prob_cpm);
  cmd.AddValue ("target-loss-rx-drop-prob-phy-cam",
                "Per-vehicle PHY-level CAM drop probability override",
                target_loss_rx_drop_prob_phy_cam);
  cmd.AddValue ("target-loss-rx-drop-prob-phy-cpm",
                "Per-vehicle PHY-level CPM drop probability override",
                target_loss_rx_drop_prob_phy_cpm);
  cmd.AddValue ("per-vehicle-prr-profile",
                "Comma/semicolon-separated entries: vehId:rxDropPhyCam[:equivTxPowerDbm[:targetPrr[:rxDropPhyCpm]]]",
                per_vehicle_prr_profile);
  cmd.AddValue ("cam-silence-drop-inference-enable",
                "Infer CAM drops from receive silence/sequence gaps (for PHY-only coupling runs)",
                cam_silence_drop_inference_enable);
  cmd.AddValue ("cam-silence-focus-tx-id",
                "StationId whose CAM stream drives silence-based drop inference",
                cam_silence_focus_tx_id);
  cmd.AddValue ("cam-silence-expected-period-ms",
                "Expected CAM periodicity [ms] used by silence-based drop inference",
                cam_silence_expected_period_ms);
  cmd.AddValue ("cam-silence-timeout-s",
                "Timeout [s] with no CAM before inferring a drop event",
                cam_silence_timeout_s);
  cmd.AddValue ("cam-silence-bootstrap-time-s",
                "Do not infer CAM drops before this simulation time [s]",
                cam_silence_bootstrap_time_s);
  cmd.AddValue ("cam-reaction-distance-m",
                "Distance threshold [m] for CAM-triggered evasive reaction",
                cam_reaction_distance_m);
  cmd.AddValue ("cam-reaction-heading-deg",
                "Heading difference threshold [deg] for CAM-triggered evasive reaction",
                cam_reaction_heading_deg);
  cmd.AddValue ("cam-reaction-target-lane",
                "Target lane index for CAM-triggered evasive lane change",
                cam_reaction_target_lane);
  cmd.AddValue ("cam-reaction-speed-factor-target-lane",
                "Speed factor when already in target lane during CAM reaction",
                cam_reaction_speed_factor_target_lane);
  cmd.AddValue ("cam-reaction-speed-factor-other-lane",
                "Speed factor when lane-changing during CAM reaction",
                cam_reaction_speed_factor_other_lane);
  cmd.AddValue ("cam-reaction-action-duration-s",
                "Duration [s] of temporary speed/lane adaptation after CAM trigger",
                cam_reaction_action_duration_s);
  cmd.AddValue ("cpm-reaction-distance-m",
                "Distance threshold [m] to consider CPM objects for evasive control",
                cpm_reaction_distance_m);
  cmd.AddValue ("cpm-reaction-ttc-s",
                "TTC threshold [s] to trigger CPM-based evasive control",
                cpm_reaction_ttc_s);
  cmd.AddValue ("sensor-reaction-enable",
                "Enable local sensor-based evasive reaction from locally detected objects",
                sensor_reaction_enable);
  cmd.AddValue ("sensor-reaction-distance-m",
                "Distance threshold [m] for local sensor-based evasive reaction",
                sensor_reaction_distance_m);
  cmd.AddValue ("sensor-reaction-ttc-s",
                "TTC threshold [s] for local sensor-based evasive reaction",
                sensor_reaction_ttc_s);
  cmd.AddValue ("sensor-reaction-period-ms",
                "Polling period [ms] for local sensor hazard checks",
                sensor_reaction_period_ms);
  cmd.AddValue ("sensor-range-m",
                "Local SUMO sensor range [m]",
                sensor_range_m);
  cmd.AddValue ("sensor-reaction-focus-vehicle-id",
                "Optional vehicle id filter for local sensor reaction (e.g., veh2)",
                sensor_reaction_focus_vehicle_id);
  cmd.AddValue ("reaction-action-cooldown-s",
                "Minimum interval [s] between consecutive evasive control actions",
                reaction_action_cooldown_s);
  cmd.AddValue ("reaction-force-lane-change-enable",
                "Force evasive lane-change requests by disabling autonomous lane-change logic during action",
                reaction_force_lane_change_enable);
  cmd.AddValue ("crash-mode-enable",
                "Enable crash-test mode: repeated drop_decision_no_action forces unsafe speed",
                crash_mode_enable);
  cmd.AddValue ("crash-mode-vehicle-id",
                "Vehicle ID for crash-test mode (empty = all non-emergency vehicles)",
                crash_mode_vehicle_id);
  cmd.AddValue ("crash-mode-no-action-threshold",
                "Consecutive drop_decision_no_action events needed to trigger crash mode",
                crash_mode_no_action_threshold);
  cmd.AddValue ("crash-mode-force-speed-mps",
                "Forced speed [m/s] while crash-test mode is active",
                crash_mode_force_speed_mps);
  cmd.AddValue ("crash-mode-duration-s",
                "Duration [s] of crash-test forced speed mode",
                crash_mode_duration_s);
  cmd.AddValue ("crash-mode-min-time-s",
                "Do not activate crash-test mode before this simulation time [s]",
                crash_mode_min_time_s);
  cmd.AddValue ("v2x-awareness-junction",
                "Vehicles start without junction yield awareness; first received emergency "
                "CAM grants it.  Outcome depends on channel quality, not on thresholds.",
                v2x_awareness_junction_enable);
  cmd.AddValue ("penetrationRate", "Rate of vehicles equipped with wireless communication devices", penetrationRate);
  cmd.AddValue ("send-cam", "Turn on or off CAM dissemination for this scenario", send_cam);
  cmd.AddValue ("send-cpm", "Turn on or off CPM dissemination for this scenario", send_cpm);
  cmd.AddValue ("sionna", "Enable SIONNA usage", sionna);
  cmd.AddValue ("sionna-server-ip", "SIONNA server IP address", server_ip);
  cmd.AddValue ("sionna-local-machine", "SIONNA will be executed on local machine", local_machine);
  cmd.AddValue ("sionna-verbose", "Enable verbose logs in SIONNA helper", sionna_verbose);
  cmd.AddValue ("incident-enable", "Enable stalled incident vehicle injection", incident_enable);
  cmd.AddValue ("incident-vehicle-id", "Vehicle ID to force-stop as incident source", incident_vehicle_id);
  cmd.AddValue ("incident-time-s", "Simulation time [s] when incident stop is injected", incident_time_s);
  cmd.AddValue ("incident-stop-duration-s", "How long to keep incident vehicle stopped [s]", incident_stop_duration_s);
  cmd.AddValue ("incident-recover-max-speed-mps",
                "Recovery max speed [m/s] after incident; negative keeps original speed",
                incident_recover_max_speed_mps);
  cmd.AddValue ("incident-retry-max", "Max retries while waiting for incident vehicle to appear", incident_retry_max);
  cmd.AddValue ("incident-setstop-enable",
                "Use TraCI setStop during incident injection (disable to force in-place hold only)",
                incident_setstop_enable);
  cmd.AddValue ("sumo-collision-action",
                "SUMO collision action override (empty keeps SUMO default)",
                sumo_collision_action);
  cmd.AddValue ("sumo-collision-output",
                "SUMO collision-output XML path (empty disables output)",
                sumo_collision_output);
  cmd.AddValue ("sumo-collision-check-junctions",
                "Enable SUMO collision checks on junctions when collision-action is set",
                sumo_collision_check_junctions);
  cmd.AddValue ("sumo-collision-stoptime-s",
                "SUMO collision.stoptime [s]; negative disables explicit setting",
                sumo_collision_stoptime_s);

  cmd.AddValue ("simTime",
                "Simulation time in seconds",
                simTime);
  cmd.AddValue ("sim-time",
                "Simulation time in seconds",
                simTime);
  cmd.AddValue ("slBearerActivationTime",
                "Sidelik bearer activation time in seconds",
                slBearersActivationTime);
  cmd.AddValue ("centralFrequencyBandSl",
                "The central frequency to be used for Sidelink band/channel",
                centralFrequencyBandSl);
  cmd.AddValue ("bandwidthBandSl",
                "The system bandwidth to be used for Sidelink",
                bandwidthBandSl);
  cmd.AddValue ("txPower",
                "total tx power in dBm",
                txPower);
  cmd.AddValue ("tddPattern",
                "The TDD pattern string",
                tddPattern);
  cmd.AddValue ("slBitMap",
                "The Sidelink bitmap string",
                slBitMap);
  cmd.AddValue ("numerologyBwpSl",
                "The numerology to be used in Sidelink bandwidth part",
                numerologyBwpSl);
  cmd.AddValue ("slSensingWindow",
                "The Sidelink sensing window length in ms",
                slSensingWindow);
  cmd.AddValue ("slSelectionWindow",
                "The parameter which decides the minimum Sidelink selection "
                "window length in physical slots. T2min = slSelectionWindow * 2^numerology",
                slSelectionWindow);
  cmd.AddValue ("slSubchannelSize",
                "The Sidelink subchannel size in RBs",
                slSubchannelSize);
  cmd.AddValue ("slMaxNumPerReserve",
                "The parameter which indicates the maximum number of reserved "
                "PSCCH/PSSCH resources that can be indicated by an SCI.",
                slMaxNumPerReserve);
  cmd.AddValue ("slProbResourceKeep",
                "The parameter which indicates the probability with which the "
                "UE keeps the current resource when the resource reselection"
                "counter reaches zero.",
                slProbResourceKeep);
  cmd.AddValue ("slMaxTxTransNumPssch",
                "The parameter which indicates the maximum transmission number "
                "(including new transmission and retransmission) for PSSCH.",
                slMaxTxTransNumPssch);
  cmd.AddValue ("ReservationPeriod",
                "The resource reservation period in ms",
                reservationPeriod);
  cmd.AddValue ("enableSensing",
                "If true, it enables the sensing based resource selection for "
                "SL, otherwise, no sensing is applied",
                enableSensing);
  cmd.AddValue ("t1",
                "The start of the selection window in physical slots, "
                "accounting for physical layer processing delay",
                t1);
  cmd.AddValue ("t2",
                "The end of the selection window in physical slots",
                t2);
  cmd.AddValue ("slThresPsschRsrp",
                "A threshold in dBm used for sensing based UE autonomous resource selection",
                slThresPsschRsrp);
  cmd.AddValue ("enableChannelRandomness",
                "Enable shadowing and channel updates",
                enableChannelRandomness);
  cmd.AddValue ("channelUpdatePeriod",
                "The channel update period in ms",
                channelUpdatePeriod);
  cmd.AddValue ("mcs",
                "The MCS to used for sidelink",
                mcs);


  // Parse the command line
  cmd.Parse (argc, argv);

  const std::unordered_map<std::string, double> perVehicleEquivalentDbm =
    ParsePerVehicleEquivalentDbm (per_vehicle_prr_profile);

  SionnaHelper& sionnaHelper = SionnaHelper::GetInstance ();
  if (sionna)
    {
      sionnaHelper.SetSionna (sionna);
      sionnaHelper.SetServerIp (server_ip);
      sionnaHelper.SetLocalMachine (local_machine);
      sionnaHelper.SetVerbose (sionna_verbose);
    }

  if (verbose)
    {
      LogComponentEnable ("v2v-nrv2x", LOG_LEVEL_INFO);
      LogComponentEnable ("CABasicService", LOG_LEVEL_INFO);
      LogComponentEnable ("DENBasicService", LOG_LEVEL_INFO);
    }

  /*
   * Check if the frequency is in the allowed range.
   * If you need to add other checks, here is the best position to put them.
   */
  NS_ABORT_IF (centralFrequencyBandSl > 6e9);

  /*
   * Default values for the simulation.
   */
  Config::SetDefault ("ns3::LteRlcUm::MaxTxBufferSize", UintegerValue (999999999));


  /* Use the realtime scheduler of ns3 */
  if(realtime)
      GlobalValue::Bind ("SimulatorImplementationType", StringValue ("ns3::RealtimeSimulatorImpl"));

  /***  Read from the mob_trace the number of vehicles that will be created.
   *       The number of vehicles is directly parsed from the rou.xml file, looking at all
   *       the valid XML elements of type <vehicle>
  ***/
  NS_LOG_INFO("Reading the .rou file...");
  std::string path = sumo_folder + mob_trace;

  /* Load the .rou.xml document */
  xmlInitParser();
  rou_xml_file = xmlParseFile(path.c_str ());
  if (rou_xml_file == NULL)
    {
      NS_FATAL_ERROR("Error: unable to parse the specified XML file: "<<path);
    }
  numberOfNodes = XML_rou_count_vehicles(rou_xml_file);

  xmlFreeDoc(rou_xml_file);
  xmlCleanupParser();

  if(numberOfNodes==-1)
    {
      NS_FATAL_ERROR("Fatal error: cannot gather the number of vehicles from the specified XML file: "<<path<<". Please check if it is a correct SUMO file.");
    }
  NS_LOG_INFO("The .rou file has been read: " << numberOfNodes << " vehicles will be present in the simulation.");
  /*
   * Create a NodeContainer for all the UEs
   */
  NodeContainer allSlUesContainer;
  allSlUesContainer.Create(numberOfNodes);

  /*
   * Assign mobility to the UEs.
   */
  MobilityHelper mobility;
  mobility.Install (allSlUesContainer);

  /*
   * Setup the NR module. We create the various helpers needed for the
   * NR simulation:
   * - EpcHelper, which will setup the core network
   * - NrHelper, which takes care of creating and connecting the various
   * part of the NR stack
   */
  Ptr<NrPointToPointEpcHelper> epcHelper = CreateObject<NrPointToPointEpcHelper> ();
  Ptr<NrHelper> nrHelper = CreateObject<NrHelper> ();

  // Put the pointers inside nrHelper
  nrHelper->SetEpcHelper (epcHelper);

  /*
   * Spectrum division. We create one operational band, containing
   * one component carrier, and a single bandwidth part
   * centered at the frequency specified by the input parameters.
   * We will use the StreetCanyon channel modeling.
   */
  BandwidthPartInfoPtrVector allBwps;
  CcBwpCreator ccBwpCreator;
  const uint8_t numCcPerBand = 1;

  /* Create the configuration for the CcBwpHelper. SimpleOperationBandConf
   * creates a single BWP per CC
   */
  // CcBwpCreator expects hertz, whereas the sidelink BWP field below uses
  // units of 100 kHz. Keep the two representations explicitly consistent.
  const double channelBandwidthHz = static_cast<double> (bandwidthBandSl) * 100000.0;
  CcBwpCreator::SimpleOperationBandConf bandConfSl (centralFrequencyBandSl, channelBandwidthHz, numCcPerBand, BandwidthPartInfo::V2V_Highway);
  std::cout << "NR-SIDELINK-BANDWIDTH,bandwidthBandSl_100kHz=" << bandwidthBandSl
            << ",operation_band_hz=" << channelBandwidthHz << std::endl;
  //CcBwpCreator::SimpleOperationBandConf bandConfSl (centralFrequencyBandSl, bandwidthBandSl, numCcPerBand, BandwidthPartInfo::CV2X_UrbanMicrocell);

  // By using the configuration created, it is time to make the operation bands
  OperationBandInfo bandSl = ccBwpCreator.CreateOperationBandContiguousCc (bandConfSl);

  /*
   * The configured spectrum division is:
   * ------------Band1--------------
   * ------------CC1----------------
   * ------------BwpSl--------------
   */
  if (enableChannelRandomness)
    {
      Config::SetDefault ("ns3::ThreeGppChannelModel::UpdatePeriod", TimeValue (MilliSeconds (channelUpdatePeriod)));
      nrHelper->SetChannelConditionModelAttribute ("UpdatePeriod", TimeValue (MilliSeconds (channelUpdatePeriod)));
      nrHelper->SetPathlossAttribute ("ShadowingEnabled", BooleanValue (true));
    }
  else
    {
      Config::SetDefault ("ns3::ThreeGppChannelModel::UpdatePeriod", TimeValue (MilliSeconds (0)));
      nrHelper->SetChannelConditionModelAttribute ("UpdatePeriod", TimeValue (MilliSeconds (0)));
      nrHelper->SetPathlossAttribute ("ShadowingEnabled", BooleanValue (false));
    }

  /*
   * Initialize channel and pathloss, plus other things inside bandSl. If needed,
   * the band configuration can be done manually, but we leave it for more
   * sophisticated examples. For the moment, this method will take care
   * of all the spectrum initialization needs.
   */
  nrHelper->InitializeOperationBand (&bandSl);
  allBwps = CcBwpCreator::GetAllBwps ({bandSl});


  /*
   * Now, we can setup the attributes. We can have three kind of attributes:
   */

  /*
   * Antennas for all the UEs
   * We are not using beamforming in SL, rather we are using
   * quasi-omnidirectional transmission and reception, which is the default
   * configuration of the beams.
   *
   * Following attribute would be common for all the UEs
   */
  nrHelper->SetUeAntennaAttribute ("NumRows", UintegerValue (1));  //following parameter has no impact at the moment because:
  nrHelper->SetUeAntennaAttribute ("NumColumns", UintegerValue (2));
  nrHelper->SetUeAntennaAttribute ("AntennaElement", PointerValue (CreateObject<IsotropicAntennaModel> ()));

  nrHelper->SetUePhyAttribute ("TxPower", DoubleValue (txPower));

  nrHelper->SetUeMacAttribute ("EnableSensing", BooleanValue (enableSensing));
  nrHelper->SetUeMacAttribute ("T1", UintegerValue (static_cast<uint8_t> (t1)));
  nrHelper->SetUeMacAttribute ("T2", UintegerValue (t2));
  nrHelper->SetUeMacAttribute ("ActivePoolId", UintegerValue (0));
  nrHelper->SetUeMacAttribute ("ReservationPeriod", TimeValue (MilliSeconds (reservationPeriod)));
  nrHelper->SetUeMacAttribute ("NumSidelinkProcess", UintegerValue (4));
  nrHelper->SetUeMacAttribute ("EnableBlindReTx", BooleanValue (true));
  nrHelper->SetUeMacAttribute ("SlThresPsschRsrp", IntegerValue (slThresPsschRsrp));

  uint8_t bwpIdForGbrMcptt = 0;

  nrHelper->SetBwpManagerTypeId (TypeId::LookupByName ("ns3::NrSlBwpManagerUe"));
  nrHelper->SetUeBwpManagerAlgorithmAttribute ("GBR_MC_PUSH_TO_TALK", UintegerValue (bwpIdForGbrMcptt));

  std::set<uint8_t> bwpIdContainer;
  bwpIdContainer.insert (bwpIdForGbrMcptt);

  /*
   * We have configured the attributes we needed. Now, install and get the pointers
   * to the NetDevices, which contains all the NR stack:
   */
  NetDeviceContainer allSlUesNetDeviceContainer = nrHelper->InstallUeDevice (allSlUesContainer, allBwps);

  // When all the configuration is done, explicitly call UpdateConfig ()
  for (auto it = allSlUesNetDeviceContainer.Begin (); it != allSlUesNetDeviceContainer.End (); ++it)
    {
      DynamicCast<NrUeNetDevice> (*it)->UpdateConfig ();
    }

  /*
   * Configure Sidelink. We create the following helpers needed for the
   * NR Sidelink, i.e., V2X simulation:
   * - NrSlHelper, which will configure the UEs protocol stack to be ready to
   *   perform Sidelink related procedures.
   * - EpcHelper, which takes care of triggering the call to EpcUeNas class
   *   to establish the NR Sidelink bearer(s). We note that, at this stage
   *   just communicate the pointer of already instantiated EpcHelper object,
   *   which is the same pointer communicated to the NrHelper above.
   */
  Ptr<NrSlHelper> nrSlHelper = CreateObject <NrSlHelper> ();
  // Put the pointers inside NrSlHelper
  nrSlHelper->SetEpcHelper (epcHelper);

  /*
   * Set the SL error model and AMC
   * Error model type: ns3::NrEesmCcT1, ns3::NrEesmCcT2, ns3::NrEesmIrT1,
   *                   ns3::NrEesmIrT2, ns3::NrLteMiErrorModel
   * AMC type: NrAmc::ShannonModel or NrAmc::ErrorModel
   */
  std::string errorModel = "ns3::NrLteMiErrorModel";
  nrSlHelper->SetSlErrorModel (errorModel);
  nrSlHelper->SetUeSlAmcAttribute ("AmcModel", EnumValue (NrAmc::ErrorModel));

  /*
   * Set the SL scheduler attributes
   * In this example we use NrSlUeMacSchedulerSimple scheduler, which uses
   * fix MCS value
   */
  nrSlHelper->SetNrSlSchedulerTypeId (NrSlUeMacSchedulerSimple::GetTypeId());
  nrSlHelper->SetUeSlSchedulerAttribute ("FixNrSlMcs", BooleanValue (true));
  nrSlHelper->SetUeSlSchedulerAttribute ("InitialNrSlMcs", UintegerValue (mcs));

  /*
   * Very important method to configure UE protocol stack, i.e., it would
   * configure all the SAPs among the layers, setup callbacks, configure
   * error model, configure AMC, and configure ChunkProcessor in Interference
   * API.
   */
  nrSlHelper->PrepareUeForSidelink (allSlUesNetDeviceContainer, bwpIdContainer);


  /*
   * Start preparing for all the sub Structs/RRC Information Element (IEs)
   * of LteRrcSap::SidelinkPreconfigNr. This is the main structure, which would
   * hold all the pre-configuration related to Sidelink.
   */

  //SlResourcePoolNr IE
  LteRrcSap::SlResourcePoolNr slResourcePoolNr;
  //get it from pool factory
  Ptr<NrSlCommPreconfigResourcePoolFactory> ptrFactory = Create<NrSlCommPreconfigResourcePoolFactory> ();
  /*
   * Above pool factory is created to help the users of the simulator to create
   * a pool with valid default configuration. Please have a look at the
   * constructor of NrSlCommPreconfigResourcePoolFactory class.
   *
   * In the following, we show how one could change those default pool parameter
   * values as per the need.
   */
  std::vector <std::bitset<1> > slBitMapVector;
  GetSlBitmapFromString (slBitMap, slBitMapVector);
  NS_ABORT_MSG_IF (slBitMapVector.empty (), "GetSlBitmapFromString failed to generate SL bitmap");
  ptrFactory->SetSlTimeResources (slBitMapVector);
  ptrFactory->SetSlSensingWindow (slSensingWindow); // T0 in ms
  ptrFactory->SetSlSelectionWindow (slSelectionWindow);
  ptrFactory->SetSlFreqResourcePscch (10); // PSCCH RBs
  ptrFactory->SetSlSubchannelSize (slSubchannelSize);
  ptrFactory->SetSlMaxNumPerReserve (slMaxNumPerReserve);
  //Once parameters are configured, we can create the pool
  LteRrcSap::SlResourcePoolNr pool = ptrFactory->CreatePool ();
  slResourcePoolNr = pool;

  //Configure the SlResourcePoolConfigNr IE, which hold a pool and its id
  LteRrcSap::SlResourcePoolConfigNr slresoPoolConfigNr;
  slresoPoolConfigNr.haveSlResourcePoolConfigNr = true;
  //Pool id, ranges from 0 to 15
  uint16_t poolId = 0;
  LteRrcSap::SlResourcePoolIdNr slResourcePoolIdNr;
  slResourcePoolIdNr.id = poolId;
  slresoPoolConfigNr.slResourcePoolId = slResourcePoolIdNr;
  slresoPoolConfigNr.slResourcePool = slResourcePoolNr;

  //Configure the SlBwpPoolConfigCommonNr IE, which hold an array of pools
  LteRrcSap::SlBwpPoolConfigCommonNr slBwpPoolConfigCommonNr;
  //Array for pools, we insert the pool in the array as per its poolId
  slBwpPoolConfigCommonNr.slTxPoolSelectedNormal [slResourcePoolIdNr.id] = slresoPoolConfigNr;

  //Configure the BWP IE
  LteRrcSap::Bwp bwp;
  bwp.numerology = numerologyBwpSl;
  bwp.symbolsPerSlots = 14;
  bwp.rbPerRbg = 1;
  bwp.bandwidth = bandwidthBandSl;

  //Configure the SlBwpGeneric IE
  LteRrcSap::SlBwpGeneric slBwpGeneric;
  slBwpGeneric.bwp = bwp;
  slBwpGeneric.slLengthSymbols = LteRrcSap::GetSlLengthSymbolsEnum (14);
  slBwpGeneric.slStartSymbol = LteRrcSap::GetSlStartSymbolEnum (0);

  //Configure the SlBwpConfigCommonNr IE
  LteRrcSap::SlBwpConfigCommonNr slBwpConfigCommonNr;
  slBwpConfigCommonNr.haveSlBwpGeneric = true;
  slBwpConfigCommonNr.slBwpGeneric = slBwpGeneric;
  slBwpConfigCommonNr.haveSlBwpPoolConfigCommonNr = true;
  slBwpConfigCommonNr.slBwpPoolConfigCommonNr = slBwpPoolConfigCommonNr;

  //Configure the SlFreqConfigCommonNr IE, which hold the array to store
  //the configuration of all Sidelink BWP (s).
  LteRrcSap::SlFreqConfigCommonNr slFreConfigCommonNr;
  //Array for BWPs. Here we will iterate over the BWPs, which
  //we want to use for SL.
  for (const auto &it:bwpIdContainer)
    {
      // it is the BWP id
      slFreConfigCommonNr.slBwpList [it] = slBwpConfigCommonNr;
    }

  //Configure the TddUlDlConfigCommon IE
  LteRrcSap::TddUlDlConfigCommon tddUlDlConfigCommon;
  tddUlDlConfigCommon.tddPattern = tddPattern;

  //Configure the SlPreconfigGeneralNr IE
  LteRrcSap::SlPreconfigGeneralNr slPreconfigGeneralNr;
  slPreconfigGeneralNr.slTddConfig = tddUlDlConfigCommon;

  //Configure the SlUeSelectedConfig IE
  LteRrcSap::SlUeSelectedConfig slUeSelectedPreConfig;
  NS_ABORT_MSG_UNLESS (slProbResourceKeep <= 1.0, "slProbResourceKeep value must be between 0 and 1");
  slUeSelectedPreConfig.slProbResourceKeep = slProbResourceKeep;
  //Configure the SlPsschTxParameters IE
  LteRrcSap::SlPsschTxParameters psschParams;
  psschParams.slMaxTxTransNumPssch = static_cast<uint8_t> (slMaxTxTransNumPssch);
  //Configure the SlPsschTxConfigList IE
  LteRrcSap::SlPsschTxConfigList pscchTxConfigList;
  pscchTxConfigList.slPsschTxParameters [0] = psschParams;
  slUeSelectedPreConfig.slPsschTxConfigList = pscchTxConfigList;

  /*
   * Finally, configure the SidelinkPreconfigNr. This is the main structure
   * that needs to be communicated to NrSlUeRrc class
   */
  LteRrcSap::SidelinkPreconfigNr slPreConfigNr;
  slPreConfigNr.slPreconfigGeneral = slPreconfigGeneralNr;
  slPreConfigNr.slUeSelectedPreConfig = slUeSelectedPreConfig;
  slPreConfigNr.slPreconfigFreqInfoList [0] = slFreConfigCommonNr;

  //Communicate the above pre-configuration to the NrSlHelper
  nrSlHelper->InstallNrSlPreConfiguration (allSlUesNetDeviceContainer, slPreConfigNr);
  Ptr<NrUeNetDevice> bandwidthAuditDevice = DynamicCast<NrUeNetDevice> (allSlUesNetDeviceContainer.Get (0));
  Ptr<NrUePhy> bandwidthAuditPhy = bandwidthAuditDevice->GetPhy (0);
  if (std::abs (bandSl.m_channelBandwidth - channelBandwidthHz) > 0.5 ||
      std::abs (static_cast<double> (bandwidthAuditPhy->GetChannelBandwidth ()) - channelBandwidthHz) > 0.5 ||
      (bandwidthBandSl == 400 && numerologyBwpSl == 2 && bandwidthAuditPhy->GetRbNum () != 53))
    {
      NS_FATAL_ERROR ("Inconsistent NR sidelink operation band, PHY bandwidth, or resource blocks");
    }
  std::cout << "NR-SIDELINK-PHY,channel_bandwidth_hz=" << bandwidthAuditPhy->GetChannelBandwidth ()
            << ",resource_blocks=" << bandwidthAuditPhy->GetRbNum () << std::endl;

  /****************************** End SL Configuration ***********************/

  /*
   * Fix the random streams
   */
  int64_t stream = 1;
  stream += nrHelper->AssignStreams (allSlUesNetDeviceContainer, stream);
  stream += nrSlHelper->AssignStreams (allSlUesNetDeviceContainer, stream);

  /*
   * if enableOneTxPerLane is true:
   *
   * Divide the UEs in transmitting UEs and receiving UEs. Each lane can
   * have only odd number of UEs, and on each lane middle UE would
   * be the transmitter.
   *
   * else:
   *
   * All the UEs can transmit and receive
   */
  NodeContainer txSlUes;
  NodeContainer rxSlUes;
  NetDeviceContainer txSlUesNetDevice;
  NetDeviceContainer rxSlUesNetDevice;
  txSlUes.Add (allSlUesContainer);
  rxSlUes.Add (allSlUesContainer);
  txSlUesNetDevice.Add (allSlUesNetDeviceContainer);
  rxSlUesNetDevice.Add (allSlUesNetDeviceContainer);

  /*
   * Configure the IP stack, and activate NR Sidelink bearer (s) as per the
   * configured time.
   *
   * This example supports IPV4 and IPV6
   */

  InternetStackHelper internet;
  internet.Install (allSlUesContainer);
  uint32_t dstL2Id = 255;
  Ipv4Address groupAddress4 ("225.0.0.0");     //use multicast address as destination

  Address remoteAddress;
  Address localAddress;
  uint16_t port = 8000;
  Ptr<LteSlTft> tft;

  Ipv4InterfaceContainer ueIpIface;
  ueIpIface = epcHelper->AssignUeIpv4Address (allSlUesNetDeviceContainer);

  // set the default gateway for the UE
  Ipv4StaticRoutingHelper ipv4RoutingHelper;
  for (uint32_t u = 0; u < allSlUesContainer.GetN (); ++u)
    {
      Ptr<Node> ueNode = allSlUesContainer.Get (u);
      // Set the default gateway for the UE
      Ptr<Ipv4StaticRouting> ueStaticRouting = ipv4RoutingHelper.GetStaticRouting (ueNode->GetObject<Ipv4> ());
      ueStaticRouting->SetDefaultRoute (epcHelper->GetUeDefaultGatewayAddress (), 1);
    }
  remoteAddress = InetSocketAddress (groupAddress4, port);
  localAddress = InetSocketAddress (Ipv4Address::GetAny (), port);

  tft = Create<LteSlTft> (LteSlTft::Direction::TRANSMIT, LteSlTft::CommType::GroupCast, groupAddress4, dstL2Id);
  //Set Sidelink bearers
  nrSlHelper->ActivateNrSlBearer (slBearersActivationTime, allSlUesNetDeviceContainer, tft);

  tft = Create<LteSlTft> (LteSlTft::Direction::RECEIVE, LteSlTft::CommType::GroupCast, groupAddress4, dstL2Id);
  //Set Sidelink bearers
  nrSlHelper->ActivateNrSlBearer (slBearersActivationTime, allSlUesNetDeviceContainer, tft);

  // enable log component
  //LogComponentEnable("NrUeMac", LOG_LEVEL_INFO);

  /*** 6. Setup Traci and start SUMO ***/
  Ptr<TraciClient> sumoClient = CreateObject<TraciClient> ();
  if (sionna)
    {
      sumoClient->SetSionnaUp ();
    }
  sumoClient->SetAttribute ("SumoConfigPath", StringValue (sumo_config));
  sumoClient->SetAttribute ("SumoBinaryPath", StringValue (""));    // use system installation of sumo
  sumoClient->SetAttribute ("SynchInterval", TimeValue (Seconds (sumo_updates)));
  sumoClient->SetAttribute ("StartTime", TimeValue (Seconds (0.0)));
  sumoClient->SetAttribute ("SumoGUI", BooleanValue (sumo_gui));
  sumoClient->SetAttribute ("SumoPort", UintegerValue (sumo_port));
  sumoClient->SetAttribute ("PenetrationRate", DoubleValue (penetrationRate));
  sumoClient->SetAttribute ("SumoLogFile", BooleanValue (false));
  sumoClient->SetAttribute ("SumoStepLog", BooleanValue (false));
  sumoClient->SetAttribute ("SumoSeed", IntegerValue (sumo_seed));

  std::string sumo_additional_options = "--verbose false";

  if(sumo_netstate_file_name!="")
  {
    sumo_additional_options += " --netstate-dump " + sumo_netstate_file_name;
  }
  if (!sumo_collision_action.empty ())
    {
      sumo_additional_options += " --collision.action " + sumo_collision_action;
      sumo_additional_options += std::string (" --collision.check-junctions ")
                                 + (sumo_collision_check_junctions ? "true" : "false");
    }
  if (!sumo_collision_output.empty ())
    {
      sumo_additional_options += " --collision-output " + sumo_collision_output;
    }
  if (sumo_collision_stoptime_s >= 0.0)
    {
      sumo_additional_options += " --collision.stoptime " + std::to_string (sumo_collision_stoptime_s);
    }

  sumoClient->SetAttribute ("SumoAdditionalCmdOptions", StringValue (sumo_additional_options));
  // 1s is occasionally too short on loaded hosts/WSL and causes transient
  // TraCI "Connection refused" right after SUMO startup.
  sumoClient->SetAttribute ("SumoWaitForSocket", TimeValue (Seconds (5.0)));

  /* Create and setup the web-based vehicle visualizer of ms-van3t */
  vehicleVisualizer vehicleVisObj;
  Ptr<vehicleVisualizer> vehicleVis = &vehicleVisObj;
  if (vehicle_vis)
  {
      vehicleVis->startServer();
      vehicleVis->connectToServer ();
      sumoClient->SetAttribute ("VehicleVisualizer", PointerValue (vehicleVis));
  }

  Ptr<MetricSupervisor> metSup = NULL;
  MetricSupervisor prrSupObj(m_baseline_prr);
  if(m_metric_sup)
    {
      metSup = &prrSupObj;
      metSup->setTraCIClient(sumoClient);
    }

  std::string officialSqlitePath;
  std::unique_ptr<SQLiteOutput> officialDb;
  std::unique_ptr<UeMacPscchTxOutputStats> officialPscchTxStats;
  std::unique_ptr<UeMacPsschTxOutputStats> officialPsschTxStats;
  std::unique_ptr<UePhyPscchRxOutputStats> officialPscchRxStats;
  std::unique_ptr<UePhyPsschRxOutputStats> officialPsschRxStats;
  std::unique_ptr<UeRlcRxOutputStats> officialRlcRxStats;
  if (enable_official_sqlite)
    {
      officialSqlitePath =
        NormalizeOutputDir (outputDir) + simTag + "-v2v-emergencyVehicleAlert-nrv2x.db";
      officialDb = std::make_unique<SQLiteOutput> (officialSqlitePath);

      officialPscchTxStats = std::make_unique<UeMacPscchTxOutputStats> ();
      officialPscchTxStats->SetDb (officialDb.get (), "pscchTxUeMac");
      Config::ConnectWithoutContext (
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/ComponentCarrierMapUe/*/NrUeMac/SlPscchScheduling",
        MakeBoundCallback (&NotifySlPscchScheduling, officialPscchTxStats.get ()));

      officialPsschTxStats = std::make_unique<UeMacPsschTxOutputStats> ();
      officialPsschTxStats->SetDb (officialDb.get (), "psschTxUeMac");
      Config::ConnectWithoutContext (
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/ComponentCarrierMapUe/*/NrUeMac/SlPsschScheduling",
        MakeBoundCallback (&NotifySlPsschScheduling, officialPsschTxStats.get ()));

      officialPscchRxStats = std::make_unique<UePhyPscchRxOutputStats> ();
      officialPscchRxStats->SetDb (officialDb.get (), "pscchRxUePhy");
      Config::ConnectWithoutContext (
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/ComponentCarrierMapUe/*/NrUePhy/NrSpectrumPhyList/*/RxPscchTraceUe",
        MakeBoundCallback (&NotifySlPscchRx, officialPscchRxStats.get ()));

      officialPsschRxStats = std::make_unique<UePhyPsschRxOutputStats> ();
      officialPsschRxStats->SetDb (officialDb.get (), "psschRxUePhy");
      Config::ConnectWithoutContext (
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/ComponentCarrierMapUe/*/NrUePhy/NrSpectrumPhyList/*/RxPsschTraceUe",
        MakeBoundCallback (&NotifySlPsschRx, officialPsschRxStats.get ()));

      officialRlcRxStats = std::make_unique<UeRlcRxOutputStats> ();
      officialRlcRxStats->SetDb (officialDb.get (), "rlcRx");
      Config::ConnectWithoutContext (
        "/NodeList/*/DeviceList/*/$ns3::NrUeNetDevice/ComponentCarrierMapUe/*/NrUeMac/RxRlcPduWithTxRnti",
        MakeBoundCallback (&NotifySlRlcPduRx, officialRlcRxStats.get ()));

      std::cout << "OFFICIAL-5GLENA-SQLITE-ENABLED,path=" << officialSqlitePath << std::endl;
    }

  /*** 7. Setup interface and application for dynamic nodes ***/
  emergencyVehicleAlertHelper EmergencyVehicleAlertHelper;
  EmergencyVehicleAlertHelper.SetAttribute ("Client", PointerValue (sumoClient));
  EmergencyVehicleAlertHelper.SetAttribute ("RealTime", BooleanValue(realtime));
  EmergencyVehicleAlertHelper.SetAttribute ("PrintSummary", BooleanValue (true));
  EmergencyVehicleAlertHelper.SetAttribute ("CSV", StringValue(csv_name));
  EmergencyVehicleAlertHelper.SetAttribute ("Model", StringValue ("nrv2x"));
  EmergencyVehicleAlertHelper.SetAttribute ("MetricSupervisor", PointerValue (metSup));
  EmergencyVehicleAlertHelper.SetAttribute ("SendCAM", BooleanValue (send_cam));
  EmergencyVehicleAlertHelper.SetAttribute ("SendCPM", BooleanValue (send_cpm));
  EmergencyVehicleAlertHelper.SetAttribute ("RxDropProbCam", DoubleValue (rx_drop_prob_cam));
  EmergencyVehicleAlertHelper.SetAttribute ("RxDropProbCpm", DoubleValue (rx_drop_prob_cpm));
  EmergencyVehicleAlertHelper.SetAttribute ("RxDropProbPhyCam", DoubleValue (rx_drop_prob_phy_cam));
  EmergencyVehicleAlertHelper.SetAttribute ("RxDropProbPhyCpm", DoubleValue (rx_drop_prob_phy_cpm));
  EmergencyVehicleAlertHelper.SetAttribute ("DropTriggeredReactionEnable", BooleanValue (drop_triggered_reaction_enable));
  EmergencyVehicleAlertHelper.SetAttribute ("TargetLossProfileEnable", BooleanValue (target_loss_profile_enable));
  EmergencyVehicleAlertHelper.SetAttribute ("TargetLossVehicleId", StringValue (target_loss_vehicle_id));
  EmergencyVehicleAlertHelper.SetAttribute ("TargetLossRxDropProbCam", DoubleValue (target_loss_rx_drop_prob_cam));
  EmergencyVehicleAlertHelper.SetAttribute ("TargetLossRxDropProbCpm", DoubleValue (target_loss_rx_drop_prob_cpm));
  EmergencyVehicleAlertHelper.SetAttribute ("TargetLossRxDropProbPhyCam", DoubleValue (target_loss_rx_drop_prob_phy_cam));
  EmergencyVehicleAlertHelper.SetAttribute ("TargetLossRxDropProbPhyCpm", DoubleValue (target_loss_rx_drop_prob_phy_cpm));
  EmergencyVehicleAlertHelper.SetAttribute ("PerVehiclePrrProfile", StringValue (per_vehicle_prr_profile));
  EmergencyVehicleAlertHelper.SetAttribute ("CamSilenceDropInferenceEnable", BooleanValue (cam_silence_drop_inference_enable));
  EmergencyVehicleAlertHelper.SetAttribute ("CamSilenceFocusTxId", IntegerValue (cam_silence_focus_tx_id));
  EmergencyVehicleAlertHelper.SetAttribute ("CamSilenceExpectedPeriodMs", DoubleValue (cam_silence_expected_period_ms));
  EmergencyVehicleAlertHelper.SetAttribute ("CamSilenceTimeoutS", DoubleValue (cam_silence_timeout_s));
  EmergencyVehicleAlertHelper.SetAttribute ("CamSilenceBootstrapTimeS", DoubleValue (cam_silence_bootstrap_time_s));
  EmergencyVehicleAlertHelper.SetAttribute ("ReactionDistanceThreshold", DoubleValue (cam_reaction_distance_m));
  EmergencyVehicleAlertHelper.SetAttribute ("ReactionHeadingThreshold", DoubleValue (cam_reaction_heading_deg));
  EmergencyVehicleAlertHelper.SetAttribute ("ReactionTargetLane", IntegerValue (cam_reaction_target_lane));
  EmergencyVehicleAlertHelper.SetAttribute ("ReactionSpeedFactorTargetLane", DoubleValue (cam_reaction_speed_factor_target_lane));
  EmergencyVehicleAlertHelper.SetAttribute ("ReactionSpeedFactorOtherLane", DoubleValue (cam_reaction_speed_factor_other_lane));
  EmergencyVehicleAlertHelper.SetAttribute ("ReactionActionDurationS", DoubleValue (cam_reaction_action_duration_s));
  EmergencyVehicleAlertHelper.SetAttribute ("CpmReactionDistanceThreshold", DoubleValue (cpm_reaction_distance_m));
  EmergencyVehicleAlertHelper.SetAttribute ("CpmReactionTtcThresholdS", DoubleValue (cpm_reaction_ttc_s));
  EmergencyVehicleAlertHelper.SetAttribute ("SensorReactionEnable", BooleanValue (sensor_reaction_enable));
  EmergencyVehicleAlertHelper.SetAttribute ("SensorReactionDistanceThresholdM", DoubleValue (sensor_reaction_distance_m));
  EmergencyVehicleAlertHelper.SetAttribute ("SensorReactionTtcThresholdS", DoubleValue (sensor_reaction_ttc_s));
  EmergencyVehicleAlertHelper.SetAttribute ("SensorReactionPeriodMs", DoubleValue (sensor_reaction_period_ms));
  EmergencyVehicleAlertHelper.SetAttribute ("SensorRangeM", DoubleValue (sensor_range_m));
  EmergencyVehicleAlertHelper.SetAttribute ("SensorReactionFocusVehicleId", StringValue (sensor_reaction_focus_vehicle_id));
  EmergencyVehicleAlertHelper.SetAttribute ("ReactionActionCooldownS", DoubleValue (reaction_action_cooldown_s));
  EmergencyVehicleAlertHelper.SetAttribute ("ReactionForceLaneChangeEnable", BooleanValue (reaction_force_lane_change_enable));
  EmergencyVehicleAlertHelper.SetAttribute ("CrashModeEnable", BooleanValue (crash_mode_enable));
  EmergencyVehicleAlertHelper.SetAttribute ("CrashModeVehicleId", StringValue (crash_mode_vehicle_id));
  EmergencyVehicleAlertHelper.SetAttribute ("CrashModeNoActionThreshold", UintegerValue (crash_mode_no_action_threshold));
  EmergencyVehicleAlertHelper.SetAttribute ("CrashModeForceSpeedMps", DoubleValue (crash_mode_force_speed_mps));
  EmergencyVehicleAlertHelper.SetAttribute ("CrashModeDurationS", DoubleValue (crash_mode_duration_s));
  EmergencyVehicleAlertHelper.SetAttribute ("CrashModeMinTimeS", DoubleValue (crash_mode_min_time_s));
  EmergencyVehicleAlertHelper.SetAttribute ("V2xAwarenessJunctionEnable", BooleanValue (v2x_awareness_junction_enable));

  /* callback function for node creation */
  int i=0;
  STARTUP_FCN setupNewWifiNode = [&] (std::string vehicleID,TraciClient::StationTypeTraCI_t stationType) -> Ptr<Node>
    {
      (void) stationType;
      if (nodeCounter >= allSlUesContainer.GetN())
        NS_FATAL_ERROR("Node Pool empty!: " << nodeCounter << " nodes created.");

      Ptr<Node> includedNode = allSlUesContainer.Get(nodeCounter);
      ++nodeCounter; // increment counter for next node

      auto eqIt = perVehicleEquivalentDbm.find (vehicleID);
      if (eqIt != perVehicleEquivalentDbm.end ())
        {
          Ptr<NrUeNetDevice> nrUeDev = nullptr;
          for (uint32_t devIdx = 0; devIdx < includedNode->GetNDevices (); ++devIdx)
            {
              nrUeDev = DynamicCast<NrUeNetDevice> (includedNode->GetDevice (devIdx));
              if (nrUeDev != nullptr)
                {
                  break;
                }
            }

          if (nrUeDev != nullptr)
            {
              Ptr<NrUePhy> uePhy = nrUeDev->GetPhy (0);
              if (uePhy != nullptr)
                {
                  const double baseNoiseFigureDb = uePhy->GetNoiseFigure ();
                  const double noisePenaltyDb = txPower - eqIt->second;
                  const double newNoiseFigureDb = std::max (0.0, baseNoiseFigureDb + noisePenaltyDb);
                  uePhy->SetNoiseFigure (newNoiseFigureDb);
                  std::cout << "PER-VEHICLE-EQUIV-DBM-APPLIED,id=" << vehicleID
                            << ",equiv_tx_power_dbm=" << eqIt->second
                            << ",base_noise_figure_db=" << baseNoiseFigureDb
                            << ",new_noise_figure_db=" << newNoiseFigureDb
                            << std::endl;
                }
            }
        }

      /* Install Application */
      EmergencyVehicleAlertHelper.SetAttribute ("IpAddr", Ipv4AddressValue(groupAddress4));
      i++;

      //ApplicationContainer CAMSenderApp = CamSenderHelper.Install (includedNode);
      ApplicationContainer AppSample = EmergencyVehicleAlertHelper.Install (includedNode);

      AppSample.Start (Seconds (0.0));
      AppSample.Stop (Seconds(simTime) - Simulator::Now () - Seconds (0.1));

      return includedNode;
    };

  /* callback function for node shutdown */
  SHUTDOWN_FCN shutdownWifiNode = [] (Ptr<Node> exNode,std::string vehicleID)
    {
      /* stop all applications */
      Ptr<emergencyVehicleAlert> appSample_ = exNode->GetApplication(0)->GetObject<emergencyVehicleAlert>();

      if(appSample_)
        appSample_->StopApplicationNow();

       /* set position outside communication range */
      Ptr<ConstantPositionMobilityModel> mob = exNode->GetObject<ConstantPositionMobilityModel>();
      mob->SetPosition(Vector(-1000.0+(rand()%25),320.0+(rand()%25),250.0)); // rand() for visualization purposes

      /* NOTE: further actions could be required for a safe shut down! */
    };

  /* start traci client with given function pointers */
  sumoClient->SumoSetup (setupNewWifiNode, shutdownWifiNode);

  if (incident_enable)
    {
      auto incidentAttempts = std::make_shared<uint32_t> (0);
      auto originalMaxSpeed = std::make_shared<double> (-1.0);
      auto originalSpeedMode = std::make_shared<int> (31);
      std::function<void ()> applyIncident;
      applyIncident = [&, incidentAttempts, originalMaxSpeed, originalSpeedMode] () mutable
        {
          ++(*incidentAttempts);
          std::vector<std::string> activeVehicles = sumoClient->TraCIAPI::vehicle.getIDList ();
          bool found = std::find (activeVehicles.begin (), activeVehicles.end (), incident_vehicle_id) != activeVehicles.end ();
          if (!found)
            {
              if (*incidentAttempts <= incident_retry_max)
                {
                  NS_LOG_WARN ("Incident vehicle '" << incident_vehicle_id
                               << "' not present yet; retry " << *incidentAttempts << "/" << incident_retry_max);
                  Simulator::Schedule (Seconds (1.0), applyIncident);
                }
              else
                {
                  NS_LOG_ERROR ("Incident injection failed: vehicle '" << incident_vehicle_id << "' never appeared");
                }
              return;
            }

          std::string roadId = sumoClient->TraCIAPI::vehicle.getRoadID (incident_vehicle_id);
          int laneIdx = sumoClient->TraCIAPI::vehicle.getLaneIndex (incident_vehicle_id);
          double lanePos = sumoClient->TraCIAPI::vehicle.getLanePosition (incident_vehicle_id);
          *originalMaxSpeed = sumoClient->TraCIAPI::vehicle.getMaxSpeed (incident_vehicle_id);
          double speedNow = 0.0;
          try
            {
              speedNow = std::max (0.0, sumoClient->TraCIAPI::vehicle.getSpeed (incident_vehicle_id));
              *originalSpeedMode = sumoClient->TraCIAPI::vehicle.getSpeedMode (incident_vehicle_id);
            }
          catch (const std::exception &)
            {
              speedNow = 0.0;
              *originalSpeedMode = 31;
            }

          // Force immediate low-speed hold first (works even when setStop is rejected by SUMO).
          sumoClient->TraCIAPI::vehicle.setSpeedMode (incident_vehicle_id, 0);
          sumoClient->TraCIAPI::vehicle.setMaxSpeed (incident_vehicle_id, 0.1);
          sumoClient->TraCIAPI::vehicle.setSpeed (incident_vehicle_id, 0.0);
          sumoClient->TraCIAPI::vehicle.slowDown (incident_vehicle_id, 0.0, 1.0);

          auto holdStopped = std::make_shared<std::function<void (double)>> ();
          *holdStopped = [&, holdStopped] (double remainingS)
            {
              if (remainingS <= 0.0)
                {
                  return;
                }
              try
                {
                  sumoClient->TraCIAPI::vehicle.setMaxSpeed (incident_vehicle_id, 0.1);
                  sumoClient->TraCIAPI::vehicle.setSpeed (incident_vehicle_id, 0.0);
                  sumoClient->TraCIAPI::vehicle.slowDown (incident_vehicle_id, 0.0, 0.2);
                }
              catch (const std::exception &)
                {
                  return;
                }
              double next = std::min (0.5, remainingS);
              Simulator::Schedule (Seconds (next), [holdStopped, remainingS, next] () mutable {
                (*holdStopped) (remainingS - next);
              });
            };

          bool stopApplied = false;
          std::string stopEdgeUsed = roadId;
          int stopLaneUsed = std::max (0, laneIdx);
          double stopPosUsed = lanePos;
          if (incident_setstop_enable && !roadId.empty () && roadId[0] != ':')
            {
              auto clampStopPos = [&] (const std::string& laneId, double rawPos)
                {
                  double stopPos = rawPos;
                  if (!std::isfinite (stopPos) || stopPos < 1.0)
                    {
                      stopPos = 1.0;
                    }
                  try
                    {
                      double laneLength = sumoClient->TraCIAPI::lane.getLength (laneId);
                      if (std::isfinite (laneLength) && laneLength > 1.0)
                        {
                          stopPos = std::min (stopPos, laneLength - 1.0);
                        }
                    }
                  catch (const std::exception &)
                    {
                      // Keep raw stopPos if lane length is unavailable.
                    }
                  return stopPos;
                };

              auto calcRequiredGap = [&] (double speedMps)
                {
                  // Conservative braking buffer to avoid SUMO "too close to brake" errors.
                  const double comfortableDecel = 3.0;
                  double brakingDist = (speedMps * speedMps) / (2.0 * comfortableDecel);
                  return std::max (35.0, brakingDist + 15.0);
                };

              auto trySetStop = [&] (const std::string& edgeId, int lane, double pos) -> bool
                {
                  if (edgeId.empty () || edgeId[0] == ':')
                    {
                      return false;
                    }
                  int stopLane = std::max (0, lane);
                  std::string laneId = edgeId + "_" + std::to_string (stopLane);
                  double stopPos = clampStopPos (laneId, pos);
                  if (!std::isfinite (stopPos) || stopPos < 1.0)
                    {
                      stopPos = 1.0;
                    }
                  sumoClient->TraCIAPI::vehicle.setStop (incident_vehicle_id,
                                                         edgeId,
                                                         stopPos,
                                                         stopLane,
                                                         incident_stop_duration_s,
                                                         0,
                                                         0.0,
                                                         -1.0);
                  stopEdgeUsed = edgeId;
                  stopLaneUsed = stopLane;
                  stopPosUsed = stopPos;
                  return true;
                };

              // First attempt: stop on current edge only if there is enough distance to brake.
              double requiredGap = calcRequiredGap (speedNow);
              bool canTryCurrentEdge = false;
              std::string laneIdCurrent = roadId + "_" + std::to_string (std::max (0, laneIdx));
              try
                {
                  double laneLength = sumoClient->TraCIAPI::lane.getLength (laneIdCurrent);
                  if (std::isfinite (laneLength) && std::isfinite (lanePos))
                    {
                      double remainingDist = laneLength - lanePos;
                      canTryCurrentEdge = remainingDist > (requiredGap + 5.0);
                    }
                }
              catch (const std::exception &)
                {
                  // If lane geometry is unavailable, skip current-edge stop and use next-edge fallback.
                }

              if (canTryCurrentEdge)
                {
                  double stopPosCurrent = lanePos + requiredGap;
                  try
                    {
                      stopApplied = trySetStop (roadId, laneIdx, stopPosCurrent);
                    }
                  catch (const std::exception &eCurrent)
                    {
                      NS_LOG_WARN ("setStop on current edge failed for incident vehicle '" << incident_vehicle_id
                                   << "': " << eCurrent.what ());
                    }
                }
              else
                {
                  NS_LOG_INFO ("Skipping current-edge setStop for incident vehicle '" << incident_vehicle_id
                               << "' because braking distance is too short.");
                }

              // Fallback: stop on next route edge to avoid \"too close to brake\" near junction.
              if (!stopApplied)
                {
                  try
                    {
                      std::vector<std::string> route = sumoClient->TraCIAPI::vehicle.getRoute (incident_vehicle_id);
                      int routeIndex = sumoClient->TraCIAPI::vehicle.getRouteIndex (incident_vehicle_id);
                      if (routeIndex >= 0 && static_cast<size_t> (routeIndex + 1) < route.size ())
                        {
                          std::string nextEdge = route.at (routeIndex + 1);
                          double nextEdgeStopPos = std::max (40.0, requiredGap * 0.75);
                          stopApplied = trySetStop (nextEdge, 0, nextEdgeStopPos);
                        }
                    }
                  catch (const std::exception &eNext)
                    {
                      NS_LOG_WARN ("setStop on next edge failed for incident vehicle '" << incident_vehicle_id
                                   << "': " << eNext.what ());
                    }
                }

              if (!stopApplied)
                {
                  NS_LOG_WARN ("setStop failed for incident vehicle '" << incident_vehicle_id
                               << "'. Falling back to low max-speed hold only.");
                }
            }
          else if (!incident_setstop_enable)
            {
              NS_LOG_INFO ("setStop disabled for incident vehicle '" << incident_vehicle_id
                           << "'. Keeping in-place low-speed hold only.");
            }
          (*holdStopped) (incident_stop_duration_s);

          libsumo::TraCIColor incidentColor;
          incidentColor.r = 230;
          incidentColor.g = 30;
          incidentColor.b = 30;
          incidentColor.a = 255;
          sumoClient->TraCIAPI::vehicle.setColor (incident_vehicle_id, incidentColor);

          std::cout << "INCIDENT-APPLIED,id=" << incident_vehicle_id
                    << ",time_s=" << Simulator::Now ().GetSeconds ()
                    << ",road=" << roadId
                    << ",lane=" << laneIdx
                    << ",lane_pos_m=" << lanePos
                    << ",stop_edge=" << stopEdgeUsed
                    << ",stop_lane=" << stopLaneUsed
                    << ",stop_pos_m=" << stopPosUsed
                    << ",duration_s=" << incident_stop_duration_s
                    << ",setStopApplied=" << (stopApplied ? 1 : 0)
                    << std::endl;

          if (incident_stop_duration_s > 0.0)
            {
              Simulator::Schedule (Seconds (incident_stop_duration_s),
                                   [&, originalMaxSpeed, originalSpeedMode] ()
                                   {
                                     double recoverSpeed = incident_recover_max_speed_mps;
                                     if (recoverSpeed < 0.0)
                                       {
                                         recoverSpeed = *originalMaxSpeed;
                                       }
                                     try
                                       {
                                         sumoClient->TraCIAPI::vehicle.setSpeedMode (incident_vehicle_id, *originalSpeedMode);
                                       }
                                     catch (const std::exception &e)
                                       {
                                         NS_LOG_WARN ("Incident speed-mode restore skipped for '" << incident_vehicle_id
                                                      << "': " << e.what ());
                                       }
                                     if (recoverSpeed > 0.0)
                                       {
                                         try
                                           {
                                             sumoClient->TraCIAPI::vehicle.setMaxSpeed (incident_vehicle_id, recoverSpeed);
                                             sumoClient->TraCIAPI::vehicle.setSpeed (incident_vehicle_id, -1.0);
                                             sumoClient->TraCIAPI::vehicle.slowDown (incident_vehicle_id, recoverSpeed, 2.0);
                                             std::cout << "INCIDENT-RELEASED,id=" << incident_vehicle_id
                                                       << ",time_s=" << Simulator::Now ().GetSeconds ()
                                                       << ",recover_max_speed_mps=" << recoverSpeed
                                                       << std::endl;
                                           }
                                         catch (const std::exception &e)
                                           {
                                             NS_LOG_WARN ("Incident release skipped for '" << incident_vehicle_id
                                                          << "': " << e.what ());
                                           }
                                       }
                                   });
            }
        };
      if (incident_time_s >= simTime)
        {
          NS_LOG_WARN ("incident-time-s >= simTime, incident will not trigger: "
                       << incident_time_s << " >= " << simTime);
        }
      else
        {
          Simulator::Schedule (Seconds (incident_time_s), applyIncident);
        }
    }

  /*** 8. Start Simulation ***/
  Simulator::Stop (Seconds(simTime));

  Simulator::Run ();

  // End the TraCI session while the client is still alive.  SumoStop() is
  // idempotent, so TraciClient's destructor will not send CMD_CLOSE again.
  sumoClient->SumoStop ();

  if (enable_official_sqlite)
    {
      officialPscchTxStats->EmptyCache ();
      officialPsschTxStats->EmptyCache ();
      officialPscchRxStats->EmptyCache ();
      officialPsschRxStats->EmptyCache ();
      officialRlcRxStats->EmptyCache ();
    }

  Simulator::Destroy ();

  if(m_metric_sup)
    {
      if(csv_name_cumulative!="")
      {
        std::ofstream csv_cum_ofstream;
        std::string full_csv_name = csv_name_cumulative + ".csv";

        if(access(full_csv_name.c_str(),F_OK)!=-1)
        {
          // The file already exists
          csv_cum_ofstream.open(full_csv_name,std::ofstream::out | std::ofstream::app);
        }
        else
        {
          // The file does not exist yet
          csv_cum_ofstream.open(full_csv_name);
          csv_cum_ofstream << "current_txpower_dBm,avg_PRR,avg_latency_ms" << std::endl;
        }

        csv_cum_ofstream << txPower << "," << metSup->getAveragePRR_overall () << "," << metSup->getAverageLatency_overall () << std::endl;
      }
      std::cout << "Average PRR: " << metSup->getAveragePRR_overall () << std::endl;
      std::cout << "Average latency (ms): " << metSup->getAverageLatency_overall () << std::endl;
    }

  if (enable_official_sqlite)
    {
      std::cout << "OFFICIAL-5GLENA-SQLITE-PATH: " << officialSqlitePath << std::endl;
    }


  return 0;
}
