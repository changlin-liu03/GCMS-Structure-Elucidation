import copy
from gcms_agent.config import PROJECT_ROOT, load_config
from gcms_agent.harness.client import HarnessClient
from gcms_agent.workflow.policy import session_policy, review_client


def test_all_rounds_preserve_low_thinking_even_after_invalid_trials():
    for profile in ("standard", "economy"):
        config = load_config(profile=profile)
        before = copy.deepcopy(config)
        for round_number in range(3):
            state = {"validation": {"valid": False}, "rounds_completed": round_number,
                     "history": [{"exploration_feedback": {"status": "exploration_insufficient"}}]}
            selected, tier = session_policy(config, [state])
            assert tier == "fixed"
            assert selected["model"]["thinking"] is True
            assert selected["model"]["effort"] == "low"
            assert config == before


def test_concurrent_sessions_do_not_mutate_shared_harness():
    config = load_config(profile="economy")
    original = HarnessClient({**config["model"], "workspace": "run-specific-workspaces"}, PROJECT_ROOT)
    first = review_client(original, config)
    second = review_client(original, config)
    assert first is not second and first is not original
    assert first.config["thinking"] is second.config["thinking"] is True
    assert first.config["effort"] == second.config["effort"] == "low"
    assert first.config["workspace"] == second.config["workspace"] == "run-specific-workspaces"
    assert first.config["stop_on_output_limit"] is False
    assert first.config["max_output_tokens"] is None
    assert first.config["pre_tool_max_output_chars"] == 0
    assert first.config["session_max_output_chars"] == 0
    assert first.config["request_timeout_seconds"] == 0
    first.config["max_output_tokens"] = 1
    assert second.config["max_output_tokens"] is None


def test_standard_preserves_resource_settings_and_mock_injection():
    config = load_config(profile="standard")
    selected, tier = session_policy(config, [])
    assert selected == config
    assert config["runtime"]["max_output_tokens"] is None
    assert config["runtime"]["reservation_output_tokens"] == 65536
    fake = object()
    assert review_client(fake, selected) is fake


def test_economy_removes_monetary_caps_but_keeps_scheduling_controls():
    runtime = load_config(profile="economy")["runtime"]
    assert runtime["case_budget_cny"] is None
    assert runtime["budget_cny"] is None
    assert runtime["max_sessions_per_case"] == 8
    assert runtime["offpeak_only"] is True
    assert runtime["batch_concurrency"] == 2
