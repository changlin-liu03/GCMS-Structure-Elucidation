#!/usr/bin/env bash
# DiffGCMS full test-set evaluation
# Usage:
#   bash scripts/test.sh /path/to/frozen_checkpoint.ckpt
#   bash scripts/test.sh /path/to/finetuned_checkpoint.ckpt
#   bash scripts/test.sh /path/to.ckpt train.eval_batch_size=32
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"

PYTHON="${PYTHON:-python}"

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/test.sh /path/to.ckpt [hydra overrides...]"
  echo "Provide a checkpoint from frozen-encoder training or last-four-layer fine-tuning."
  exit 1
fi

ENC="${ROOT}/checkpoints/spectus_encoder"
CKPT="$1"
shift

if [[ ! -f "$CKPT" ]]; then
  echo "Checkpoint not found: ${CKPT}"
  exit 1
fi
if [[ ! -d "$ENC" ]] || [[ ! -f "$ENC/0000.pt" ]]; then
  echo "Encoder weights not found: ${ENC}"
  echo "Run this command first: python scripts/extract_spectus_encoder.py"
  exit 1
fi

echo "[DiffGCMS] python: ${PYTHON}"
echo "[DiffGCMS] encoder: ${ENC}"
echo "[DiffGCMS] checkpoint: ${CKPT}"

"${PYTHON}" src/gcms_main.py --config-name=config_test \
  "general.encoder='${ENC}'" \
  "general.test_only='${CKPT}'" \
  train.eval_batch_size=128 \
  "$@"
