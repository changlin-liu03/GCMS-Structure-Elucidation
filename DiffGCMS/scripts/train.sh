#!/usr/bin/env bash
# DiffGCMS training launcher (frozen SpecTUS encoder)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="${PYTHON:-python}"

ENC="${ROOT}/checkpoints/spectus_encoder"
if [[ ! -d "$ENC" ]] || [[ ! -f "$ENC/0000.pt" ]]; then
  echo "[DiffGCMS] ${ENC} not found. Extracting encoder weights from SpecTUS..."
  "${PYTHON}" scripts/extract_spectus_encoder.py --output "$ENC"
fi

STATS_DIR="${ROOT}/../dataset/split_90_5_5/stats/atom_types.txt"
if [[ ! -f "$STATS_DIR" ]]; then
  echo "[DiffGCMS] Precomputed statistics not found. Computing them from the full training set..."
  "${PYTHON}" scripts/compute_stats.py \
    --jsonl "${ROOT}/../dataset/split_90_5_5/train.jsonl" \
    --stats-dir "${ROOT}/../dataset/split_90_5_5/stats"
fi

echo "[DiffGCMS] python: ${PYTHON}"
echo "[DiffGCMS] encoder: ${ENC}"
echo "[DiffGCMS] Starting training and validation with a frozen encoder (no testing)..."
"${PYTHON}" src/gcms_main.py --config-name=config_train "general.encoder=${ENC}" "$@"
