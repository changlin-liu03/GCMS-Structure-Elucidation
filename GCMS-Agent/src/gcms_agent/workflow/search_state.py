"""Separate the exploration cursor from the best committed scientific result."""
from copy import deepcopy

FIELDS = ("graph", "validation", "evidence", "score", "graph_version")


def remember_best(state):
    if not state.get("validation", {}).get("valid"):
        return
    best = state.get("best_state")
    # Complete assessments win; point score matches final ranking.
    # Exact ties retain the earlier structure, without claiming identity.
    key = lambda s: (s.get("score") is not None, (s.get("score") or {}).get("score", -1))
    if best is None or key(state) > key(best):
        state["best_state"] = {k: deepcopy(state[k]) for k in FIELDS if k in state}


def finalize_best(state):
    remember_best(state)
    state["exploration_state"] = {k: deepcopy(state[k]) for k in FIELDS if k in state}
    if state.get("best_state"):
        state.update(deepcopy(state["best_state"]))


def resume_exploration(state):
    if state.get("exploration_state"):
        state.update(deepcopy(state.pop("exploration_state")))


def final_status(state):
    if not state["validation"].get("valid"):
        return "invalid_unresolved"
    initial = state.get("initial_validation", {})
    return "kept" if initial.get("valid") and initial.get("canonical_smiles") == state["validation"].get("canonical_smiles") else "corrected"
