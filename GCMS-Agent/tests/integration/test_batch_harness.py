"""SDK protocol isolation, real batch tool submission and aggregate billing."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gcms_agent.chemistry.smiles_io import parse_smiles
from gcms_agent.data.intensity import raw_peaks
from gcms_agent.harness.client import HarnessClient, HarnessError
from gcms_agent.harness.prompts import batch_inference_payload
from gcms_agent.workflow.candidate import assess

PROJECT = Path(__file__).resolve().parents[2]


def batch_payload():
    peaks = raw_peaks([15, 29, 30], [20, 23, 29])
    graph = parse_smiles("CC")["graph"]
    validation, evidence, score = assess(graph, peaks)
    return {"sample_id": "synthetic", "peaks": peaks, "rules_version": "test",
            "candidates": [{"candidate_id": cid, "graph": graph,
                "validation": validation, "evidence": evidence, "score": score,
                "graph_version": 0, "round": 1} for cid in ("c01", "c02")]}


def sdk_for_batch(*, submit=True, max_turns_after_submit=False, error_detail=None, status=None,
                  known_usage=True, qualifying_trial=False):
    sdk = SimpleNamespace(prompts=[])
    sdk.ClaudeAgentOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    sdk.HookMatcher = lambda **kwargs: SimpleNamespace(**kwargs)
    sdk.tool = lambda name, description, schema: lambda handler: SimpleNamespace(name=name, handler=handler)
    sdk.create_sdk_mcp_server = lambda **kwargs: kwargs

    class Assistant:
        model = "deepseek-flash"
        content = []

    class Result:
        is_error = max_turns_after_submit or error_detail is not None
        subtype = "error_max_turns" if max_turns_after_submit else "success"
        usage = {"input_tokens": 100, "cache_read_input_tokens": 500, "output_tokens": 30} if known_usage else None
        errors = [error_detail] if error_detail else []
        api_error_status = status
        stop_reason = "stop_sequence" if error_detail else "end_turn"
        terminal_reason = "api_error" if error_detail else "completed"
        num_turns = 4

    class Client:
        def __init__(self, options):
            self.options = options
            sdk.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def query(self, prompt):
            sdk.prompts.append(prompt)

        async def receive_response(self):
            yield Assistant()
            if qualifying_trial:
                tool = next(t for t in self.options.mcp_servers["gcms"]["tools"] if t.name == "analyze_proposals")
                result = await tool.handler({"proposals": [{"candidate_id": "c01", "base_version": 0,
                    "operations": [{"op": "set_bond", "a": "Mol_C_1", "b": "Mol_C_2", "order": 2},
                                   {"op": "set_atom", "id": "Mol_C_1", "hydrogens": 2},
                                   {"op": "set_atom", "id": "Mol_C_2", "hydrogens": 2}]}]})
                sdk.trial_response = json.loads(result["content"][0]["text"])
            if submit:
                tool = next(t for t in self.options.mcp_servers["gcms"]["tools"] if t.name == "submit_reviews")
                result = await tool.handler({"reviews": [
                    {"candidate_id": cid, "decision": "keep", "proposals": [],
                     "summary": "Synthetic first review; no verified new proposal."}
                    for cid in ("c01", "c02")]})
                assert not result.get("is_error"), result
            yield Result()

    sdk.AssistantMessage, sdk.ResultMessage, sdk.ClaudeSDKClient = Assistant, Result, Client
    return sdk


@pytest.mark.parametrize("timeout, expected", [(0, None), (60, 60.0)])
def test_session_deadline_zero_disables_wait_for_timeout(timeout, expected):
    import asyncio
    sdk = sdk_for_batch()
    original_wait_for = asyncio.wait_for
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        client = HarnessClient({"workspace": directory, "request_timeout_seconds": timeout}, PROJECT)
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk), \
                patch("gcms_agent.harness.client.asyncio.wait_for", wraps=original_wait_for) as waiting:
            result = asyncio.run(client.review_batch(batch_payload()))
        assert waiting.call_args_list[0].kwargs["timeout"] == expected
    assert len(result["reviews"]) == 2


@pytest.mark.parametrize("cap, expected", [(None, ""), (4096, "4096")])
def test_output_override_can_be_removed_even_with_inherited_cap(cap, expected):
    sdk = sdk_for_batch()
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder",
                                 "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "2048"}):
        options = HarnessClient({"max_output_tokens": cap}, PROJECT).build_options(
            sdk, {}, set(), PROJECT)
    assert options.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == expected


def test_batch_blocks_labels_and_mixed_samples():
    item = batch_payload()
    item["candidates"][0]["ground_truth"] = "do-not-send"
    with pytest.raises(ValueError, match="Forbidden"):
        batch_inference_payload(item)
    item = batch_payload()
    item["candidates"][0]["sample_id"] = "another"
    with pytest.raises(ValueError, match="mix samples"):
        batch_inference_payload(item)


def test_batch_sdk_keeps_harness_and_usage_once():
    import asyncio
    sdk = sdk_for_batch()
    item = batch_payload()
    item["trial_cache"] = {"private_sentinel": "must-not-enter-prompt"}
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        client = HarnessClient({"workspace": directory, "thinking": False, "max_tool_turns": 6}, PROJECT)
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            result = asyncio.run(client.review_batch(item))
    assert len(result["reviews"]) == 2
    assert result["usage"]["input_tokens"] == 100
    assert result["usage"]["output_tokens"] == 30
    assert not any(review.get("stop_review") for review in result["reviews"])
    assert sdk.options.max_turns == 6
    assert sdk.options.thinking == {"type": "disabled"}
    assert json.loads(sdk.options.env["CLAUDE_CODE_EXTRA_BODY"]) == {"thinking": {"type": "disabled"}}
    assert sdk.options.tools == [] and sdk.options.setting_sources == []
    assert "must-not-enter-prompt" not in sdk.prompts[0]
    data = json.loads(sdk.prompts[0].split("\n", 1)[1])
    assert len(data["spectrum"]) == 3
    assert "graph" not in data["candidates"][0]
    assert "audit" in result


def test_thinking_audit_counts_blocks_without_persisting_their_contents():
    import asyncio
    sdk = sdk_for_batch()
    sdk.AssistantMessage.content = [SimpleNamespace(thinking="private-synthetic-sentinel")]
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        client = HarnessClient({"workspace": directory, "thinking": False}, PROJECT)
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            result = asyncio.run(client.review_batch(batch_payload()))
    assert result["audit"]["session_metrics"]["thinking_blocks"] == 1
    assert result["audit"]["session_metrics"]["thinking_characters"] == len("private-synthetic-sentinel")
    assert "private-synthetic-sentinel" not in json.dumps(result)


def test_explicit_thinking_body_overrides_ambient_extra_body():
    sdk = sdk_for_batch()
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder",
                               "CLAUDE_CODE_EXTRA_BODY": '{"thinking":{"type":"enabled"}}'}):
        client = HarnessClient({"thinking": False}, PROJECT)
        options = client.build_options(sdk, {}, set(), PROJECT)
        assert json.loads(options.env["CLAUDE_CODE_EXTRA_BODY"])["thinking"] == {"type": "disabled"}
        client = HarnessClient({"thinking": True}, PROJECT)
        options = client.build_options(sdk, {}, set(), PROJECT)
        assert json.loads(options.env["CLAUDE_CODE_EXTRA_BODY"])["thinking"] == {"type": "enabled"}


def test_low_effort_is_explicit_in_sdk_environment_and_request_body():
    sdk = sdk_for_batch()
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder",
                               "CLAUDE_CODE_EFFORT_LEVEL": "high",
                               "CLAUDE_CODE_EXTRA_BODY": '{"output_config":{"effort":"high"}}'}):
        client = HarnessClient({"thinking": True, "effort": "low"}, PROJECT)
        options = client.build_options(sdk, {}, set(), PROJECT)
    assert options.thinking["type"] == "enabled"
    assert options.effort == "low"
    assert options.env["CLAUDE_CODE_EFFORT_LEVEL"] == "low"
    assert json.loads(options.env["CLAUDE_CODE_EXTRA_BODY"]) == {
        "thinking": {"type": "enabled"}, "output_config": {"effort": "low"}}


def test_effort_does_not_silently_map_an_unsupported_level():
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        with pytest.raises(HarnessError, match="model.effort"):
            HarnessClient({"thinking": True, "effort": "medium"}, PROJECT).build_options(
                sdk_for_batch(), {}, set(), PROJECT)


def test_batch_requires_complete_structured_submission():
    import asyncio
    sdk = sdk_for_batch(submit=False)
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        client = HarnessClient({"workspace": directory}, PROJECT)
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            with pytest.raises(HarnessError) as caught:
                asyncio.run(client.review_batch(batch_payload()))
    assert caught.value.category == "missing_submission"
    assert caught.value.usage["known"]


def test_valid_submission_survives_terminal_turn_limit():
    import asyncio
    sdk = sdk_for_batch(max_turns_after_submit=True)
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        client = HarnessClient({"workspace": directory}, PROJECT)
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            result = asyncio.run(client.review_batch(batch_payload()))
    assert len(result["reviews"]) == 2
    assert result["audit"]["session_metrics"]["terminal_reason"] == "turn_limit_after_submission"


OUTPUT_LIMIT = "API Error: Claude's response exceeded the 8192 output token maximum."


def test_output_limit_is_case_failure_with_redacted_terminal_metadata():
    import asyncio
    sdk = sdk_for_batch(submit=False, error_detail=OUTPUT_LIMIT + " sk-synthetic-secret")
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            with pytest.raises(HarnessError) as caught:
                asyncio.run(HarnessClient({"workspace": directory}, PROJECT).review_batch(batch_payload()))
    error = caught.value
    assert error.category == "output_limit" and not error.retryable
    assert error.usage["known"]
    metrics = error.audit["session_metrics"]
    assert metrics["terminal_metadata"]["terminal_reason"] == "api_error"
    assert metrics["terminal_metadata"]["num_turns"] == 4
    assert "sk-synthetic-secret" not in json.dumps(error.audit)
    assert "[REDACTED]" in error.detail


def test_public_max_tokens_stop_is_output_limit_even_without_error_flag():
    import asyncio
    sdk = sdk_for_batch(submit=False)
    sdk.ResultMessage.stop_reason = "max_tokens"
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            with pytest.raises(HarnessError) as caught:
                asyncio.run(HarnessClient({"workspace": directory}, PROJECT).review_batch(batch_payload()))
    assert caught.value.category == "output_limit"


def test_long_transport_error_is_classified_before_log_truncation():
    import asyncio
    sdk = sdk_for_batch()
    async def failed_exit(self, *args):
        raise RuntimeError("x" * 900 + " API Error: 429 Too Many Requests sk-synthetic-secret")
    sdk.ClaudeSDKClient.__aexit__ = failed_exit
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            with pytest.raises(HarnessError) as caught:
                asyncio.run(HarnessClient({"workspace": directory}, PROJECT).review_batch(batch_payload()))
    error = caught.value
    assert error.category == "service_error" and error.retryable
    assert len(error.detail) == 800
    assert error.audit["session_metrics"]["transport_failure"]["effective_http_status"] == 429
    assert "sk-synthetic-secret" not in json.dumps(error.audit)


def test_resource_subtype_does_not_override_authentication_failure():
    import asyncio
    sdk = sdk_for_batch(max_turns_after_submit=True, error_detail="invalid api key", status=401)
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            with pytest.raises(HarnessError) as caught:
                asyncio.run(HarnessClient({"workspace": directory}, PROJECT).review_batch(batch_payload()))
    assert caught.value.category == "authentication_error"


@pytest.mark.parametrize("detail,status,stop,turns,category,retryable,effective", [
    ("HTTP 429: Too Many Requests", None, "max_tokens", False, "service_error", True, 429),
    ("HTTP status 500: Internal Server Error", None, "tool_use", True, "service_error", True, 500),
    ("status=418: provider error", None, "max_tokens", True, "service_error", False, 418),
    ("HTTP 500 plus output maximum", 401, "max_tokens", True, "authentication_error", False, 401),
    ("Provider unavailable", "503", "max_tokens", True, "service_error", True, 503),
    ("API Error: 404 resource not found", None, "max_tokens", True, "service_error", False, 404),
    ("x" * 900 + " HTTP 429: rate limited", None, "max_tokens", True, "service_error", True, 429),
])
@pytest.mark.parametrize("submitted", [False, True])
def test_text_http_errors_take_priority_over_resource_fallbacks(detail, status, stop, turns,
                                                               category, retryable, effective, submitted):
    import asyncio
    sdk = sdk_for_batch(submit=submitted, max_turns_after_submit=turns, error_detail=detail, status=status)
    sdk.ResultMessage.stop_reason = stop
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            with pytest.raises(HarnessError) as caught:
                asyncio.run(HarnessClient({"workspace": directory}, PROJECT).review_batch(batch_payload()))
    error = caught.value
    assert error.category == category and error.retryable is retryable
    assert error.audit["session_metrics"]["terminal_metadata"]["effective_http_status"] == effective
    assert len(error.detail) <= 800


def test_output_limit_does_not_promote_an_unsubmitted_qualifying_trial():
    import asyncio
    item = batch_payload()
    item["peaks"] = raw_peaks([28], [29])
    for candidate in item["candidates"]:
        # Invalid initial valence makes the subsequently analyzed C=C graph a
        # qualifying first-valid repair, independent of EI scoring details.
        candidate["graph"]["atoms"][0]["hydrogens"] = 4
        candidate["validation"], candidate["evidence"], candidate["score"] = assess(candidate["graph"], item["peaks"])
    sdk = sdk_for_batch(submit=False, error_detail=OUTPUT_LIMIT, qualifying_trial=True)
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            with pytest.raises(HarnessError) as caught:
                asyncio.run(HarnessClient({"workspace": directory}, PROJECT).review_batch(item))
    assert caught.value.category == "output_limit"
    trial = caught.value.audit["trials"][0]
    assert trial["verdict"]["accepted"] is True
    assert trial["selected"] is False


@pytest.mark.parametrize("known,detail,status,expected", [
    (True, OUTPUT_LIMIT, None, None),
    (False, OUTPUT_LIMIT, None, "output_limit"),
    (True, "invalid api key", 401, "authentication_error"),
    (True, "insufficient balance", 402, "billing_error"),
    (True, "unknown model", 400, "configuration_error"),
])
def test_only_known_usage_resource_failure_preserves_validated_submission(known, detail, status, expected):
    import asyncio
    sdk = sdk_for_batch(error_detail=detail, status=status, known_usage=known)
    with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
        with patch("gcms_agent.harness.client.importlib.import_module", return_value=sdk):
            client = HarnessClient({"workspace": directory}, PROJECT)
            if expected:
                with pytest.raises(HarnessError) as caught:
                    asyncio.run(client.review_batch(batch_payload()))
                assert caught.value.category == expected
            else:
                result = asyncio.run(client.review_batch(batch_payload()))
                assert len(result["reviews"]) == 2
                assert result["audit"]["session_metrics"]["terminal_reason"] == "output_limit_after_submission"
                assert result["usage"]["output_tokens"] == 30
