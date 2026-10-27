# Search-Pattern Analysis

## Measures

- **Search proportion:** acquired unique cells divided by available cells.
- **Search Index:** `(alternative-wise - attribute-wise) / (alternative-wise + attribute-wise)`. Positive values indicate alternative-wise search; negative values indicate attribute-wise search.
- **ARC:** chance-corrected clustering, computed separately for apartment and dimension categories.
- **Action classification:** grouping executed tools into observation, click, typing, navigation, and completion operations.

## Code

[arc_search/](arc_search/README.md) computes ARC, recomputes Search Index, checks acquisition sequences, and exports session/group summaries. Supply acquisition and session CSVs using its CLI options.

`audit_action_effects.py` reads agent traces and classifies actions for process analysis. Run `python analysis/audit_action_effects.py --help` for input and output options.

## Setup

```powershell
python -m pip install -r analysis/requirements.txt
python -m unittest discover -s analysis/arc_search -p "test_*.py" -v
```

Input formats and calculation details are documented in [arc_search/](arc_search/README.md). Session data and generated results are supplied locally.
