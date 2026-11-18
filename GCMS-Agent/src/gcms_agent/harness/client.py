"""DeepSeek via Claude Agent SDK, with structured MCP submission only."""

import asyncio
import hashlib
import importlib
import json
import math
import os
import re
from pathlib import Path
from uuid import uuid4

from .hooks import redact, restricted_pre_tool_hook
from .transport import connection_failure
from .prompts import (inference_payload, system_prompt, user_prompt,
                      batch_inference_payload, batch_user_prompt)
from .skill_loader import SkillLibrary
from gcms_agent.tools.server import build_server


class HarnessError(RuntimeError):
    def __init__(self, message, *, category="service_error", retryable=False, usage=None, format_errors=0, detail=None, audit=None):
        super().__init__(message)
        self.category = category
        self.retryable = retryable
        self.format_errors = format_errors
        self.detail = redact(detail)[:800] if detail else None
        self.usage = usage or {"known": False, "input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}
        self.audit = audit or {}


def normalize_usage(raw):
    """Use the final session aggregate once; input_tokens excludes cache hits."""
    if not isinstance(raw, dict):
        return {"known": False, "input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}
    def number(name):
        value = raw.get(name, 0)
        if type(value) not in (int, float) or value < 0:
            raise HarnessError("Invalid usage returned by provider", category="usage_error")
        return int(value)
    return {"known": "input_tokens" in raw and "output_tokens" in raw,
            "input_tokens": number("input_tokens") + number("cache_creation_input_tokens"),
            "cache_read_input_tokens": number("cache_read_input_tokens"),
            "output_tokens": number("output_tokens")}


def effective_http_status(status, detail):
    """Resolve HTTP errors once so resource fallbacks cannot hide text-only errors."""
    if type(status) is int and 400 <= status <= 599:
        return status
    if isinstance(status, str) and re.fullmatch(r"[45]\d\d", status.strip()):
        return int(status.strip())
    # Prefer explicit HTTP/status markers, including less common HTTP errors.
    match = re.search(r"\b(?:HTTP(?:\s+(?:error|status))?|status(?:_code)?|API\s+Error)\s*[:=]?\s*([45]\d\d)\b", detail, re.I)
    if match is None:
        match = re.search(r"\b(400|401|402|403|408|429|500|502|503|504|529)\b", detail)
    return int(match.group(1)) if match else None


def classify_failure(status, detail):
    lower = detail.lower()
    status = effective_http_status(status, detail)
    if status in (401, 403) or any(term in lower for term in ("invalid api key", "authentication", "unauthorized", "invalid auth")):
        return "authentication_error", False
    if status == 402 or any(term in lower for term in ("insufficient balance", "insufficient credits", "credit balance", "payment required")):
        return "billing_error", False
    if status == 400 or any(term in lower for term in ("model not found", "invalid model", "unknown model", "not logged in")):
        return "configuration_error", False
    if status is None and re.search(r"response exceeded the \d+ output token maximum|"
                                   r"(?:exceeded|reached).{0,40}(?:max_tokens|output token (?:limit|maximum))", lower):
        return "output_limit", False
    return "service_error", status in (408, 429, 500, 502, 503, 504, 529) or (status is None and connection_failure(detail))


def terminal_metadata(message):
    """Only public termination metadata; no assistant content or private thoughts."""
    result = {}
    for field in ("subtype", "stop_reason", "terminal_reason", "num_turns", "api_error_status", "is_error"):
        value = getattr(message, field, None)
        if value is None or type(value) in (str, int, float, bool):
            result[field] = redact(value)[:160] if isinstance(value, str) else value
    return result


class ProgressWatchdog:
    """Inspect public streaming metadata, never retain generated content.

    These are host cancellation thresholds, not a provider token/billing cap.
    In particular, an interrupted ResultMessage may omit in-flight usage.
    """

    def __init__(self, config, tool_names):
        self.enabled = config.get("pre_tool_watchdog_enabled", False)
        self.stop_on_output_limit = config.get("stop_on_output_limit", False)
        if type(self.enabled) is not bool or type(self.stop_on_output_limit) is not bool:
            raise HarnessError("Watchdog switches must be boolean", category="configuration_error")
        self.limits = {}
        for name, default in (("pre_tool_timeout_seconds", 90), ("pre_tool_max_output_chars", 24000),
                              ("pre_tool_max_responses", 1), ("pre_tool_interrupt_grace_seconds", 5),
                              ("session_max_output_chars", 0)):
            value = config.get(name, default)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise HarnessError(f"{name} must be finite and nonnegative", category="configuration_error")
            if name in {"pre_tool_max_output_chars", "pre_tool_max_responses", "session_max_output_chars"} and type(value) is not int:
                raise HarnessError(f"{name} must be an integer", category="configuration_error")
            if name == "pre_tool_interrupt_grace_seconds" and value == 0:
                raise HarnessError(f"{name} must be positive", category="configuration_error")
            self.limits[name] = value
        self.tool_names = set(tool_names)
        self.started = None
        self.first_tool_seconds = None
        self.output_chars = 0
        self.session_output_chars = 0
        self.response_ids = set()
        self.current_response = None
        self.stream_events = 0
        self.responses = {}
        self.blocks = {}
        self.trigger = None
        self.interrupt_status = None
        self.reported_usage = None

    def before_first_tool(self, state):
        return self.enabled and self.first_tool_seconds is None and state.submitted is None

    def mark_tool(self, name):
        if name in self.tool_names and self.first_tool_seconds is None:
            self.first_tool_seconds = max(0., asyncio.get_running_loop().time() - self.started)

    def _block(self, response_id, index, kind=None):
        row = self.blocks.setdefault((response_id, index), {"kind": kind, "stream_text": 0, "stream_json": 0,
            "final_text": 0, "final_json": 0, "counted_text": 0, "counted_json": 0,
            "pre_tool": self.first_tool_seconds is None, "fingerprint": None, "tool_id": None})
        if kind is not None:
            row["kind"] = kind
        return row

    def _count_block(self, row):
        # A final AssistantMessage and stream fragments describe the same block.
        # Account only the increase; partial streaming never disables fallback
        # for blocks whose deltas were absent. No content is retained here.
        text = max(row["stream_text"], row["final_text"])
        tool_json = max(row["stream_json"], row["final_json"])
        additional_text = text - row["counted_text"]
        if row["pre_tool"]:
            self.output_chars += additional_text
        self.session_output_chars += additional_text + tool_json - row["counted_json"]
        row["counted_text"], row["counted_json"] = text, tool_json

    def _assistant_blocks(self, message):
        response_id = getattr(message, "message_id", None) or self.current_response
        if response_id is None:
            response_id = f"assistant_{len(self.response_ids)}"
        self.response_ids.add(response_id)
        for block in getattr(message, "content", []) or []:
            self.mark_tool(getattr(block, "name", None))
            text = getattr(block, "text", None)
            thinking = getattr(block, "thinking", None)
            tool_input = getattr(block, "input", None)
            kind = ("text" if isinstance(text, str) else "thinking" if isinstance(thinking, str)
                    else "tool_use" if isinstance(tool_input, dict) else None)
            if kind is None:
                continue
            content = text if kind == "text" else thinking if kind == "thinking" else json.dumps(tool_input, ensure_ascii=False)
            fingerprint = hashlib.sha256(content.encode()).hexdigest()
            candidates = [(index, row) for (rid, index), row in self.blocks.items()
                          if rid == response_id and row["kind"] == kind]
            # The CLI can split one response into one AssistantMessage per
            # block. Match stream blocks in index order, then recognize replay.
            available = [pair for pair in candidates if pair[1]["fingerprint"] is None]
            if kind == "tool_use":
                matched = [pair for pair in candidates if pair[1]["tool_id"] == getattr(block, "id", None)]
            else:
                matched = []
            matched = matched or available or [pair for pair in candidates if pair[1]["fingerprint"] == fingerprint]
            if matched:
                row = min(matched, key=lambda pair: pair[0])[1]
            else:
                index = max((idx for rid, idx in self.blocks if rid == response_id), default=-1) + 1
                row = self._block(response_id, index, kind)
            row["fingerprint"] = fingerprint
            if kind == "tool_use":
                row["final_json"] = len(content)
                row["tool_id"] = getattr(block, "id", None)
            else:
                row["final_text"] = len(content)
            self._count_block(row)

    def observe(self, message, sdk, state):
        event_class = getattr(sdk, "StreamEvent", ())
        if isinstance(message, event_class) and getattr(message, "parent_tool_use_id", None) is None:
            event = message.event
            self.stream_events += 1
            kind = event.get("type")
            if kind == "message_start":
                body = event.get("message") or {}
                self.current_response = body.get("id") or f"anonymous_{len(self.response_ids)}"
                self.response_ids.add(self.current_response)
                normalized = normalize_usage(body.get("usage"))
                self.responses[self.current_response] = normalized
            elif kind == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    self.mark_tool(block.get("name"))
                row = self._block(self.current_response, event.get("index", 0), block.get("type"))
                row["tool_id"] = block.get("id")
                row["stream_text"] += sum(len(block[k]) for k in ("text", "thinking") if isinstance(block.get(k), str))
                if isinstance(block.get("input"), dict) and block["input"]:
                    row["stream_json"] += len(json.dumps(block["input"], ensure_ascii=False))
                self._count_block(row)
            elif kind == "content_block_delta":
                delta = event.get("delta") or {}
                block_kind = ("text" if "text" in delta else "thinking" if "thinking" in delta
                              else "tool_use" if "partial_json" in delta else None)
                row = self._block(self.current_response, event.get("index", 0), block_kind)
                row["stream_text"] += sum(len(delta[k]) for k in ("text", "thinking") if isinstance(delta.get(k), str))
                row["stream_json"] += len(delta["partial_json"]) if isinstance(delta.get("partial_json"), str) else 0
                self._count_block(row)
            elif kind == "message_delta":
                if self.current_response is not None and isinstance(event.get("usage"), dict):
                    usage = event["usage"]
                    if "output_tokens" in usage:
                        self.responses[self.current_response]["output_tokens"] = normalize_usage(usage)["output_tokens"]
                if self.stop_on_output_limit and (event.get("delta") or {}).get("stop_reason") == "max_tokens":
                    return "output_limit", "stream_max_tokens"
        elif isinstance(message, sdk.AssistantMessage):
            self._assistant_blocks(message)
        cap = self.limits["session_max_output_chars"]
        if cap and self.session_output_chars >= cap:
            return "session_output_limit", "session_output_characters"
        if self.before_first_tool(state):
            cap = self.limits["pre_tool_max_output_chars"]
            if cap and self.output_chars >= cap:
                return "pre_tool_no_progress", "output_characters"
            cap = self.limits["pre_tool_max_responses"]
            if cap and len(self.response_ids) > cap:
                return "pre_tool_no_progress", "response_count"
        return None

    def audit(self):
        return {"enabled": self.enabled, "stop_on_output_limit": self.stop_on_output_limit,
                "limits": dict(self.limits), "trigger": self.trigger,
                "interrupt_status": self.interrupt_status, "first_tool_seconds": self.first_tool_seconds,
                "pre_tool_output_chars": self.output_chars, "observed_response_count": len(self.response_ids),
                "session_output_chars": self.session_output_chars,
                "stream_events": self.stream_events,
                "observed_usage_lower_bound": {"known": False, **{key: sum(row.get(key, 0) for row in self.responses.values())
                    for key in ("input_tokens", "cache_read_input_tokens", "output_tokens")}},
                "reported_usage": self.reported_usage,
                "accounting_policy": "interrupted usage is unverified; retain reservation until reconciled"}


async def guarded_messages(client, sdk, state, watchdog):
    """Interrupt once, then briefly drain a final receipt before closing the SDK.

    Keep the SDK's context manager on its entering task. A pending anext is
    shielded during timeout so its generator remains usable for receipt drain.
    """
    loop = asyncio.get_running_loop()
    watchdog.started = loop.time()
    iterator = client.receive_response().__aiter__()
    pending = None
    drain_deadline = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(iterator.__anext__())
            deadline = drain_deadline
            if deadline is None and watchdog.before_first_tool(state) and watchdog.limits["pre_tool_timeout_seconds"]:
                deadline = watchdog.started + watchdog.limits["pre_tool_timeout_seconds"]
            try:
                # Do not let a continuous stream of immediately readable pings
                # bypass either the absolute progress or receipt-drain deadline.
                if deadline is not None and loop.time() >= deadline:
                    raise asyncio.TimeoutError
                if deadline is None:
                    message = await pending
                else:
                    message = await asyncio.wait_for(asyncio.shield(pending), max(.001, deadline - loop.time()))
                pending = None
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                if watchdog.trigger:
                    break
                # A tool may have completed via the SDK while awaiting a message.
                if not watchdog.before_first_tool(state):
                    continue
                reason = ("pre_tool_no_progress", "elapsed_time")
            else:
                if isinstance(message, sdk.ResultMessage):
                    if watchdog.trigger:
                        watchdog.reported_usage = normalize_usage(message.usage)
                    yield message
                    return
                reason = watchdog.observe(message, sdk, state) if not watchdog.trigger else None
                yield message
            if reason and watchdog.trigger is None:
                watchdog.trigger = {"category": reason[0], "reason": reason[1],
                                    "elapsed_seconds": loop.time() - watchdog.started}
                drain_deadline = loop.time() + watchdog.limits["pre_tool_interrupt_grace_seconds"]
                try:
                    await asyncio.wait_for(client.interrupt(), timeout=max(.001, drain_deadline - loop.time()))
                    watchdog.interrupt_status = "acknowledged"
                except Exception as exc:
                    watchdog.interrupt_status = type(exc).__name__
        if watchdog.trigger:
            raise HarnessError("Session interrupted by progress watchdog", category=watchdog.trigger["category"])
    finally:
        if pending is not None:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await iterator.aclose()


class HarnessClient:
    """Isolated Claude SDK sessions for single or batched chemical reviews."""

    def __init__(self, config: dict, project_root: Path):
        self.config = dict(config)
        self.project_root = Path(project_root).resolve()
        self.library = SkillLibrary(self.project_root)

    def build_options(self, sdk, server, tool_names, workspace, review_state=None, mode="legacy"):
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            raise HarnessError("Set DEEPSEEK_API_KEY in the process environment", category="configuration_error")
        base = self.config.get("api_base", self.config.get("base_url", "https://api.deepseek.com/anthropic")).rstrip("/")
        if base != "https://api.deepseek.com/anthropic":
            raise HarnessError("This backend requires the official DeepSeek Anthropic endpoint", category="configuration_error")
        model = self.config.get("model", "deepseek-flash[1m]")
        if model not in ("deepseek-flash", "deepseek-flash[1m]"):
            raise HarnessError("This experiment requires deepseek-flash", category="configuration_error")
        thinking_enabled = self.config.get("thinking", False)
        if type(thinking_enabled) is not bool:
            raise HarnessError("model.thinking must be a boolean", category="configuration_error")
        effort = self.config.get("effort", "high")
        if thinking_enabled and effort not in ("low", "high", "max"):
            raise HarnessError("model.effort must be low, high or max", category="configuration_error")
        extra_body = {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
        if thinking_enabled:
            extra_body["output_config"] = {"effort": effort}
        environment = {
            "ANTHROPIC_BASE_URL": base, "ANTHROPIC_AUTH_TOKEN": key,
            "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model, "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash", "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-flash",
            # Empty clears inherited overrides; the pinned CLI uses its default.
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS": ("" if self.config.get("max_output_tokens") is None
                                             else str(self.config["max_output_tokens"])),
            "CLAUDE_CODE_EFFORT_LEVEL": "", "MAX_THINKING_TOKENS": "",
            # The pinned CLI omits the HTTP thinking field for --thinking disabled.
            # DeepSeek defaults to enabled when omitted. Its native extra-body
            # mechanism makes the user's choice explicit on every API request.
            "CLAUDE_CODE_EXTRA_BODY": json.dumps(extra_body, separators=(",", ":")),
            "CLAUDE_CONFIG_DIR": str(Path(workspace) / ".claude"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1",
            "CLAUDE_CODE_USE_BEDROCK": "", "CLAUDE_CODE_USE_VERTEX": "", "CLAUDE_CODE_USE_FOUNDRY": "",
        }
        if thinking_enabled:
            environment["CLAUDE_CODE_EFFORT_LEVEL"] = effort
        options = dict(
            model=model, env=environment, cwd=str(workspace),
            system_prompt=system_prompt(self.library, mode), tools=[], skills=[], setting_sources=[], plugins=[],
            mcp_servers={"gcms": server}, strict_mcp_config=True, allowed_tools=sorted(tool_names),
            permission_mode="dontAsk", max_turns=int(self.config.get("max_tool_turns", 12)),
            thinking=({"type": "enabled", "budget_tokens": 4096} if thinking_enabled else {"type": "disabled"}),
            effort=effort if thinking_enabled else None,
            include_partial_messages=True,
            hooks={"PreToolUse": [sdk.HookMatcher(hooks=[restricted_pre_tool_hook(tool_names, review_state)])]},
            extra_args={"no-session-persistence": None},
        )
        if self.config.get("cli_path"):
            path = Path(self.config["cli_path"])
            if not path.is_file() or path.suffix.lower() in (".cmd", ".bat"):
                raise HarnessError("cli_path must point to a native Claude executable", category="configuration_error")
            options["cli_path"] = str(path)
        return sdk.ClaudeAgentOptions(**options)

    async def review(self, payload: dict) -> dict:
        return await self._review(payload, batched=False)

    async def review_batch(self, payload: dict) -> dict:
        return await self._review(payload, batched=True)

    async def _review(self, payload: dict, *, batched: bool) -> dict:
        clean = batch_inference_payload(payload) if batched else inference_payload(payload)
        default_repairs = self.config.get("max_format_repairs", 2)
        if batched:
            default_repairs = min(item.get("format_repairs_remaining", default_repairs)
                                  for item in clean["candidates"])
        remaining_repairs = payload.get("format_repairs_remaining", default_repairs)
        if type(remaining_repairs) is not int or remaining_repairs < 0:
            raise HarnessError("format_repairs_remaining must be a nonnegative integer", category="configuration_error")
        try:
            sdk = importlib.import_module("claude_agent_sdk")
        except ImportError as exc:
            raise HarnessError("Install the pinned claude-agent-sdk package before live inference", category="configuration_error") from exc
        if batched:
            from gcms_agent.tools.batch_server import build_batch_server
            clean["max_trial_batches"] = payload.get("max_trial_batches", self.config.get("max_trial_batches", 4))
            max_turns = self.config.get("max_tool_turns", 12)
            reserve = self.config.get("reserve_submit_turns", 2)
            if type(max_turns) is not int or type(reserve) is not int or not 1 <= reserve < max_turns:
                raise HarnessError("reserve_submit_turns must be positive and below max_tool_turns", category="configuration_error")
            clean["session_budget"] = {"max_tool_turns": max_turns, "reserve_submit_turns": reserve,
                                       "max_non_submission_tool_calls": max_turns - reserve}
            # Private deterministic cache never enters the LLM prompt or a serialized receipt.
            if "trial_cache" in payload:
                clean["trial_cache"] = payload["trial_cache"]
            server, state, names = build_batch_server(sdk, clean, self.library, remaining_repairs)
            prompt = batch_user_prompt(clean)
        else:
            server, state, names = build_server(sdk, clean, self.library, remaining_repairs)
            prompt = user_prompt(clean)
        # A fresh empty cwd cannot contain test labels, project CLAUDE.md, or legacy sessions.
        workspace_base = Path(self.config.get("workspace", self.project_root / "runs" / ".harness"))
        workspace = workspace_base / uuid4().hex
        watchdog = ProgressWatchdog(self.config, names)
        options = self.build_options(sdk, server, names, workspace, state,
                                     "case_batch" if batched else "legacy")
        workspace.mkdir(parents=True, exist_ok=False)
        usage = normalize_usage(None)
        models = set()
        terminal_seen = False
        metrics = {"prompt_chars": len(prompt), "system_prompt_chars": len(options.system_prompt),
                   "assistant_messages": 0, "assistant_text_chars": 0, "tool_use_blocks": 0,
                   "content_block_types": {}, "thinking_blocks": 0, "thinking_characters": 0}

        def audit():
            return {**(state.audit() if hasattr(state, "audit") else {}), "session_metrics": dict(metrics),
                    "progress_watchdog": watchdog.audit(),
                    "thinking_control": {"requested": "enabled" if self.config.get("thinking", False) else "disabled",
                                         "requested_effort": self.config.get("effort", "high") if self.config.get("thinking", False) else None,
                                         "transport": "CLAUDE_CODE_EXTRA_BODY",
                                         "response_thinking_blocks": metrics["thinking_blocks"]}}

        async def run():
            nonlocal usage, terminal_seen
            async with sdk.ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                async for message in guarded_messages(client, sdk, state, watchdog):
                    if isinstance(message, sdk.AssistantMessage):
                        metrics["assistant_messages"] += 1
                        if hasattr(state, "observe_model_response"):
                            state.observe_model_response(getattr(message, "message_id", None))
                        if message.model:
                            models.add(message.model)
                        # Record only sizes; no free-form assistant text or private thoughts.
                        for block in getattr(message, "content", []) or []:
                            block_type = type(block).__name__
                            metrics["content_block_types"][block_type] = metrics["content_block_types"].get(block_type, 0) + 1
                            if isinstance(getattr(block, "thinking", None), str):
                                metrics["thinking_blocks"] += 1
                                metrics["thinking_characters"] += len(block.thinking)
                            if isinstance(getattr(block, "text", None), str):
                                metrics["assistant_text_chars"] += len(block.text)
                            if getattr(block, "name", None) is not None:
                                metrics["tool_use_blocks"] += 1
                    elif isinstance(message, sdk.ResultMessage):
                        terminal_seen = True
                        metrics["terminal_metadata"] = terminal_metadata(message)
                        if watchdog.trigger:
                            # A CLI interrupt receipt may exclude an in-flight API
                            # request. Do not mistake partial/zero usage for its bill.
                            usage = normalize_usage(None)
                            pieces = [item for item in (getattr(message, "errors", None) or []) if isinstance(item, str)]
                            if isinstance(getattr(message, "result", None), str):
                                pieces.append(message.result)
                            detail = redact("; ".join(pieces))
                            status = effective_http_status(getattr(message, "api_error_status", None), detail)
                            category, retryable = classify_failure(status, detail)
                            if status is not None or category in {"authentication_error", "billing_error", "configuration_error", "usage_error"}:
                                metrics["terminal_metadata"]["effective_http_status"] = status
                                metrics["terminal_failure"] = {"category": category, "retryable": retryable,
                                    "detail": detail[:800], "effective_http_status": status}
                                raise HarnessError("Provider error while draining interrupted session", category=category,
                                                   retryable=retryable, usage=usage, detail=detail)
                            if state.submitted is None:
                                raise HarnessError("Session interrupted by progress watchdog",
                                                   category=watchdog.trigger["category"], usage=usage)
                            # A submission can race with the interrupt. Retain a
                            # validated submission, still charge uncertain usage
                            # conservatively instead of promoting the receipt.
                            metrics["terminal_reason"] = "watchdog_after_submission"
                            continue
                        usage = normalize_usage(message.usage)
                        if message.is_error or getattr(message, "stop_reason", None) == "max_tokens":
                            status = getattr(message, "api_error_status", None)
                            pieces = [item for item in (getattr(message, "errors", None) or []) if isinstance(item, str)]
                            if isinstance(getattr(message, "result", None), str):
                                pieces.append(message.result)
                            full_detail = redact("; ".join(pieces))
                            if not full_detail and getattr(message, "stop_reason", None) == "max_tokens":
                                full_detail = "API response reached max_tokens."
                            status = effective_http_status(status, full_detail)
                            detail = full_detail[:800]
                            metrics["terminal_metadata"]["effective_http_status"] = status
                            category, retryable = classify_failure(status, full_detail)
                            if (category == "service_error" and status is None
                                    and getattr(message, "stop_reason", None) == "max_tokens"):
                                category, retryable = "output_limit", False
                            if (category in {"service_error", "output_limit"} and status is None
                                    and getattr(message, "subtype", "") == "error_max_turns"):
                                category, retryable = "turn_limit", False
                            if (status is None and state.format_errors > state.max_format_repairs
                                    and category not in {"authentication_error", "billing_error", "configuration_error", "usage_error"}):
                                category, retryable = "format_retry_exhausted", False
                            metrics["terminal_failure"] = {"category": category, "retryable": retryable,
                                                           "detail": detail, "effective_http_status": status}
                            # A validated complete tool submission is authoritative. A later
                            # resource stop cannot erase it, but unknown accounting or provider
                            # auth/configuration errors are never hidden by this recovery.
                            if (state.submitted is not None and usage["known"]
                                    and category in {"turn_limit", "output_limit"}):
                                metrics["terminal_reason"] = category + "_after_submission"
                                continue
                            raise HarnessError(f"Claude Harness terminated with an error (HTTP status {status})", category=category, retryable=retryable, usage=usage, detail=detail, audit=audit())

        try:
            session_timeout = float(self.config.get("request_timeout_seconds", 300))
            await asyncio.wait_for(run(), timeout=None if session_timeout == 0 else session_timeout)
        except asyncio.TimeoutError as exc:
            if not (watchdog.trigger and state.submitted is not None):
                raise HarnessError("Review session timed out", category="timeout", usage=usage, format_errors=state.format_errors, audit=audit()) from exc
            usage = normalize_usage(None)
            metrics["terminal_reason"] = "watchdog_after_submission"
        except HarnessError as exc:
            exc.format_errors = state.format_errors
            exc.audit = audit()
            if not (watchdog.trigger and state.submitted is not None and exc.category == watchdog.trigger["category"]):
                raise
            usage = normalize_usage(None)
            metrics["terminal_reason"] = "watchdog_after_submission"
        except Exception as exc:
            # Provider and transport exception strings may include authentication headers.
            full_detail = redact(str(exc))
            detail = full_detail[:800]
            status = effective_http_status(getattr(exc, "status_code", None), full_detail)
            category, retryable = classify_failure(status, full_detail)
            if (watchdog.trigger and status is None
                    and category not in {"authentication_error", "billing_error", "configuration_error", "usage_error"}):
                category, retryable = watchdog.trigger["category"], False
            metrics["transport_failure"] = {"category": category, "retryable": retryable,
                                            "effective_http_status": status,
                                            "detail": detail}
            if not (watchdog.trigger and state.submitted is not None and category == watchdog.trigger["category"]):
                raise HarnessError(f"Harness transport failed ({type(exc).__name__})", category=category, retryable=retryable, usage=usage, format_errors=state.format_errors, detail=detail, audit=audit()) from None
            usage = normalize_usage(None)
            metrics["terminal_reason"] = "watchdog_after_submission"
        if state.format_errors > state.max_format_repairs:
            raise HarnessError("Structured submission repair limit reached", category="format_retry_exhausted", usage=usage, format_errors=state.format_errors, audit=audit())
        if state.submitted is None or (not terminal_seen and not watchdog.trigger):
            raise HarnessError("No validated submit_review result was received", category="missing_submission", usage=usage, format_errors=state.format_errors, audit=audit())
        return {**state.submitted, "usage": usage, "format_errors": state.format_errors,
                "actual_models": sorted(models), "skills_manifest": self.library.manifest(), "audit": audit()}
