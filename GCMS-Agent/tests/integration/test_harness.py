"""Offline tests of submission, isolation and SDK option construction."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from gcms_agent.harness.client import HarnessClient, HarnessError, normalize_usage, classify_failure
from gcms_agent.harness.hooks import redact, restricted_pre_tool_hook
from gcms_agent.harness.prompts import inference_payload
from gcms_agent.harness.skill_loader import SkillLibrary
from gcms_agent.tools.server import ReviewTools, check_review

PROJECT = Path(__file__).resolve().parents[2]


def payload():
    return {"sample_id": "synthetic", "candidate_id": "c1", "graph": {
        "atoms": [{"id": "M_C_1", "element": "C"}, {"id": "M_C_2", "element": "C"}],
        "bonds": [{"a": "M_C_1", "b": "M_C_2", "order": 1}],
    }, "peaks": [{"mz": 30, "intensity": 29}], "round": 1, "graph_version": 0}


def edit_review(count=1):
    return {"decision": "edit", "summary": "Synthetic review", "proposals": [{
        "base_version": 0,
        "operations": [{"op": "set_atom", "id": "M_C_1", "hydrogens": 3} for _ in range(count)],
        "reason": "Chemical repair hypothesis", "peak_refs": [],
        "operation_explanations": ["Restore valence" for _ in range(count)],
    }]}


class ReferenceTests(unittest.TestCase):
    def test_fixed_library_and_reference_boundaries(self):
        library = SkillLibrary(PROJECT)
        self.assertEqual(len(library.skills), 5)
        reference = library.read_reference("molecode-graph-reasoning/references/syntax.md")
        self.assertEqual(len(reference["sha256"]), 64)
        with self.assertRaises(ValueError):
            library.read_reference("../../test.json")

    def test_nested_truth_and_secrets_never_enter_context(self):
        for key in ("true_smiles", "ground_truth", "api_key"):
            item = payload()
            item["previous_feedback"] = [{key: "must_not_enter_model"}]
            with self.assertRaises(ValueError):
                inference_payload(item)
        item = payload()
        item["arbitrary_file_path"] = "test.json"
        self.assertNotIn("arbitrary_file_path", inference_payload(item))

    def test_valid_operations_have_no_count_cap(self):
        result = check_review(edit_review(20), payload())
        self.assertEqual(len(result["proposals"][0]["operations"]), 20)

    def test_forbidden_edits_and_invented_peaks_rejected(self):
        for change in ({"op": "add_atom", "id": "M_C_3"},
                       {"op": "set_atom", "id": "M_C_1", "element": "O"},
                       {"op": "remove_bond", "a": "M_C_1", "b": "missing"}):
            review = edit_review()
            review["proposals"][0]["operations"] = [change]
            with self.assertRaises(ValueError):
                check_review(review, payload())
        review = edit_review()
        review["proposals"][0]["peak_refs"] = [999]
        with self.assertRaises(ValueError):
            check_review(review, payload())

    def test_submit_requires_one_explanation_per_operation(self):
        review = edit_review(2)
        review["proposals"][0]["operation_explanations"] = ["only one"]
        with self.assertRaises(ValueError):
            check_review(review, payload())

    def test_first_chemical_repair_does_not_require_strong_peak(self):
        self.assertEqual(check_review(edit_review(), payload())["proposals"][0]["peak_refs"], [])

    def test_stale_version_rejected(self):
        review = edit_review()
        review["proposals"][0]["base_version"] = 1
        with self.assertRaisesRegex(ValueError, "stale_graph_version"):
            check_review(review, payload())
        del review["proposals"][0]["base_version"]
        with self.assertRaises(ValueError):
            check_review(review, payload())

    def test_host_repair_budget_is_not_exposed_as_model_data(self):
        item = payload()
        item["format_repairs_remaining"] = 1
        self.assertNotIn("format_repairs_remaining", inference_payload(item))
        state = ReviewTools(item, SkillLibrary(PROJECT), max_format_repairs=0)
        with self.assertRaises(ValueError):
            state.submit({})
        with self.assertRaisesRegex(ValueError, "format_retry_exhausted"):
            state.submit({"decision": "keep", "proposals": [], "summary": "stop"})

    def test_real_chemistry_trial_is_transactional(self):
        from gcms_agent.chemistry.smiles_io import parse_smiles
        from gcms_agent.tools.molecular import validate_proposal
        from copy import deepcopy
        graph = parse_smiles("CCO")["graph"]
        before = deepcopy(graph)
        changed = validate_proposal(graph, [
            {"op": "set_bond", "a": "Mol_C_2", "b": "Mol_O_1", "order": 2},
            {"op": "set_atom", "id": "Mol_C_2", "hydrogens": 1},
            {"op": "set_atom", "id": "Mol_O_1", "hydrogens": 0},
        ])
        self.assertTrue(changed["valid"], changed["errors"])
        self.assertEqual(changed["canonical_smiles"], "CC=O")
        self.assertEqual(graph, before)

    def test_submission_idempotent_and_format_repair_limit(self):
        state = ReviewTools(payload(), SkillLibrary(PROJECT))
        good = {"decision": "keep", "proposals": [], "summary": "No supported improvement"}
        state.submit(good)
        state.submit(good)
        with self.assertRaises(ValueError):
            state.submit(edit_review())
        state = ReviewTools(payload(), SkillLibrary(PROJECT))
        for _ in range(3):
            with self.assertRaises(ValueError):
                state.submit({})
        with self.assertRaisesRegex(ValueError, "format_retry_exhausted"):
            state.submit(good)

    def test_usage_cache_is_not_counted_twice(self):
        usage = normalize_usage({"input_tokens": 100, "cache_read_input_tokens": 500, "output_tokens": 30})
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["cache_read_input_tokens"], 500)
        self.assertFalse(normalize_usage(None)["known"])

    def test_error_details_redact_credentials_and_classify(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-private-value"}):
            detail = redact("401 Invalid API key test-private-value; Authorization: Bearer bearer.secret; x-api-key: sk-abc123")
            error = HarnessError("request failed", detail=detail * 100)
        for secret in ("test-private-value", "bearer.secret", "sk-abc123"):
            self.assertNotIn(secret, error.detail)
        self.assertLessEqual(len(error.detail), 800)
        self.assertEqual(classify_failure(None, detail), ("authentication_error", False))
        self.assertEqual(classify_failure(402, "balance"), ("billing_error", False))
        self.assertEqual(classify_failure(400, "unknown model"), ("configuration_error", False))
        self.assertEqual(classify_failure(429, "rate limited"), ("service_error", True))


def fake_sdk(submit=True):
    sdk = SimpleNamespace()
    sdk.ClaudeAgentOptions = lambda **kwargs: SimpleNamespace(**kwargs)
    sdk.HookMatcher = lambda **kwargs: SimpleNamespace(**kwargs)
    sdk.tool = lambda name, description, schema: lambda handler: SimpleNamespace(name=name, handler=handler)
    sdk.create_sdk_mcp_server = lambda **kwargs: kwargs
    class Assistant:
        model = "deepseek-flash"
        usage = {"input_tokens": 50, "output_tokens": 20}
    class Result:
        is_error = False
        usage = {"input_tokens": 100, "cache_read_input_tokens": 500, "output_tokens": 30}
    class Client:
        def __init__(self, options):
            self.options = options
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def query(self, prompt):
            self.prompt = prompt
        async def receive_response(self):
            yield Assistant()
            if submit:
                tool = next(item for item in self.options.mcp_servers["gcms"]["tools"] if item.name == "submit_review")
                await tool.handler({"decision": "keep", "proposals": [], "summary": "No verified improvement"})
            yield Result()
    sdk.AssistantMessage, sdk.ResultMessage, sdk.ClaudeSDKClient = Assistant, Result, Client
    return sdk


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_structured_tool_submission_completes(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
            client = HarnessClient({"workspace": directory}, PROJECT)
            with patch("gcms_agent.harness.client.importlib.import_module", return_value=fake_sdk()):
                result = await client.review(payload())
            self.assertEqual(result["decision"], "keep")
            self.assertEqual(result["usage"]["input_tokens"], 100)
            with patch("gcms_agent.harness.client.importlib.import_module", return_value=fake_sdk(submit=False)):
                with self.assertRaises(HarnessError) as caught:
                    await client.review(payload())
            self.assertEqual(caught.exception.category, "missing_submission")

    async def test_tool_boundary(self):
        hook = restricted_pre_tool_hook({"mcp__gcms__submit_review"})
        self.assertEqual(await hook({"tool_name": "mcp__gcms__submit_review"}, None, None), {})
        denied = await hook({"tool_name": "Read"}, None, None)
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    async def test_schema_failures_count_before_sdk_handler(self):
        state = ReviewTools(payload(), SkillLibrary(PROJECT), max_format_repairs=1)
        hook = restricted_pre_tool_hook({"mcp__gcms__submit_review"}, state)
        data = {"tool_name": "mcp__gcms__submit_review", "tool_input": {}}
        first = await hook(data, "call1", None)
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(state.format_errors, 1)
        await hook(data, "call1", None)
        self.assertEqual(state.format_errors, 1)
        exhausted = await hook(data, "call2", None)
        self.assertEqual(state.format_errors, 2)
        self.assertFalse(exhausted["continue_"])
        self.assertEqual(exhausted["stopReason"], "format_retry_exhausted")

    async def test_sdk_options_disable_ambient_tools_and_models(self):
        sdk = fake_sdk()
        client = HarnessClient({}, PROJECT)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-placeholder"}):
            options = client.build_options(sdk, {}, {"mcp__gcms__submit_review"}, Path("isolated"))
        self.assertEqual(options.tools, [])
        self.assertEqual(options.skills, [])
        self.assertEqual(options.setting_sources, [])
        self.assertTrue(options.strict_mcp_config)
        self.assertEqual(options.env["ANTHROPIC_DEFAULT_OPUS_MODEL"], "deepseek-flash[1m]")
        self.assertNotIn("fallback_model", vars(options))
        self.assertNotIn("unit-test-placeholder", options.system_prompt)
        self.assertEqual(options.env["CLAUDE_CONFIG_DIR"], str(Path("isolated") / ".claude"))


if __name__ == "__main__":
    unittest.main()
