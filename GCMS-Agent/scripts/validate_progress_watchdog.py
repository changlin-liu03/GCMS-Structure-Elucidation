"""Exercise production SDK/CLI cancellation against a loopback-only fake API.

No provider requests, credentials, input spectra or model thoughts are used.
Request counts measure cancellation races; they are not a billing guarantee.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import claude_agent_sdk as sdk

from gcms_agent.config import PROJECT_ROOT
from gcms_agent.harness.client import HarnessClient, HarnessError


def sse(kind, **value):
    return (f"event: {kind}\ndata: " + json.dumps({"type": kind, **value}) + "\n\n").encode()


async def validate():
    records = []
    scenario = {"mode": "truncation"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if "count_tokens" in self.path:
                output = b'{"input_tokens":100}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(output)))
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(output)
                return
            mode = scenario["mode"]
            records.append({"mode": mode, "max_tokens": body.get("max_tokens"), "thinking": body.get("thinking"),
                            "output_config": body.get("output_config")})
            number = len(records)
            mode_request = sum(row["mode"] == mode for row in records)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(sse("message_start", message={"id": f"msg_local_{number}",
                    "type": "message", "role": "assistant", "model": "deepseek-flash", "content": [],
                    "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 0}}))
                self.wfile.flush()
                if mode == "quiet":
                    time.sleep(1.)
                if mode == "session_chars" and mode_request == 1:
                    name = next(tool["name"] for tool in body["tools"] if tool["name"].endswith("read_reference"))
                    self.wfile.write(sse("content_block_start", index=0,
                        content_block={"type": "tool_use", "id": "tool_local", "name": name, "input": {}}))
                    self.wfile.write(sse("content_block_delta", index=0, delta={"type": "input_json_delta",
                        "partial_json": json.dumps({"reference_id": "molecode-graph-reasoning/references/syntax.md"})}))
                    self.wfile.write(sse("content_block_stop", index=0))
                    self.wfile.write(sse("message_delta", delta={"stop_reason": "tool_use", "stop_sequence": None},
                                        usage={"output_tokens": 50}))
                    self.wfile.write(sse("message_stop"))
                    self.wfile.flush()
                    return
                self.wfile.write(sse("content_block_start", index=0, content_block={"type": "text", "text": ""}))
                self.wfile.write(sse("content_block_delta", index=0, delta={"type": "text_delta",
                    "text": "s" * 500 if mode == "session_chars" else "synthetic reply"}))
                self.wfile.write(sse("content_block_stop", index=0))
                self.wfile.write(sse("message_delta", delta={"stop_reason": "max_tokens", "stop_sequence": None},
                                    usage={"output_tokens": body.get("max_tokens", 8192)}))
                self.wfile.write(sse("message_stop"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # Expected after host cancellation.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    class LocalHarness(HarnessClient):
        def build_options(self, *args, **kwargs):
            result = super().build_options(*args, **kwargs)
            result.env["ANTHROPIC_BASE_URL"] = base
            result.env["ANTHROPIC_AUTH_TOKEN"] = "fake-loopback-only"
            return result

    result = []
    payload = {"sample_id": "synthetic", "candidate_id": "c1", "graph_version": 0,
               "graph": {"atoms": [{"id": "Mol_C_1", "element": "C", "hydrogens": 4}], "bonds": []},
               "peaks": [{"mz": 16, "intensity": 29}], "round": 1}
    try:
        for mode in ("truncation", "quiet", "session_chars", "economy"):
            scenario["mode"] = mode
            before = len(records)
            with tempfile.TemporaryDirectory(prefix="gcms-watchdog-") as directory:
                client = LocalHarness({"workspace": directory, "thinking": False,
                    "stop_on_output_limit": True, "pre_tool_watchdog_enabled": True,
                    "session_max_output_chars": 200 if mode == "session_chars" else 0,
                    "pre_tool_timeout_seconds": .15 if mode == "quiet" else 15,
                    "pre_tool_interrupt_grace_seconds": 3, "max_output_tokens": 2048 if mode == "economy" else 8192,
                    "request_timeout_seconds": 30}, PROJECT_ROOT)
                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fake-loopback-only",
                        "CLAUDE_CODE_EFFORT_LEVEL": "high", "MAX_THINKING_TOKENS": "32768",
                        "CLAUDE_CODE_EXTRA_BODY": '{"thinking":{"type":"enabled"}}'}):
                    try:
                        await client.review(payload)
                    except HarnessError as exc:
                        result.append({"mode": mode, "category": exc.category, "usage": exc.usage,
                            "watchdog": exc.audit.get("progress_watchdog"), "requests": records[before:]})
                    else:
                        raise AssertionError("Synthetic non-submission unexpectedly completed")
    finally:
        server.shutdown()
        server.server_close()
    return {"uses_paid_api": False, "endpoint": "loopback mock only", "sdk_version": sdk.__version__,
            "results": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(validate())
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for row in report["results"]:
        expected = {"quiet": "pre_tool_no_progress", "session_chars": "session_output_limit"}.get(row["mode"], "output_limit")
        assert row["category"] == expected, row
        assert row["watchdog"]["trigger"] is not None, row
        assert not row["usage"]["known"], row
        assert all(request["thinking"] == {"type": "disabled"} for request in row["requests"]), row
        if row["mode"] == "economy":
            assert row["requests"] and all(request["max_tokens"] == 2048 for request in row["requests"]), row
        if row["mode"] == "session_chars":
            assert row["watchdog"]["first_tool_seconds"] is not None, row
            assert len(row["requests"]) >= 2, row
        # The CLI and host run concurrently: one continuation may already have
        # started. Prove the old four-request recovery chain was cut short.
        assert len(row["requests"]) < 4, row


if __name__ == "__main__":
    main()
