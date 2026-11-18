from pathlib import Path
import hashlib
import json
import yaml
import math

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "raw_linear_v1"

def load_config(directory=None, profile=None):
    """Load base files and any profile from the same configuration directory.

    A custom directory must include profiles/economy.yaml when economy is
    requested. Missing custom profiles never fall back to project defaults.
    """
    root = Path(directory) if directory else PROJECT_ROOT / "configs"
    config = {name: yaml.safe_load((root / f"{name}.yaml").read_text(encoding="utf-8")) for name in ("model", "chemistry", "scoring", "runtime")}
    if profile not in (None, "standard", "economy"):
        raise ValueError("Unknown configuration profile")
    if profile == "economy":
        profile_path = root / "profiles" / "economy.yaml"
        try:
            overrides = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"Missing economy profile in the selected configuration directory: {profile_path}"
            ) from exc
        for section, values in overrides.items():
            config[section].update(values)
    runtime = config["runtime"]
    currency = runtime.get("budget_currency", "USD")
    if currency not in ("USD", "CNY"):
        raise ValueError("runtime.budget_currency must be USD or CNY")
    budget = runtime.get(f"budget_{currency.lower()}")
    if budget is not None and (type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0):
        raise ValueError("Budget must be null or finite and positive")
    if runtime["concurrency"] < 1:
        raise ValueError("Concurrency must be positive")
    if runtime.get("review_strategy", "legacy") not in ("legacy", "case_batch"):
        raise ValueError("Unknown review strategy")
    if type(runtime.get("offpeak_only", False)) is not bool:
        raise ValueError("offpeak_only must be boolean")
    for name in ("batch_size", "max_batch_chars", "max_tool_turns", "max_trial_batches", "reservation_input_tokens", "case_output_token_target", "reserve_submit_turns", "batch_concurrency", "max_sessions_per_case", "max_transport_failures_per_case"):
        if name in runtime and (type(runtime[name]) is not int or runtime[name] < 1):
            raise ValueError(f"runtime.{name} must be a positive integer")
    session_timeout = runtime.get("session_timeout_seconds", 300)
    if type(session_timeout) is not int or session_timeout < 0:
        raise ValueError("runtime.session_timeout_seconds must be a nonnegative integer (0 disables it)")
    for name in ("case_budget_cny", "case_budget_usd"):
        if runtime.get(name) is not None and (type(runtime[name]) not in (int, float) or not math.isfinite(runtime[name]) or runtime[name] <= 0):
            raise ValueError(f"runtime.{name} must be finite and positive")
    for name in ("pre_tool_watchdog_enabled", "stop_on_output_limit"):
        if name in runtime and type(runtime[name]) is not bool:
            raise ValueError(f"runtime.{name} must be boolean")
    guard_fields = {"pre_tool_timeout_seconds", "pre_tool_max_output_chars", "pre_tool_max_responses", "pre_tool_interrupt_grace_seconds", "session_max_output_chars"}
    for name in guard_fields:
        if name in runtime and (type(runtime[name]) is not int or runtime[name] < 0):
            raise ValueError(f"runtime.{name} must be a nonnegative integer")
    if runtime.get("reserve_submit_turns", 1) >= runtime.get("max_tool_turns", 6):
        raise ValueError("reserve_submit_turns must leave at least one exploration turn")
    if currency == "CNY":
        for tariff in ("peak", "offpeak"):
            for name in ("input", "cache", "output"):
                price = config["model"].get("prices_cny", {}).get(tariff, {}).get(name)
                if type(price) not in (int, float) or not math.isfinite(price) or price < 0:
                    raise ValueError("CNY prices must be finite and nonnegative")
    for name in ("preserve_heavy_atom_composition", "require_connected", "require_neutral", "require_closed_shell"):
        if config["chemistry"].get(name) is not True:
            raise ValueError(f"This release requires chemistry.{name}=true")
    score = config["scoring"]
    if score.get("intensity_encoding") != "raw_linear" or score["fragmentation_depth"] != 2:
        raise ValueError("This release supports raw_linear intensity and two-step rules only")
    if config["runtime"]["proposals_per_round"] != 3:
        raise ValueError("The structured tool contract permits three alternative proposals per round")
    output_limit = runtime.get("max_output_tokens")
    if output_limit is not None and (type(output_limit) is not int or output_limit < 1):
        raise ValueError("runtime.max_output_tokens must be null or a positive integer")
    reservation = runtime.get("reservation_output_tokens")
    if (output_limit is None or reservation is not None) and (type(reservation) is not int or reservation < 1):
        raise ValueError("runtime.reservation_output_tokens must be positive when no output cap is set")
    for name in ("max_rounds", "stagnation_rounds", "candidate_timeout_seconds", "concurrency"):
        if type(config["runtime"][name]) is not int or config["runtime"][name] < 1:
            raise ValueError(f"runtime.{name} must be a positive integer")
    for name in ("network_retries", "format_retries"):
        if type(config["runtime"][name]) is not int or config["runtime"][name] < 0:
            raise ValueError(f"runtime.{name} must be a nonnegative integer")
    return config

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)
