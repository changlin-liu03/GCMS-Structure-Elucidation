"""Stream cancellation, accounting uncertainty and submission preservation."""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gcms_agent.harness.client import HarnessClient, HarnessError, ProgressWatchdog, guarded_messages


class Stream:
    parent_tool_use_id = None

    def __init__(self, kind, **body):
        self.event = {"type": kind, **body}


class Assistant:
    content = []
    model = "deepseek-flash"


class Result:
    usage = {"input_tokens": 100, "output_tokens": 5}
    is_error = False


SDK = SimpleNamespace(StreamEvent=Stream, AssistantMessage=Assistant, ResultMessage=Result)
TOOL = "mcp__gcms__get_spectrum"


def start(mid="one"):
    return Stream("message_start", message={"id": mid, "usage": {"input_tokens": 100, "output_tokens": 0}})


class FakeClient:
    def __init__(self, events, *, receipt=True, quiet=False):
        self.events, self.receipt, self.quiet = events, receipt, quiet
        self.interrupts = 0
        self.interrupted = asyncio.Event()

    async def interrupt(self):
        self.interrupts += 1
        self.interrupted.set()

    async def receive_response(self):
        for event in self.events:
            yield event
        if self.quiet or self.interrupts:
            await self.interrupted.wait()
        if self.receipt:
            yield Result()
        else:
            await asyncio.Event().wait()


def collect(events, config, *, receipt=True, quiet=False, submitted=False):
    async def run():
        client = FakeClient(events, receipt=receipt, quiet=quiet)
        guard = ProgressWatchdog(config, {TOOL})
        state = SimpleNamespace(submitted={} if submitted else None)
        values = []
        error = None
        try:
            async for message in guarded_messages(client, SDK, state, guard):
                values.append(message)
        except HarnessError as exc:
            error = exc
        return client, guard, values, error
    return asyncio.run(run())


def test_first_truncation_interrupts_without_waiting_for_cli_recovery():
    events = [start(), Stream("message_delta", delta={"stop_reason": "max_tokens"}, usage={"output_tokens": 8192})]
    client, guard, values, error = collect(events, {"stop_on_output_limit": True})
    assert client.interrupts == 1 and error is None
    assert guard.trigger["category"] == "output_limit"
    assert guard.audit()["observed_usage_lower_bound"]["output_tokens"] == 8192
    assert guard.reported_usage["output_tokens"] == 5  # The receipt can be incomplete.
    assert isinstance(values[-1], Result)


def test_output_limit_interrupts_after_a_tool_and_after_submission():
    events = [start(), Stream("content_block_start", content_block={"type": "tool_use", "name": TOOL}),
              Stream("message_delta", delta={"stop_reason": "max_tokens"})]
    client, guard, _, _ = collect(events, {"stop_on_output_limit": True})
    assert client.interrupts == 1 and guard.first_tool_seconds is not None
    client, guard, _, _ = collect(events, {"stop_on_output_limit": True}, submitted=True)
    assert client.interrupts == 1 and guard.trigger["category"] == "output_limit"


def test_character_watchdog_retains_counts_without_generated_text():
    sentinel = "private-synthetic-thought"
    events = [start(), Stream("content_block_delta", delta={"type": "thinking_delta", "thinking": sentinel})]
    client, guard, _, _ = collect(events, {"pre_tool_watchdog_enabled": True, "pre_tool_max_output_chars": 10})
    assert client.interrupts == 1 and guard.trigger["reason"] == "output_characters"
    assert guard.output_chars == len(sentinel)
    assert sentinel not in json.dumps(guard.audit())


def test_elapsed_watchdog_drains_receipt_without_cancelling_the_iterator():
    client, guard, values, error = collect([], {"pre_tool_watchdog_enabled": True,
        "pre_tool_timeout_seconds": .01}, quiet=True)
    assert client.interrupts == 1 and guard.trigger["reason"] == "elapsed_time"
    assert isinstance(values[-1], Result) and error is None


def test_missing_interrupt_receipt_is_bounded_and_unknown():
    client, guard, _, error = collect([start(), start("two")], {"pre_tool_watchdog_enabled": True,
        "pre_tool_interrupt_grace_seconds": .01}, receipt=False)
    assert client.interrupts == 1 and guard.trigger["reason"] == "response_count"
    assert error.category == "pre_tool_no_progress" and not error.usage["known"]


def test_normal_tool_progress_disables_pretool_limits_and_defaults_preserve_behavior():
    events = [start(), Stream("content_block_start", content_block={"type": "tool_use", "name": TOOL}),
              start("two"), Stream("content_block_delta", delta={"text": "long output"})]
    client, guard, _, _ = collect(events, {"pre_tool_watchdog_enabled": True, "pre_tool_max_output_chars": 1})
    assert client.interrupts == 0 and guard.first_tool_seconds is not None
    client, guard, _, _ = collect([start(), start("two"), Stream("message_delta", delta={"stop_reason": "max_tokens"})], {})
    assert client.interrupts == 0 and guard.trigger is None


def test_session_guard_counts_tool_json_and_thinking_after_first_tool():
    events = [start(), Stream("content_block_start", content_block={"type": "tool_use", "name": TOOL}),
              Stream("content_block_delta", delta={"type": "input_json_delta", "partial_json": '{"mz":[16]}'}),
              start("two"), Stream("content_block_delta", delta={"thinking": "synthetic thought"})]
    client, guard, _, _ = collect(events, {"session_max_output_chars": 20})
    assert client.interrupts == 1 and guard.trigger["category"] == "session_output_limit"
    assert guard.session_output_chars == len('{"mz":[16]}synthetic thought')
    assert guard.output_chars == 0 and guard.first_tool_seconds is not None
    client, guard, _, _ = collect(events, {"session_max_output_chars": 20}, submitted=True)
    assert client.interrupts == 1 and guard.trigger["category"] == "session_output_limit"


@pytest.mark.parametrize("config", [{"pre_tool_max_responses": 1.5}, {"pre_tool_timeout_seconds": float("inf")},
    {"pre_tool_max_output_chars": -1}, {"pre_tool_watchdog_enabled": "true"}, {"pre_tool_interrupt_grace_seconds": 0}])
def test_watchdog_configuration_is_validated(config):
    with pytest.raises(HarnessError) as exc:
        ProgressWatchdog(config, {TOOL})
    assert exc.value.category == "configuration_error"


def test_production_harness_never_promotes_an_interrupted_receipt_to_known_usage(tmp_path):
    from test_batch_harness import batch_payload, sdk_for_batch
    sdk = sdk_for_batch(submit=False)
    sdk.StreamEvent = Stream
    base = sdk.ClaudeSDKClient

    class Client(base):
        async def interrupt(self):
            pass

        async def receive_response(self):
            yield start()
            yield Stream("message_delta", delta={"stop_reason": "max_tokens"}, usage={"output_tokens": 8192})
            yield sdk.ResultMessage()

    sdk.ClaudeSDKClient = Client
    harness = HarnessClient({"workspace": str(tmp_path), "stop_on_output_limit": True}, Path(__file__).resolve().parents[2])
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-local-only"}), patch(
            "gcms_agent.harness.client.importlib.import_module", return_value=sdk):
        with pytest.raises(HarnessError) as exc:
            asyncio.run(harness.review_batch(batch_payload()))
    assert exc.value.category == "output_limit"
    assert not exc.value.usage["known"]
    assert exc.value.audit["progress_watchdog"]["reported_usage"]["known"]


def test_submission_racing_with_interrupt_is_retained_with_unknown_usage(tmp_path):
    from test_batch_harness import batch_payload, sdk_for_batch
    sdk = sdk_for_batch(submit=True)
    sdk.StreamEvent = Stream
    base = sdk.ClaudeSDKClient

    class Client(base):
        async def interrupt(self):
            pass

        async def receive_response(self):
            yield start()
            yield Stream("message_delta", delta={"stop_reason": "max_tokens"}, usage={"output_tokens": 8192})
            async for message in super().receive_response():
                yield message

    sdk.ClaudeSDKClient = Client
    harness = HarnessClient({"workspace": str(tmp_path), "stop_on_output_limit": True}, Path(__file__).resolve().parents[2])
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-local-only"}), patch(
            "gcms_agent.harness.client.importlib.import_module", return_value=sdk):
        result = asyncio.run(harness.review_batch(batch_payload()))
    assert len(result["reviews"]) == 2 and not result["usage"]["known"]
    assert result["audit"]["session_metrics"]["terminal_reason"] == "watchdog_after_submission"


@pytest.mark.parametrize("phase", ["result", "receive", "exit"])
@pytest.mark.parametrize("detail,category,retryable,status", [
    ("HTTP 401 unauthorized", "authentication_error", False, 401),
    ("HTTP 402 payment required", "billing_error", False, 402),
    ("HTTP 503 service unavailable", "service_error", True, 503),
    ("Invalid API key", "authentication_error", False, None),
    ("Insufficient balance", "billing_error", False, None),
    ("Unknown model", "configuration_error", False, None),
    ("Transport ended after interruption", "output_limit", False, None),
])
def test_watchdog_does_not_mask_provider_errors_during_drain_or_exit(tmp_path, phase, detail, category, retryable, status):
    from test_batch_harness import batch_payload, sdk_for_batch
    sdk = sdk_for_batch(submit=False, error_detail=detail if phase == "result" else None)
    sdk.StreamEvent = Stream
    base = sdk.ClaudeSDKClient

    class Client(base):
        async def interrupt(self):
            pass

        async def __aexit__(self, *args):
            if phase == "exit":
                raise RuntimeError(detail)
            return False

        async def receive_response(self):
            yield start()
            yield Stream("message_delta", delta={"stop_reason": "max_tokens"}, usage={"output_tokens": 8192})
            if phase == "receive":
                raise RuntimeError(detail)
            yield sdk.ResultMessage()

    sdk.ClaudeSDKClient = Client
    harness = HarnessClient({"workspace": str(tmp_path), "stop_on_output_limit": True}, Path(__file__).resolve().parents[2])
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-local-only"}), patch(
            "gcms_agent.harness.client.importlib.import_module", return_value=sdk):
        with pytest.raises(HarnessError) as caught:
            asyncio.run(harness.review_batch(batch_payload()))
    error = caught.value
    assert error.category == category and error.retryable is retryable
    assert not error.usage["known"]
    if status is not None:
        failure = error.audit["session_metrics"]["terminal_failure" if phase == "result" else "transport_failure"]
        assert failure["effective_http_status"] == status


def test_absolute_deadlines_apply_even_while_pings_are_continuously_readable():
    async def run():
        class BusyClient(FakeClient):
            async def receive_response(self):
                self.pings = 0
                for _ in range(10000):
                    self.pings += 1
                    yield Stream("ping")
                await asyncio.Event().wait()
        client = BusyClient([])
        guard = ProgressWatchdog({"pre_tool_watchdog_enabled": True,
            "pre_tool_timeout_seconds": .005, "pre_tool_interrupt_grace_seconds": .005}, {TOOL})
        with pytest.raises(HarnessError) as caught:
            async for _ in guarded_messages(client, SDK, SimpleNamespace(submitted=None), guard):
                pass
        assert caught.value.category == "pre_tool_no_progress"
        assert guard.trigger["reason"] == "elapsed_time" and client.interrupts == 1
        assert client.pings < 10000  # Both progress deadline and receipt drain are bounded.
    asyncio.run(run())


def assistant(mid, *blocks):
    value = Assistant()
    value.message_id, value.content = mid, list(blocks)
    return value


def test_nonempty_block_start_and_final_message_count_only_the_missing_characters():
    events = [start(), Stream("content_block_start", index=0, content_block={"type": "thinking", "thinking": "abc"}),
              Stream("content_block_delta", index=0, delta={"thinking": "def"}),
              assistant("one", SimpleNamespace(thinking="abcdef")),
              Stream("content_block_start", index=1, content_block={"type": "text", "text": "hi"}),
              assistant("one", SimpleNamespace(text="hi there")),
              assistant("one", SimpleNamespace(thinking="abcdef"), SimpleNamespace(text="hi there"))]
    _, guard, _, _ = collect(events, {})
    assert guard.output_chars == guard.session_output_chars == 14


def test_message_start_without_content_deltas_does_not_disable_final_block_fallback():
    events = [start(), assistant("one", SimpleNamespace(text="long final text"))]
    client, guard, _, _ = collect(events, {"session_max_output_chars": 10})
    assert client.interrupts == 1 and guard.trigger["category"] == "session_output_limit"
    assert guard.session_output_chars == len("long final text")


def test_final_block_then_late_stream_deltas_are_not_double_counted():
    events = [start(), assistant("one", SimpleNamespace(text="abcdef")),
              Stream("content_block_start", index=0, content_block={"type": "text", "text": "abc"}),
              Stream("content_block_delta", index=0, delta={"text": "def"})]
    _, guard, _, _ = collect(events, {})
    assert guard.session_output_chars == 6


@pytest.mark.parametrize("receipt", [True, False])
@pytest.mark.parametrize("trigger", ["tokens", "characters"])
def test_post_submission_generation_is_interrupted_but_submission_is_retained(tmp_path, receipt, trigger):
    from test_batch_harness import batch_payload, sdk_for_batch
    sdk = sdk_for_batch(submit=True)
    sdk.StreamEvent = Stream
    base = sdk.ClaudeSDKClient

    class Client(base):
        async def interrupt(self):
            sdk.interrupted = True

        async def receive_response(self):
            async for message in super().receive_response():
                if not isinstance(message, sdk.ResultMessage):
                    yield message
            # The actual MCP submit handler above has already accepted reviews.
            yield start()
            if trigger == "tokens":
                yield Stream("message_delta", delta={"stop_reason": "max_tokens"})
            else:
                yield Stream("content_block_start", index=0, content_block={"type": "text", "text": "extra output"})
            if receipt:
                yield sdk.ResultMessage()
            else:
                await asyncio.Event().wait()

    sdk.ClaudeSDKClient = Client
    harness = HarnessClient({"workspace": str(tmp_path), "stop_on_output_limit": True,
        "session_max_output_chars": 5, "pre_tool_interrupt_grace_seconds": .01}, Path(__file__).resolve().parents[2])
    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-local-only"}), patch(
            "gcms_agent.harness.client.importlib.import_module", return_value=sdk):
        result = asyncio.run(harness.review_batch(batch_payload()))
    assert sdk.interrupted and len(result["reviews"]) == 2 and not result["usage"]["known"]
    assert result["audit"]["session_metrics"]["terminal_reason"] == "watchdog_after_submission"
