# Archived offline replay check

**English** | [简体中文](README.zh-CN.md)

This script was moved here from `scripts/` to preserve the replay procedure for an earlier experiment. It runs offline and requires historical inputs absent from this repository.

| Script | Purpose | Inputs and output |
|---|---|---|
| [validate_economy_replay.py](validate_economy_replay.py) | Replay saved candidate proposals through the batch-review tools and workflow, then compare final structures and scores without reading evaluation labels. | Reads checkpoints from `runs/single_top10_miss_nothinking_v2/` and inputs from `data/inference/single_top10_miss.json`. Writes `runs/economy_offline_validation/replay.json`. |

Paths are relative to the GCMS-Agent project root. Running the script can overwrite the replay output. It has no command-line parser: invoking it with `--help` starts the replay. Read the source before running it.

Replay depends on the historical protocol and results. Check data, protocol, rules, and environment compatibility when restoring old files. Archiving did not establish reproducibility or relax the original assertions. For byte-exact historical code, consult the experiment’s frozen source snapshot.

See the [scripts guide](../../README.md) for current entry points.
