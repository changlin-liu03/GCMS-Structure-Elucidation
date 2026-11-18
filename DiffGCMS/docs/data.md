# DiffGCMS Data Format

English | [简体中文](data.zh-CN.md)

See the [README](../README.md) for commands. The repository reads preprocessed JSONL files, with one spectrum–graph pair per line. Raw instrument files are not direct inputs.

## Fields

| Field | Type | Requirements and use |
|---|---|---|
| `input_ids` | Integer array | Required; m/z tokens, default range 0–500, at most 300 peaks |
| `position_ids` | Integer array | Required; intensity log-bins, default range 0–29, same length as the peak array |
| `node_types` | String array | Required; heavy-atom elements in node order, at most 50 by default |
| `edge_index` | Two integer arrays | Explicit values recommended; COO format `[source nodes, destination nodes]`, zero-based indices |
| `edge_types` | Integer array | One entry per edge: 1 single, 2 double, 3 triple, 4 aromatic |
| `smiles` | String | Recommended; reference molecule reconstruction and evaluation |
| `inchi` | String | Optional; evaluation tries this first, then falls back to SMILES |
| `formula` | String | Optional; `validate_formula` checks the total heavy-atom count for supported elements |
| `atom_hydrogens` | Integer array | Optional; same length as the node array, defaults to zeros; reference hydrogen counts are not used to generate molecules |

The default elements and indices are:

```text
0:C  1:N  2:O  3:F  4:Cl  5:S  6:P  7:Si  8:Br  9:I
```

Spectrum and node arrays should be nonempty. Do not add batch padding to raw records. The loader constructs valid-peak masks from array lengths; token 0 is not automatically treated as padding.

## Graph example

The following artificial record illustrates the fields. Its peak values are not a measured ethanol spectrum and must not be used for performance evaluation:

```json
{"input_ids":[31,45,46],"position_ids":[29,15,10],"node_types":["C","C","O"],"edge_index":[[0,1],[1,2]],"edge_types":[1,1],"smiles":"CCO","formula":"C2H6O","atom_hydrogens":[3,2,1]}
```

This graph has single bonds between nodes 0–1 and 1–2. Record each undirected bond once; the graph builder adds reverse edges automatically. The dense conversion fills in no-bond states, so they do not need entries in `edge_index`.

Use the COO format above consistently. Do not include duplicate bonds, both directions of the same bond, self-loops, or out-of-range node indices.

## Spectrum preprocessing

Convert inputs into integer m/z tokens and intensity log-bins before loading them. Intensity normalization, bin thresholds, peak ordering, and filtering must match the selected encoder and training data.

Retain preprocessing settings when reproducing experiments. Records exceeding peak-count or node-count limits are filtered out rather than truncated.

`validate_formula` checks only the total heavy-atom count for supported elements. It does not check per-element counts, hydrogen counts, formal charge, or consistency between SMILES and the graph.

## Splits and statistics

Prepare separate `train.jsonl`, `valid.jsonl`, and `test.jsonl` files. Perform splitting and deduplication during data preparation, and record the split criteria, proportions, and sample counts. The default directory is `split_90_5_5`.

| File | Contents |
|---|---|
| `atom_types.txt` | Atom frequencies in vocabulary order |
| `edge_types.txt` | No-bond, single, double, triple, and aromatic frequencies |
| `n_counts.txt` | Distribution indexed by node count |
| `valencies.txt` | Valency histogram, currently stored in integer-indexed bins |

```bash
python -m scripts.compute_stats --jsonl ../dataset/split_90_5_5/train.jsonl --stats-dir ../dataset/split_90_5_5/stats
```

The script uses the default atom vocabulary and a 300-peak limit. Set the node limit with `--max-nodes`. Generate statistics from a training set that contains valid samples after filtering. Regenerate and check all statistics after changing the data, vocabulary, or filtering rules.

The reproduction workflow generates all four statistics files from the training set using this standalone script. Record their source and check array lengths, finiteness, and normalization.

## Caches and paths

The dataset uses byte offsets for random access and writes `<filename>.offsets.npz` next to each JSONL file, so the data directory must be writable. Cache reuse depends on the source file's timestamp, size, and selected filtering parameters.

For custom locations, set both `dataset.datadir` and `dataset.stats_dir`. Relative paths are resolved against the repository's parent directory; absolute paths are used as supplied. Test-only runs also require all three split files. See [Experiment reproducibility](reproducibility.md).
