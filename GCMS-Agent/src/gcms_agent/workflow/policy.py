"""Per-session configuration; never mutate a shared concurrent client."""
from copy import deepcopy


def session_policy(config, candidates):
    selected = deepcopy(config)
    return selected, "fast" if not selected["model"].get("thinking", False) else "fixed"


def review_client(client, config):
    from gcms_agent.harness.client import HarnessClient
    if not isinstance(client, HarnessClient):
        return client
    runtime = config["runtime"]
    # Preserve the run-specific workspace, then apply only trusted host config.
    options = {**client.config, **config["model"]}
    for field in (
        "max_output_tokens", "max_tool_turns", "reserve_submit_turns",
        "max_trial_batches", "pre_tool_watchdog_enabled", "stop_on_output_limit",
        "pre_tool_timeout_seconds", "pre_tool_max_output_chars",
        "pre_tool_max_responses", "pre_tool_interrupt_grace_seconds",
        "session_max_output_chars",
    ):
        if field in runtime:
            options[field] = runtime[field]
    options["request_timeout_seconds"] = runtime.get("session_timeout_seconds", 300)
    options["max_format_repairs"] = runtime["format_retries"]
    return HarnessClient(options, client.project_root)
