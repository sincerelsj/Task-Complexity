# Behavioral Analysis

The paper focuses on information-search process measures rather than final-choice quality. Input behavior is collected by the [web experiment](../docs/experiment/README.md); raw historical sessions are not distributed in this source package.

## Measures

- **Proportion searched:** unique acquired cells / available cells.
- **Search Index:** `(alternative-wise - attribute-wise) / (alternative-wise + attribute-wise)`. Positive means alternative-wise search; negative means attribute-wise search. Use consecutive first acquisitions; other transition types do not enter this denominator.
- **Search variability:** mean absolute deviation of per-alternative acquisition proportions from the session mean, including unvisited alternatives as zero.
- **ARC:** chance-corrected clustering by alternative and attribute, retained as two separate scores. This is a supplementary analysis capability, not an additional result attributed to the final paper.

## Included Code

`audit_action_effects.py` classifies trace actions and their effects for maintenance diagnostics; it does not establish human motor-time equivalence.

[arc_search/](arc_search/README.md) contains the existing ARC implementation, Search Index recomputation, intermediate counts, and unit tests. It accepts explicit input paths, so historical private directory defaults need not exist in a fresh clone.

Historical result CSVs, session logs, generated analysis outputs, and manuscript build scripts are not included. A fresh clone can run the web task and analysis tests, but cannot reproduce the final paper's numerical figures without the corresponding input data.

Install optional analysis requirements yourself with `python -m pip install -r analysis/requirements.txt`. Then run from the root:

```powershell
python -m unittest discover -s analysis/arc_search -p "test_*.py" -v
```

See [paper scope and release notes](../docs/README.md) for the included code, exact execution settings, and reproducibility limits.
