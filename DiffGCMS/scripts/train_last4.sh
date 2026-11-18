#!/usr/bin/env bash
# DiffGCMS: fine-tune the last 4 encoder layers, projector, and decoder from the best frozen-stage checkpoint
# Usage:
#   bash scripts/train_last4.sh /path/to/frozen_checkpoint.ckpt
#   bash scripts/train_last4.sh /path/to.ckpt train.batch_size=64
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="${PYTHON:-python}"

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/train_last4.sh /path/to/frozen_checkpoint.ckpt [hydra overrides...]"
  echo "Provide the selected checkpoint from frozen-encoder training."
  exit 1
fi

ENC="${ROOT}/checkpoints/spectus_encoder"
CKPT="$1"
shift

if [[ ! -f "$CKPT" ]]; then
  echo "[DiffGCMS] Initialization checkpoint not found: ${CKPT}"
  exit 1
fi
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
echo "[DiffGCMS] load_weights: ${CKPT}"
echo "[DiffGCMS] Starting last-four-layer fine-tuning without restoring the frozen-stage optimizer..."
"${PYTHON}" src/gcms_main.py --config-name=config_train_last4 \
  "general.encoder='${ENC}'" \
  "general.load_weights='${CKPT}'" \
  "$@"
