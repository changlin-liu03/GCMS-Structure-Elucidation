from .molecular import validate_proposal


def analyze_proposal(graph: dict, operations: list[dict], peaks: list[dict]) -> dict:
    from gcms_agent.fragmentation.enumeration import analyze_spectrum
    from gcms_agent.scoring.support import calculate_support
    validation = validate_proposal(graph, operations)
    if not validation["valid"]:
        return {"validation": validation, "evidence": None, "score": None}
    evidence = analyze_spectrum(validation["graph"], peaks)
    score = calculate_support(peaks, evidence["supported_peaks"])
    return {"validation": validation, "evidence": evidence, "score": score}
