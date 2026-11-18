"""GCMS molecular visualization.

Same reconstruction as DiffMS: convert predicted heavy-atom graphs to RDKit
molecules without writing ground-truth `atom_hydrogens`. Implicit hydrogens
are filled by RDKit during `SanitizeMol` in `is_valid`.
"""

from src.analysis.visualization import MolecularVisualization


class GCMSMolecularVisualization(MolecularVisualization):
    pass
