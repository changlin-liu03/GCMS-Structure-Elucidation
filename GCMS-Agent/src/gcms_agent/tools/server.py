"""Build the MCP surface without granting filesystem or shell capabilities."""

import json
from copy import deepcopy

from .molecular import validate_proposal
from .spectral import analyze_proposal


OPERATION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "op": {"enum": ["set_bond", "remove_bond", "set_atom"]},
        "a": {"type": "string"}, "b": {"type": "string"},
        "order": {"enum": [1, 1.5, 2, 3]}, "id": {"type": "string"},
        "hydrogens": {"type": "integer", "minimum": 0},
        "charge": {"type": "integer"}, "radicals": {"type": "integer", "minimum": 0},
        "aromatic": {"type": "boolean"},
    }, "required": ["op"],
}
PROPOSAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "base_version": {"type": "integer", "minimum": 0},
        "operations": {"type": "array", "minItems": 1, "items": OPERATION_SCHEMA},
        "reason": {"type": "string", "minLength": 1},
        "peak_refs": {"type": "array", "items": {"type": "integer"}},
        "operation_explanations": {"type": "array", "items": {"type": "string", "minLength": 1}},
    }, "required": ["base_version", "operations", "reason", "peak_refs", "operation_explanations"],
}
REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "decision": {"enum": ["keep", "edit"]},
        "proposals": {"type": "array", "maxItems": 3, "items": PROPOSAL_SCHEMA},
        "summary": {"type": "string", "minLength": 1},
    }, "required": ["decision", "proposals", "summary"],
}


def _keys(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError(f"{label}: unknown or missing fields")


def check_operations(operations, graph):
    if not isinstance(operations, list) or not operations:
        raise ValueError("operations must be a nonempty list")
    atom_ids = {atom["id"] for atom in graph.get("atoms", [])}
    for op in operations:
        if not isinstance(op, dict):
            raise ValueError("operation must be an object")
        kind = op.get("op")
        if kind in ("set_bond", "remove_bond"):
            fields = {"op", "a", "b"} | ({"order"} if kind == "set_bond" else set())
            _keys(op, fields, fields, kind)
            if op["a"] not in atom_ids or op["b"] not in atom_ids or op["a"] == op["b"]:
                raise ValueError("bond must reference two distinct existing atom IDs")
            if kind == "set_bond" and (isinstance(op["order"], bool) or op["order"] not in (1, 1.5, 2, 3)):
                raise ValueError("unsupported bond order")
        elif kind == "set_atom":
            _keys(op, {"op", "id", "hydrogens", "charge", "radicals", "aromatic"}, {"op", "id"}, kind)
            if op["id"] not in atom_ids or len(op) < 3:
                raise ValueError("set_atom requires an existing atom and at least one changed attribute")
            for field in set(op) - {"op", "id"}:
                if field == "aromatic":
                    if type(op[field]) is not bool:
                        raise ValueError("aromatic must be boolean")
                    continue
                if type(op[field]) is not int or (field != "charge" and op[field] < 0):
                    raise ValueError(f"invalid {field}")
        else:
            raise ValueError("only set_bond, remove_bond, set_atom are permitted")


def check_base_version(proposal, payload):
    expected = payload.get("graph_version")
    supplied = proposal.get("base_version")
    if type(expected) is not int or type(supplied) is not int or expected != supplied:
        raise ValueError("stale_graph_version: base_version must equal the current graph_version")


def check_review(review, payload):
    _keys(review, REVIEW_SCHEMA["properties"], REVIEW_SCHEMA["required"], "review")
    if review["decision"] not in ("keep", "edit") or not isinstance(review["summary"], str) or not review["summary"].strip():
        raise ValueError("review requires a valid decision and nonempty summary")
    proposals = review["proposals"]
    if not isinstance(proposals, list) or len(proposals) > 3:
        raise ValueError("at most three alternative proposals per round")
    if (review["decision"] == "keep") != (len(proposals) == 0):
        raise ValueError("keep requires no proposals; edit requires at least one")
    peak_masses = {peak["mz"] for peak in payload.get("peaks", [])}
    for proposal in proposals:
        _keys(proposal, PROPOSAL_SCHEMA["properties"], PROPOSAL_SCHEMA["required"], "proposal")
        check_base_version(proposal, payload)
        check_operations(proposal["operations"], payload.get("graph") or {})
        if not isinstance(proposal["reason"], str) or not proposal["reason"].strip():
            raise ValueError("each proposal requires a reason")
        refs = proposal["peak_refs"]
        if not isinstance(refs, list) or any(type(mz) is not int or mz not in peak_masses for mz in refs):
            raise ValueError("peak_refs must reference observed integer m/z values")
        explanations = proposal["operation_explanations"]
        if not isinstance(explanations, list) or len(explanations) != len(proposal["operations"]):
            raise ValueError("one explanation is required per operation")
        if any(not isinstance(text, str) or not text.strip() for text in explanations):
            raise ValueError("operation explanations must be nonempty strings")
    return deepcopy(review)


class ReviewTools:
    def __init__(self, payload, library, max_format_repairs=2):
        self.payload = deepcopy(payload)
        self.library = library
        self.submitted = None
        self.format_errors = 0
        self.max_format_repairs = max_format_repairs
        self.preflight_errors = {}

    def preflight(self, review, tool_use_id):
        """Check before the SDK schema layer and count each failed tool call once."""
        if tool_use_id is not None and tool_use_id in self.preflight_errors:
            raise ValueError(self.preflight_errors[tool_use_id])
        if self.format_errors > self.max_format_repairs:
            raise ValueError("format_retry_exhausted")
        try:
            check_review(review, self.payload)
        except (ValueError, TypeError, KeyError) as exc:
            self.format_errors += 1
            if tool_use_id is not None:
                self.preflight_errors[tool_use_id] = str(exc)
            raise

    def submit(self, review):
        if self.format_errors > self.max_format_repairs:
            raise ValueError("format_retry_exhausted")
        try:
            checked = check_review(review, self.payload)
        except (ValueError, TypeError, KeyError):
            self.format_errors += 1
            raise
        if self.submitted is not None and checked != self.submitted:
            raise ValueError("review already submitted; start a new host-controlled round")
        self.submitted = checked
        return {"submitted": True, "message": "Review recorded. The host will validate and decide acceptance."}


def build_server(sdk, payload, library, max_format_repairs=2):
    state = ReviewTools(payload, library, max_format_repairs)

    def wrap(fn):
        async def handler(args):
            try:
                result = fn(args)
                return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, allow_nan=False)}]}
            except (ValueError, TypeError, KeyError) as exc:
                from gcms_agent.harness.hooks import redact
                return {"content": [{"type": "text", "text": redact(str(exc))}], "is_error": True}
        return handler

    operation_input = {"type": "object", "additionalProperties": False, "properties": {
        "base_version": {"type": "integer", "minimum": 0},
        "operations": {"type": "array", "minItems": 1, "items": OPERATION_SCHEMA}}, "required": ["base_version", "operations"]}

    def trial(args, spectral=False):
        if state.submitted is not None:
            raise ValueError("review already submitted")
        _keys(args, {"base_version", "operations"}, {"base_version", "operations"}, "proposal trial")
        check_base_version(args, payload)
        check_operations(args["operations"], payload.get("graph") or {})
        if spectral:
            return analyze_proposal(payload["graph"], args["operations"], payload["peaks"])
        return validate_proposal(payload["graph"], args["operations"])

    definitions = [
        ("get_graph", "Read the current candidate graph, MoleCode and validation snapshot.",
         {"type": "object", "properties": {}, "additionalProperties": False},
         lambda args: {key: payload.get(key) for key in ("graph", "molecode", "validation", "graph_version")}),
        ("read_reference", "Read a versioned scientific reference using an approved reference_id.",
         {"type": "object", "additionalProperties": False, "properties": {"reference_id": {"type": "string", "enum": list(library.references)}}, "required": ["reference_id"]},
         lambda args: library.read_reference(args["reference_id"])),
        ("validate_proposal", "Apply all operations to a copy and validate the resulting molecule. Does not commit edits.", operation_input, lambda args: trial(args)),
        ("analyze_proposal", "Validate a proposal copy and compute deterministic fragment evidence and score.", operation_input, lambda args: trial(args, spectral=True)),
        ("submit_review", "Submit the final structured review. Required to complete this review round.", REVIEW_SCHEMA, state.submit),
    ]
    tool_list = [sdk.tool(name, description, schema)(wrap(handler)) for name, description, schema, handler in definitions]
    server = sdk.create_sdk_mcp_server(name="gcms", version="0.1.0", tools=tool_list)
    names = {f"mcp__gcms__{definition[0]}" for definition in definitions}
    return server, state, names
