#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCENE_FILE="${1:-${SCENE_FILE:-experiments/strict_sionna_vkr/sionna_scenes/strict_intersection/scene.xml}}"
SIONNA_VERBOSE="${SIONNA_VERBOSE:-1}"
SIONNA_GPUS="${SIONNA_GPUS:-1}"
SIONNA_MI_VARIANT="${SIONNA_MI_VARIANT:-llvm_ad_mono_polarized}"

PY_BIN="$ROOT/.venv_sionna/bin/python"
if [[ ! -x "$PY_BIN" ]]; then
  PY_BIN="python3"
fi

VERBOSE_ARG=()
if [[ "$SIONNA_VERBOSE" == "1" ]]; then
  VERBOSE_ARG+=(--verbose)
fi

cd "$ROOT"
export SIONNA_MI_VARIANT
exec "$PY_BIN" src/sionna/sionna_v1_server_script.py \
  --path-to-xml-scenario "$SCENE_FILE" \
  --local-machine \
  --gpu "$SIONNA_GPUS" \
  "${VERBOSE_ARG[@]}"
