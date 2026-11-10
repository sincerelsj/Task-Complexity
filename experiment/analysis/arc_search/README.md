# ARC and Search Index

`compute_arc.py` computes chance-corrected clustering from first-acquisition sequences. `test_compute_arc.py` checks edge cases, transition direction, and input validation.

## Definition

For N observations across C observed categories with counts n_i, let R be adjacent same-category repetitions:

```text
E[R] = sum(n_i^2) / N - 1
max(R) = N - C
ARC = (R - E[R]) / (max(R) - E[R])
```

Compute separate scores using apartment and dimension categories. A zero denominator yields an undefined score with a reason, never a fabricated zero. The difference of the two scores is only a descriptive contrast.

## Inputs and Execution

Acquisitions require `subject_id`, `model`, `condition`, `n_alternatives`, `n_dimensions`, `acquisition_order`, `apartment_id`, and `dimension_id`. Each row is one first acquisition. Session inputs must use the same session keys and condition metadata; see validation in `compute_arc.py` for optional comparison fields. Supply your local input paths:

```powershell
python analysis/arc_search/compute_arc.py `
  --acquisitions <acquisitions.csv> --sessions <sessions.csv> `
  --output-dir analysis/arc_search/output
```

Duplicate cell acquisitions, missing order values, and inconsistent conditions are rejected. Output includes session scores, category counts, grouped summaries, correlations, descriptive coefficients, and input hashes. Generated output is ignored by Git. Coefficients are descriptive, not inferential human–agent tests.
