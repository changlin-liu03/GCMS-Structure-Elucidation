# Synthetic offline examples

**English** | [简体中文](README.md)

This directory contains two independently authored demonstration inputs. Peak positions, linear intensities, candidate order, and reference labels are synthetic choices. They were not extracted, transformed, or copied from private research data and are neither instrument measurements nor external spectral-library records. Candidate SMILES represent common small molecules; the intensities do not represent their actual EI abundances.

| File | Purpose |
|---|---|
| [inference.json](inference.json) | Two label-free inputs, each with 10 candidate slots and explicit conditions |
| [labels.json](labels.json) | Synthetic reference structures matched by sample ID, for separate evaluation demonstrations only |

`synthetic_ethanol_demo` uses ethanol/dimethyl ether candidates, including repeated strings and equivalent SMILES, to demonstrate source slots and deduplication. `synthetic_acetone_demo` uses acetone and other C3H6O isomers. Ethanol and acetone are manually designated references, not identities experimentally established from the constructed peaks.

`energy_ev: 70` is a demonstration condition, and `energy_is_assumed: true` marks it as assumed. The JSON files use only runtime-allowed fields; synthetic provenance is explained here.

## Offline execution and evaluation

Install project dependencies first and run from the repository root. This workflow makes no model calls and needs no API key or private data. The output directory must be new; choose another name when repeating the demonstration.

```powershell
$env:PYTHONPATH = (Resolve-Path ./src).Path
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'

./.venv/Scripts/python.exe -B -m gcms_agent.cli run data/examples/inference.json --mode baseline --output runs/public_example
./.venv/Scripts/python.exe -B -m gcms_agent.cli evaluate runs/public_example --labels data/examples/labels.json --input data/examples/inference.json
```

The first command produces deterministic candidate scores, rankings, and explanatory reports without Agent structure edits. The second reads separate demonstration labels and writes `runs/public_example/evaluation.json`. Specify `--labels` explicitly to avoid the default private-data label path.

The workflow should process two inputs, retaining ten source slots each. Merging equivalent structures may leave fewer than ten ranking entries. Scores and accuracy demonstrate output formats and evaluation only; they are not evidence of model performance, structure-identification ability, or real experimental effectiveness. A manually assigned reference is not required to rank first.

New files are excluded by the data ignore rules by default. Do not replace the two allowlisted JSON files with private research data. See the [parent data guide](../README.en.md) for format and local-data details.
