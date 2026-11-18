import argparse
import asyncio
import json
from datetime import datetime
from gcms_agent.config import PROJECT_ROOT, DEFAULT_DATA_ROOT, load_config

def main():
    parser = argparse.ArgumentParser(description="GC-MS candidate repair and evidence reranking")
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit", help="Prepare isolated inference data and labels")
    audit.add_argument("source")
    audit.add_argument("--output", default=str(DEFAULT_DATA_ROOT))
    for name in ("run", "resume"):
        cmd = sub.add_parser(name)
        cmd.add_argument("input")
        cmd.add_argument("--output", required=name=="resume")
        cmd.add_argument("--mode", choices=("agent", "baseline"), default="agent")
        cmd.add_argument("--limit", type=int)
        cmd.add_argument("--configs")
        cmd.add_argument("--profile", choices=("economy", "standard"), default="economy",
                         help="economy: parallel reviews; standard: original resource settings; monetary limits and thinking follow configuration")
        money = cmd.add_mutually_exclusive_group()
        money.add_argument("--budget-usd", type=float)
        money.add_argument("--budget-cny", type=float)
        cmd.add_argument("--strategy", choices=("legacy", "case_batch"))
        cmd.add_argument("--reservation-input-tokens", type=int,
                         help="Explicit session input-cost reservation estimate; may only increase on resume")
        cmd.add_argument("--offpeak-only", action="store_true", default=None,
                         help="Pause new sessions in peak windows; resume explicitly later")
    ev = sub.add_parser("evaluate")
    ev.add_argument("run_dir")
    ev.add_argument("--labels", default=str(DEFAULT_DATA_ROOT / "evaluation/labels.json"))
    ev.add_argument("--input", help="Optional isolated inputs for original Diffusion-order baseline")
    args = parser.parse_args()
    if args.command == "audit":
        from gcms_agent.data.prepare import prepare
        result = prepare(args.source, args.output)
    elif args.command == "evaluate":
        from gcms_agent.evaluation.runner import evaluate
        result = evaluate(args.run_dir, args.labels, args.input)
    else:
        from gcms_agent.workflow.runner import run_batch
        config = load_config(args.configs, profile=args.profile if args.mode == "agent" else "standard")
        if args.budget_usd is not None:
            if args.profile == "economy" and args.mode == "agent":
                parser.error("Economy profile uses CNY case budgets; use --budget-cny or --profile standard")
            import math
            if not math.isfinite(args.budget_usd) or args.budget_usd <= 0:
                parser.error("Budget must be finite and positive")
            config["runtime"]["budget_usd"] = args.budget_usd
            config["runtime"]["budget_currency"] = "USD"
        if args.budget_cny is not None:
            import math
            if not math.isfinite(args.budget_cny) or args.budget_cny <= 0:
                parser.error("Budget must be finite and positive")
            config["runtime"]["budget_cny"] = args.budget_cny
            config["runtime"]["budget_currency"] = "CNY"
        if args.strategy:
            if args.profile == "economy" and args.strategy != "case_batch" and args.mode == "agent":
                parser.error("Economy profile requires case_batch; use --profile standard for legacy")
            config["runtime"]["review_strategy"] = args.strategy
        if args.reservation_input_tokens is not None:
            if args.reservation_input_tokens <= 0:
                parser.error("Reservation input estimate must be positive")
            config["runtime"]["reservation_input_tokens"] = args.reservation_input_tokens
        if args.offpeak_only is not None:
            config["runtime"]["offpeak_only"] = args.offpeak_only
        output = args.output or PROJECT_ROOT/"runs"/(datetime.now().strftime("%Y%m%d_%H%M%S")+"_"+args.mode)
        result = asyncio.run(run_batch(args.input, output, config, args.mode, args.limit, args.command=="resume"))
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
