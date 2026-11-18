"""Annotate isotope hypotheses separately; they do not earn cleavage credit."""


def annotate_isotopes(graph, molecular_mass, peaks):
    present = {int(p["mz"]) for p in peaks}
    elements = [a["element"] for a in graph["atoms"]]
    annotations = []
    if molecular_mass not in present:
        return annotations
    for shift, reason in ((1, "Possible M+1 isotope contribution; not a separate cleavage"),
                          (2, "Possible M+2 contribution; isotope ratio is not a confirmed identity")):
        if molecular_mass + shift in present:
            annotations.append({"peak_mz": molecular_mass + shift, "parent_peak_mz": molecular_mass,
                                "kind": "isotope_hypothesis", "halogen_present": any(x in elements for x in ("Cl", "Br")),
                                "reason": reason, "used_for_scoring": False})
    return annotations

