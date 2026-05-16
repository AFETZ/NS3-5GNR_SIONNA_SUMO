#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
NS3_DIR="${NS3_DIR:-}"
OUT_DIR="${OUT_DIR:?OUT_DIR is required}"
METRICS_ARGS="${METRICS_ARGS:?METRICS_ARGS is required}"
JOBS="${JOBS:-8}"
NS3_CONFIGURE_ARGS="${NS3_CONFIGURE_ARGS:---enable-examples --build-profile=optimized --disable-werror}"

mkdir -p "$OUT_DIR"
NS3_DIR="$("$ROOT/scripts/ensure-ns3-dev.sh" --root "$ROOT" --ns3-dir "$NS3_DIR")"
"$ROOT/scripts/sync-overlay-into-bootstrap-ns3.sh" --root "$ROOT" --ns3-dir "$NS3_DIR"

cd "$NS3_DIR"

if [[ "$EUID" -eq 0 ]]; then
  NS3_USER_OVERRIDE="${NS3_USER_OVERRIDE:-ns3}"
  run_ns3() { USER="$NS3_USER_OVERRIDE" ./ns3 "$@"; }
else
  run_ns3() { ./ns3 "$@"; }
fi

read -r -a configure_args <<< "$NS3_CONFIGURE_ARGS"
run_ns3 configure "${configure_args[@]}" >/dev/null
run_ns3 build -j "$JOBS" v2v-5g-phy-metrics-experiment >/dev/null

set +e
run_ns3 run --no-build "v2v-5g-phy-metrics-experiment $METRICS_ARGS" \
  > "$OUT_DIR/v2v-5g-phy-metrics-experiment.log" 2>&1
rc=$?
set -e

if [[ $rc -ne 0 ]]; then
  echo "Native metrics sidecar failed. See $OUT_DIR/v2v-5g-phy-metrics-experiment.log" >&2
  exit $rc
fi
