#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DATE_TAG="$(date +%F)"
MODE="${MODE:-all}"

if [[ $# -gt 0 ]]; then
  MODE="$1"
fi

OUT_ROOT="${OUT_ROOT:-$HOME/NEWWAY_runs/$DATE_TAG/intersection_radar_comm}"
SUMO_GUI="${SUMO_GUI:-1}"
SIM_TIME="${SIM_TIME:-20}"
SUMO_UPDATES="${SUMO_UPDATES:-0.05}"
PLOT="${PLOT:-0}"
EXPORT_RESULTS="${EXPORT_RESULTS:-0}"
EVENT_TIMELINE="${EVENT_TIMELINE:-0}"
ENABLE_COLLISION_OUTPUT="${ENABLE_COLLISION_OUTPUT:-1}"
COLLISION_ACTION="${COLLISION_ACTION:-warn}"
COLLISION_STOPTIME_S="${COLLISION_STOPTIME_S:-1000}"
COLLISION_CAUSALITY="${COLLISION_CAUSALITY:-0}"
TX_POWER_DBM="${TX_POWER_DBM:-23}"
RNG_RUN="${RNG_RUN:-17}"

USE_SIONNA="${USE_SIONNA:-1}"
SIONNA_LOCAL_MACHINE="${SIONNA_LOCAL_MACHINE:-1}"
SIONNA_SERVER_IP="${SIONNA_SERVER_IP:-127.0.0.1}"
SIONNA_VERBOSE="${SIONNA_VERBOSE:-0}"
SIONNA_PORT="${SIONNA_PORT:-8103}"
CHECK_SIONNA_LISTENER="${CHECK_SIONNA_LISTENER:-1}"
WAIT_FOR_SIONNA_SEC="${WAIT_FOR_SIONNA_SEC:-120}"
SUMO_CONFIG="${SUMO_CONFIG:-$ROOT/experiments/intersection_radar_comm/sumo/map_intersection_radar_link.sumo.cfg}"

VEH2_EQ_DBM="${VEH2_EQ_DBM:-23}"
VEH3_GOOD_EQ_DBM="${VEH3_GOOD_EQ_DBM:-23}"
VEH3_BAD_EQ_DBM="${VEH3_BAD_EQ_DBM:--30}"

SENSOR_RANGE_M="${SENSOR_RANGE_M:-30}"
SENSOR_REACTION_DISTANCE_M="${SENSOR_REACTION_DISTANCE_M:-14}"
SENSOR_REACTION_TTC_S="${SENSOR_REACTION_TTC_S:-1.0}"
SENSOR_REACTION_PERIOD_MS="${SENSOR_REACTION_PERIOD_MS:-50}"

CAM_REACTION_DISTANCE_M="${CAM_REACTION_DISTANCE_M:-95}"
CAM_REACTION_HEADING_DEG="${CAM_REACTION_HEADING_DEG:-140}"
CAM_REACTION_SPEED_FACTOR="${CAM_REACTION_SPEED_FACTOR:-0.08}"
CAM_REACTION_ACTION_DURATION_S="${CAM_REACTION_ACTION_DURATION_S:-4.0}"

if [[ "$USE_SIONNA" != "1" ]]; then
  echo "intersection_radar_comm is Sionna-only. Set USE_SIONNA=1." >&2
  exit 2
fi

have_sionna_listener() {
  ss -lunH 2>/dev/null | awk '{print $4}' | grep -Eq "127\\.0\\.0\\.1:${SIONNA_PORT}$|0\\.0\\.0\\.0:${SIONNA_PORT}$|\\[::1\\]:${SIONNA_PORT}$|\\*:${SIONNA_PORT}$|:${SIONNA_PORT}$"
}

wait_for_sionna_listener() {
  local waited=0
  while (( waited < WAIT_FOR_SIONNA_SEC )); do
    if have_sionna_listener; then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

SIONNA_ARGS="--sionna=1 --sionna-local-machine=${SIONNA_LOCAL_MACHINE} --sionna-server-ip=${SIONNA_SERVER_IP} --sionna-verbose=${SIONNA_VERBOSE}"
if [[ "$CHECK_SIONNA_LISTENER" == "1" ]]; then
  if ! wait_for_sionna_listener; then
    echo "Sionna listener not detected on ${SIONNA_SERVER_IP}:${SIONNA_PORT} after ${WAIT_FOR_SIONNA_SEC}s." >&2
    echo "Start the server first:" >&2
    echo "  experiments/intersection_radar_comm/scripts/start_sionna_server.sh" >&2
    exit 3
  fi
fi

COMMON_ARGS="--sumo-gui=${SUMO_GUI} --sim-time=${SIM_TIME} --sumo-updates=${SUMO_UPDATES} --met-sup=1 --penetrationRate=1 \
--txPower=${TX_POWER_DBM} --RngRun=${RNG_RUN} ${SIONNA_ARGS} \
--sumo-config=${SUMO_CONFIG} \
--cam-reaction-distance-m=${CAM_REACTION_DISTANCE_M} --cam-reaction-heading-deg=${CAM_REACTION_HEADING_DEG} \
--cam-reaction-target-lane=0 --cam-reaction-speed-factor-target-lane=${CAM_REACTION_SPEED_FACTOR} \
--cam-reaction-speed-factor-other-lane=${CAM_REACTION_SPEED_FACTOR} \
--cam-reaction-action-duration-s=${CAM_REACTION_ACTION_DURATION_S} \
--reaction-force-lane-change-enable=0 \
--cpm-reaction-distance-m=0 --cpm-reaction-ttc-s=0 \
--sensor-reaction-enable=1 \
--sensor-reaction-distance-m=${SENSOR_REACTION_DISTANCE_M} \
--sensor-reaction-ttc-s=${SENSOR_REACTION_TTC_S} \
--sensor-reaction-focus-vehicle-id=veh2 \
--sensor-reaction-period-ms=${SENSOR_REACTION_PERIOD_MS} \
--sensor-range-m=${SENSOR_RANGE_M} \
--drop-triggered-reaction-enable=0 \
--rx-drop-prob-cam=0 --rx-drop-prob-cpm=0 \
--rx-drop-prob-phy-cam=0 --rx-drop-prob-phy-cpm=0 \
--target-loss-profile-enable=0 \
--target-loss-rx-drop-prob-cam=0 --target-loss-rx-drop-prob-cpm=0 \
--target-loss-rx-drop-prob-phy-cam=0 --target-loss-rx-drop-prob-phy-cpm=0 \
--incident-enable=0 \
--crash-mode-enable=0"

run_mode() {
  local mode="$1"
  local mode_args="$2"
  local out_dir="$OUT_ROOT/$mode"
  local run_args="$COMMON_ARGS $mode_args"

  echo "=== MODE: $mode ==="
  PLOT="$PLOT" \
  EXPORT_RESULTS="$EXPORT_RESULTS" \
  EVENT_TIMELINE="$EVENT_TIMELINE" \
  ENABLE_COLLISION_OUTPUT="$ENABLE_COLLISION_OUTPUT" \
  COLLISION_ACTION="$COLLISION_ACTION" \
  COLLISION_STOPTIME_S="$COLLISION_STOPTIME_S" \
  COLLISION_CAUSALITY="$COLLISION_CAUSALITY" \
  COLLISION_CAUSALITY_FOCUS_VEHICLE="veh3" \
  OUT_DIR="$out_dir" \
  RUN_ARGS="$run_args" \
  "$ROOT/experiments/operational/v2v-emergencyVehicleAlert-nrv2x/run.sh"
}

case "$MODE" in
  all|radar_bad_link|radar_only|radar_good_link)
    ;;
  *)
    echo "Invalid MODE='$MODE'. Valid values: all, radar_bad_link, radar_only, radar_good_link" >&2
    exit 2
    ;;
esac

run_selected_modes() {
  local modes=("$@")

  for selected_mode in "${modes[@]}"; do
    case "$selected_mode" in
      radar_bad_link)
        run_mode "radar_bad_link" \
          "--per-vehicle-prr-profile=veh2:0.0:${VEH2_EQ_DBM},veh3:0.0:${VEH3_BAD_EQ_DBM}"
        ;;
      radar_only)
        run_mode "radar_only" \
          "--send-cam=false \
--cam-reaction-distance-m=0 --cam-reaction-heading-deg=180 \
--per-vehicle-prr-profile=veh2:0.0:${VEH2_EQ_DBM},veh3:0.0:${VEH3_GOOD_EQ_DBM}"
        ;;
      radar_good_link)
        run_mode "radar_good_link" \
          "--per-vehicle-prr-profile=veh2:0.0:${VEH2_EQ_DBM},veh3:0.0:${VEH3_GOOD_EQ_DBM}"
        ;;
    esac
  done
}

PY_BIN="$ROOT/.venv/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="python3"
fi

SUMMARY_MODES=()
ALL_MODES=(radar_bad_link radar_only radar_good_link)
if [[ "$MODE" == "all" ]]; then
  run_selected_modes "${ALL_MODES[@]}"
  SUMMARY_MODES=("${ALL_MODES[@]}")
else
  run_selected_modes "$MODE"
  for candidate_mode in "${ALL_MODES[@]}"; do
    if [[ -d "$OUT_ROOT/$candidate_mode/artifacts" ]]; then
      SUMMARY_MODES+=("$candidate_mode")
    fi
  done
  if [[ "${#SUMMARY_MODES[@]}" -eq 0 ]]; then
    SUMMARY_MODES=("$MODE")
  fi
fi

"$PY_BIN" "$ROOT/experiments/intersection_radar_comm/tools/summarize_runs.py" \
  --runs-root "$OUT_ROOT" \
  --modes "${SUMMARY_MODES[@]}" \
  --out-dir "$OUT_ROOT/summary"

echo "VALID_INTERSECTION_RADAR_COMM_SCENARIO_DONE: $OUT_ROOT"
echo "MODES: ${SUMMARY_MODES[*]}"
echo "SUMMARY_CSV: $OUT_ROOT/summary/intersection_radar_comm_mode_summary.csv"
