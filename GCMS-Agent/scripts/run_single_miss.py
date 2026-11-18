"""Run the preselected original Top10 miss, then freeze and evaluate it."""
import argparse
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import warnings

from gcms_agent.config import DEFAULT_DATA_ROOT, load_config, write_json, digest
from gcms_agent.data.loader import load_inputs
from gcms_agent.workflow.runner import run_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--ask-key", action="store_true")
    parser.add_argument("--allow-peak", action="store_true",
                        help="Allow this single-case validation during peak hours; monetary limits follow project configuration")
    parser.add_argument("--thinking", choices=("disabled", "low"),
                        help="Override thinking for this run only")
    parser.add_argument("--max-output-tokens", type=int,
                        help="Override the per-response output token limit for this run only")
    args = parser.parse_args()
    if args.max_output_tokens is not None and args.max_output_tokens < 1:
        parser.error("--max-output-tokens must be positive")
    if (args.output / "manifest.json").exists():
        parser.error("Choose a fresh output directory")
    selection = json.loads((args.data_root / "manifests/single_top10_miss_selection.json").read_text(encoding="utf-8"))
    source = Path(selection["source"])
    if hashlib.sha256(source.read_bytes()).hexdigest() != selection["source_sha256"]:
        parser.error("Source differs from audited selection; select and audit again before inference")
    inference = args.data_root / "inference/single_top10_miss.json"
    rows = load_inputs(inference)
    if len(rows) != 1 or rows[0]["sample_id"] != selection["sample_id"]:
        parser.error("Input does not match selection")
    if digest(rows) != selection.get("inference_input_hash"):
        parser.error("Inference input changed since selection was migrated")
    config = load_config(profile="economy")
    if args.allow_peak:
        config["runtime"]["offpeak_only"] = False
    if args.thinking is not None:
        config["model"].update(thinking=args.thinking != "disabled", effort="low")
    if args.max_output_tokens is not None:
        config["runtime"]["max_output_tokens"] = args.max_output_tokens
        config["runtime"]["reservation_output_tokens"] = args.max_output_tokens * config["runtime"]["max_tool_turns"]
    from gcms_agent.credentials import load_project_credentials
    load_project_credentials()
    prompted = False
    if args.ask_key and not os.environ.get("DEEPSEEK_API_KEY"):
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("API key (hidden, process only): ").strip()
        if not key:
            parser.error("No key provided")
        os.environ["DEEPSEEK_API_KEY"] = key
        del key
        prompted = True
    if not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("Set DEEPSEEK_API_KEY or use --ask-key")
    started = time.monotonic()
    try:
        summary = asyncio.run(run_batch(inference, args.output, config))
        elapsed = time.monotonic() - started
    finally:
        if prompted:
            os.environ.pop("DEEPSEEK_API_KEY", None)
    write_json(args.output / "selection.json", selection)
    frozen = {str(p.relative_to(args.output)): hashlib.sha256(p.read_bytes()).hexdigest()
              for folder in ("results", "checkpoints", "logs")
              for p in sorted((args.output / folder).glob("*.json"))}
    write_json(args.output / "prediction_freeze.json", {"files": frozen, "wall_seconds": elapsed,
                                                       "policy": "Freeze before loading evaluation labels"})
    from gcms_agent.evaluation.runner import evaluate
    evaluation = evaluate(args.output, args.data_root / "evaluation/single_top10_miss_labels.json", inference)
    print(json.dumps({"summary": summary, "wall_seconds": elapsed,
        "original": evaluation["original_diffusion_order"],
        "agent": evaluation["processed"], "strict": evaluation["strict_end_to_end_requested_set"],
        "output": str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
