"""Observe Claude CLI request flags using a local fake Messages endpoint.

No DeepSeek connection, real key, user prompt, response thoughts or headers are
recorded. Only whitelisted protocol fields are retained. This is a transport
regression check, not a chemical or model-quality evaluation.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from unittest.mock import patch

import claude_agent_sdk as sdk
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

from gcms_agent.config import load_config, PROJECT_ROOT
from gcms_agent.harness.client import HarnessClient


FIELDS = ("thinking", "model", "max_tokens", "output_config", "stream")


def fake_sse():
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_local_audit", "type": "message", "role": "assistant",
            "model": "deepseek-flash", "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "OK"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {
            "stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {name}\ndata: {json.dumps(value)}\n\n" for name, value in events).encode()


async def validate():
    records = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            records.append({"path": self.path, **{key: body.get(key, "OMITTED") for key in FIELDS}})
            if "count_tokens" in self.path:
                output, mime = b'{"input_tokens":1}', "application/json"
            else:
                output, mime = fake_sse(), "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    results = []
    try:
        for mode in ("sdk_disabled", "sdk_zero_budget", "explicit_extra_body", "project_disabled", "project_low"):
            before = len(records)
            expected = ({"thinking.type": "enabled", "output_config.effort": "low"}
                        if mode == "project_low" else {"thinking": {"type": "disabled"}}
                        if mode in ("explicit_extra_body", "project_disabled") else None)
            with tempfile.TemporaryDirectory(prefix="gcms-local-request-audit-") as directory:
                env = {
                    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                    "ANTHROPIC_AUTH_TOKEN": "fake-local-audit-token", "ANTHROPIC_API_KEY": "",
                    "ANTHROPIC_MODEL": "deepseek-flash[1m]",
                    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-flash[1m]",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-flash[1m]",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-flash",
                    "CLAUDE_CONFIG_DIR": str(Path(directory) / ".claude"),
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1",
                    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4096", "CLAUDE_CODE_EXTRA_BODY": "",
                    "CLAUDE_CODE_EFFORT_LEVEL": "", "MAX_THINKING_TOKENS": "",
                }
                if mode == "explicit_extra_body":
                    env["CLAUDE_CODE_EXTRA_BODY"] = json.dumps({"thinking": {"type": "disabled"}})
                options = ClaudeAgentOptions(
                    model="deepseek-flash[1m]", env=env, cwd=directory,
                    system_prompt="Reply briefly.", tools=[], skills=[], setting_sources=[], plugins=[],
                    permission_mode="dontAsk", max_turns=1, extra_args={"no-session-persistence": None},
                    **({"max_thinking_tokens": 0} if mode == "sdk_zero_budget" else {"thinking": {"type": "disabled"}}),
                )
                if mode in ("project_disabled", "project_low"):
                    config = load_config()
                    harness = HarnessClient({
                        **config["model"],
                        # Test each production branch independently of the active config.
                        "thinking": mode == "project_low",
                        **({"effort": "low"} if mode == "project_low" else {}),
                        "max_output_tokens": config["runtime"]["max_output_tokens"],
                        "max_tool_turns": config["runtime"].get("max_tool_turns", 6),
                    }, PROJECT_ROOT)
                    server_config = sdk.create_sdk_mcp_server(name="gcms", version="1.0.0", tools=[])
                    # Exercise production construction with a fake key; never load .env.
                    with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-local-audit-token"}):
                        options = harness.build_options(
                            sdk, server_config, [], Path(directory), mode="case_batch")
                    # Only redirect endpoint/authentication after the production builder.
                    options.env["ANTHROPIC_BASE_URL"] = env["ANTHROPIC_BASE_URL"]
                    options.env["ANTHROPIC_AUTH_TOKEN"] = "fake-local-audit-token"

                async def run():
                    async with ClaudeSDKClient(options=options) as client:
                        await client.query("Reply OK.")
                        async for message in client.receive_response():
                            pass

                try:
                    await asyncio.wait_for(run(), timeout=35)
                    status = "completed"
                except Exception as exc:
                    status = type(exc).__name__
                results.append({"mode": mode, "expected": expected,
                                "status": status, "requests": records[before:]})
    finally:
        server.shutdown()
        server.server_close()
    return {"uses_paid_api": False, "endpoint": "loopback mock only", "captured_fields": list(FIELDS), "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(validate())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for fixed in report["results"]:
        if fixed["expected"] is None:
            continue
        messages = [row for row in fixed["requests"] if "count_tokens" not in row["path"]]
        def matches_expected(row):
            for path, expected in fixed["expected"].items():
                actual = row
                for key in path.split("."):
                    actual = actual.get(key) if isinstance(actual, dict) else None
                if actual != expected:
                    return False
            return True
        if fixed["status"] != "completed" or not messages or not all(map(matches_expected, messages)):
            raise SystemExit(f"Thinking transport assertion failed: {fixed['mode']}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
