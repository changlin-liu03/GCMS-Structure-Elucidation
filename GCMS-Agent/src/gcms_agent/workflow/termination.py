def stop_reason(state, config):
    if (state.get("score") or {}).get("score_lower", 0) >= 1 - 1e-12 and state.get("validation", {}).get("valid"):
        return "objective_ceiling"
    if state.get("stagnation_count", 0) >= config.get("stagnation_rounds", 2):
        return "stagnation"
    if state.get("rounds_completed", 0) >= config.get("max_rounds", 3):
        return "max_rounds"
    return None
