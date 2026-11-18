"""Tool boundaries and safe metadata; never log credentials or model thoughts."""

import os
import re


def redact(text: str) -> str:
    value = str(text)
    for name in ("DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        key = os.environ.get(name)
        if key:
            value = value.replace(key, "[REDACTED]")
    value = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "[REDACTED]", value)
    value = re.sub(r'''(?i)((?:authorization|x-api-key|api[_-]?key|anthropic_auth_token|anthropic_api_key|deepseek_api_key)["']?\s*[:=]\s*["']?)(?:bearer\s+)?[^\s,"'}]+''', r"\1[REDACTED]", value)
    return re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)


def restricted_pre_tool_hook(allowed_names: set[str], review_state=None):
    async def hook(data, tool_use_id, context):
        if data.get("tool_name") not in allowed_names:
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                    "permissionDecision": "deny", "permissionDecisionReason": "Only registered chemistry tools are permitted"}}
        if review_state is not None:
            if hasattr(review_state, "exploration_denial"):
                denial = review_state.exploration_denial(data.get("tool_name"))
                if denial:
                    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                            "permissionDecision": "deny", "permissionDecisionReason": denial}}
            if data.get("tool_name") in {"mcp__gcms__submit_review", "mcp__gcms__submit_reviews"}:
                try:
                    review_state.preflight(data.get("tool_input"), tool_use_id)
                except (ValueError, TypeError, KeyError) as exc:
                    response = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                        "permissionDecision": "deny", "permissionDecisionReason": redact(str(exc))}}
                    if review_state.format_errors > review_state.max_format_repairs:
                        response.update(continue_=False, stopReason="format_retry_exhausted")
                    return response
            elif review_state.format_errors > review_state.max_format_repairs:
                return {"continue_": False, "stopReason": "format_retry_exhausted"}
        return {}
    return hook
