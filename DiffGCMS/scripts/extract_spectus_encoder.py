#!/usr/bin/env python3
"""Extract SpecTUS encoder weights into a GCMSpectrumEncoder state_dict.

Example:
  python scripts/extract_spectus_encoder.py
  python scripts/extract_spectus_encoder.py --source /path/to/spectus/checkpoint-dir
  python scripts/extract_spectus_encoder.py --source MS-ML/SpecTUS_pretrained_only
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_HF_ID = "MS-ML/SpecTUS_pretrained_only"
DEFAULT_OUTPUT = ROOT / "checkpoints" / "spectus_encoder"
SPECTUS_ROOT = ROOT.parent / "SpecTUS-main"
SPECTUS_ENCODER_PREFIX = "model.encoder."

# SpecTUS Base encoder tensors that GCMSpectrumEncoder strict-loads.
EXPECTED_PREFIXES = (
    "embed_tokens.",
    "embed_positions.",
    "layernorm_embedding.",
    "layers.",
)
N_LAYERS = 12
D_MODEL = 1024


def remap_spectus_encoder_state_dict(state_dict: dict) -> dict:
    remapped = {}
    for key, value in state_dict.items():
        new_key = key
        if new_key.startswith("module."):
            new_key = new_key[len("module.") :]
        if new_key.startswith(SPECTUS_ENCODER_PREFIX):
            new_key = new_key[len(SPECTUS_ENCODER_PREFIX) :]
        elif new_key.startswith("encoder."):
            new_key = new_key[len("encoder.") :]
        remapped[new_key] = value
    return remapped


def _filter_encoder_tensors(remapped: dict) -> dict:
    return {
        k: v
        for k, v in remapped.items()
        if k.startswith(EXPECTED_PREFIXES) and "num_batches_tracked" not in k
    }


def _validate_encoder(state: dict) -> None:
    if "embed_tokens.weight" not in state:
        raise RuntimeError("Missing embed_tokens.weight after remap")
    if tuple(state["embed_tokens.weight"].shape) != (501, D_MODEL):
        raise RuntimeError(
            f"embed_tokens.weight shape {tuple(state['embed_tokens.weight'].shape)} != (501, {D_MODEL})"
        )
    layer_ids = {int(k.split(".")[1]) for k in state if k.startswith("layers.")}
    if layer_ids != set(range(N_LAYERS)):
        raise RuntimeError(f"Expected layers 0..{N_LAYERS - 1}, got {sorted(layer_ids)}")


def _torch_load(path: str):
    """Load a checkpoint without exceeding the ~2GB tool cgroup.

    Full SpecTUS `pytorch_model.bin` is 1.4GB on disk; a regular torch.load
    inflates past the limit and gets SIGKILL. mmap keeps it mapped.
    """
    kwargs = dict(map_location="cpu", mmap=True)
    try:
        return torch.load(path, weights_only=False, **kwargs)
    except TypeError:
        try:
            return torch.load(path, **kwargs)
        except TypeError:
            return torch.load(path, map_location="cpu")


def _write_sharded_encoder(mmap_state: dict, output_dir: Path) -> dict:
    """Write one tensor per file so peak RAM stays near a single layer."""
    if output_dir.exists():
        import shutil

        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    encoder_items = []
    for key, value in mmap_state.items():
        if key.startswith("module."):
            key = key[len("module.") :]
        if key.startswith(SPECTUS_ENCODER_PREFIX):
            key = key[len(SPECTUS_ENCODER_PREFIX) :]
        elif key.startswith("encoder."):
            key = key[len("encoder.") :]
        if not key.startswith(EXPECTED_PREFIXES):
            continue
        encoder_items.append((key, value))

    if not encoder_items:
        raise RuntimeError("No encoder.* tensors found in checkpoint")

    names = []
    for i, (key, value) in enumerate(encoder_items):
        tensor = value.detach().to("cpu").contiguous().clone()
        torch.save(tensor, output_dir / f"{i:04d}.pt")
        (output_dir / f"{i:04d}.key").write_text(key)
        names.append(key)
        del tensor
        if (i + 1) % 40 == 0:
            logging.info("Wrote %d/%d encoder tensors", i + 1, len(encoder_items))

    meta = {"n_tensors": len(names), "keys": names}
    (output_dir / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n")
    return {k: None for k in names}  # names only, for validation


def extract(source: str, output: Path) -> Path:
    logging.info("Loading SpecTUS weights from %s", source)
    raw = _torch_load(str(_resolve_bin(source)))
    payload = raw["state_dict"] if isinstance(raw, dict) and "state_dict" in raw else raw
    output = Path(output)
    if output.suffix == ".pt":
        output = output.with_suffix("")
    names = _write_sharded_encoder(payload, output)
    del raw, payload
    # Validate using key names + one embedding tensor.
    embed_idx = list(names).index("embed_tokens.weight")
    embed = torch.load(output / f"{embed_idx:04d}.pt", map_location="cpu", weights_only=False)
    if tuple(embed.shape) != (501, D_MODEL):
        raise RuntimeError(f"embed_tokens.weight shape {tuple(embed.shape)} != (501, {D_MODEL})")
    layer_ids = {int(k.split(".")[1]) for k in names if k.startswith("layers.")}
    if layer_ids != set(range(N_LAYERS)):
        raise RuntimeError(f"Expected layers 0..{N_LAYERS - 1}, got {sorted(layer_ids)}")
    logging.info("Wrote sharded encoder to %s (%d tensors)", output, len(names))
    return output


def _resolve_bin(source: str) -> Path:
    path = Path(source)
    if path.is_file():
        return path
    if path.is_dir():
        for name in ("pytorch_model.bin", "model.safetensors", "model.bin"):
            candidate = path / name
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"No pytorch_model.bin / model.safetensors under {path}")
    from huggingface_hub import hf_hub_download

    bin_path = hf_hub_download(repo_id=source, filename="pytorch_model.bin")
    logging.info("Downloaded %s", bin_path)
    return Path(bin_path)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=os.environ.get("SPECTUS_SOURCE", DEFAULT_HF_ID),
        help="HF repo id, local SpecTUS checkpoint dir, or pytorch_model.bin",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    extract(args.source, args.output)


if __name__ == "__main__":
    main()
