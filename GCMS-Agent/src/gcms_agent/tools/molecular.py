"""Chemistry tool adapters; all work occurs on copies of the input graph."""

from copy import deepcopy


def validate_proposal(graph: dict, operations: list[dict]) -> dict:
    from gcms_agent.chemistry.graph_edit import apply_patch
    from gcms_agent.chemistry.graph_checks import composition
    from gcms_agent.chemistry.validation import validate_graph
    revised = apply_patch(deepcopy(graph), operations)
    return validate_graph(revised, expected_composition=composition(graph), require_connected=True)
