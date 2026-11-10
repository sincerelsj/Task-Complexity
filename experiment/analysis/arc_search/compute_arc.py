"""Compute chance-corrected ARC scores for information-acquisition sequences.

The implementation follows the adjusted ratio of clustering described by
Roenker, Thompson, and Brown (1971):

    ARC = (R - E[R]) / (max(R) - E[R])

where R is the observed count of adjacent same-category repetitions,
E[R] = sum(n_i**2) / N - 1, and max(R) = N - C.

For this project, a category is either an alternative or an attribute.
The two ARC scores are retained as separate measures. Their difference is
exported only as a descriptive contrast, not as a standard ARC statistic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Hashable, Sequence

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACQUISITIONS = (
    PROJECT_ROOT
    / "paper"
    / "paper_sol"
    / "data"
    / "processed"
    / "rep1_acquisitions.csv"
)
DEFAULT_SESSIONS = (
    PROJECT_ROOT
    / "paper"
    / "paper_sol"
    / "data"
    / "processed"
    / "rep1_sessions.csv"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "output"

ACQUISITION_COLUMNS = {
    "subject_id",
    "model",
    "condition",
    "n_alternatives",
    "n_dimensions",
    "acquisition_order",
    "apartment_id",
    "dimension_id",
}


@dataclass(frozen=True)
class ArcResult:
    """ARC score and every intermediate quantity needed to audit it."""

    score: float
    n_observations: int
    n_categories: int
    observed_repetitions: int
    expected_repetitions: float
    maximum_repetitions: int
    denominator: float
    defined: bool
    undefined_reason: str


@dataclass(frozen=True)
class SearchIndexResult:
    """Payne-sign Search Index and its transition counts."""

    score: float
    alternative_wise_transitions: int
    attribute_wise_transitions: int
    other_transitions: int
    classified_transitions: int
    total_transitions: int


def compute_arc(labels: Sequence[Hashable]) -> ArcResult:
    """Return ARC for one ordered categorical sequence.

    ARC is undefined when there are fewer than two observations or when the
    observed category distribution leaves no distance between chance and the
    maximum possible repetition count. Undefined scores are returned as NaN
    with an explicit reason; they are never silently replaced by zero or one.
    """

    sequence = list(labels)
    n_observations = len(sequence)
    if n_observations < 2:
        return ArcResult(
            score=math.nan,
            n_observations=n_observations,
            n_categories=len(set(sequence)),
            observed_repetitions=0,
            expected_repetitions=math.nan,
            maximum_repetitions=0,
            denominator=math.nan,
            defined=False,
            undefined_reason="fewer_than_two_observations",
        )

    if any(pd.isna(label) for label in sequence):
        raise ValueError("ARC labels must not contain missing values")

    counts = Counter(sequence)
    n_categories = len(counts)
    observed = sum(
        first == second for first, second in zip(sequence, sequence[1:])
    )
    expected = sum(count * count for count in counts.values()) / n_observations - 1
    maximum = n_observations - n_categories
    denominator = maximum - expected

    if math.isclose(denominator, 0.0, abs_tol=1e-12):
        if n_categories == 1:
            reason = "single_category_sequence"
        elif n_categories == n_observations:
            reason = "all_categories_unique"
        else:
            reason = "zero_chance_to_maximum_distance"
        return ArcResult(
            score=math.nan,
            n_observations=n_observations,
            n_categories=n_categories,
            observed_repetitions=observed,
            expected_repetitions=expected,
            maximum_repetitions=maximum,
            denominator=denominator,
            defined=False,
            undefined_reason=reason,
        )

    score = (observed - expected) / denominator
    return ArcResult(
        score=float(score),
        n_observations=n_observations,
        n_categories=n_categories,
        observed_repetitions=observed,
        expected_repetitions=float(expected),
        maximum_repetitions=maximum,
        denominator=float(denominator),
        defined=True,
        undefined_reason="",
    )


def compute_search_index(
    alternatives: Sequence[Hashable],
    attributes: Sequence[Hashable],
) -> SearchIndexResult:
    """Recompute the current Payne-sign Search Index from the same sequence."""

    if len(alternatives) != len(attributes):
        raise ValueError("Alternative and attribute sequences must have equal length")

    alternative_wise = 0
    attribute_wise = 0
    other = 0
    for (alternative_1, attribute_1), (alternative_2, attribute_2) in zip(
        zip(alternatives, attributes),
        zip(alternatives[1:], attributes[1:]),
    ):
        if alternative_1 == alternative_2 and attribute_1 != attribute_2:
            alternative_wise += 1
        elif attribute_1 == attribute_2 and alternative_1 != alternative_2:
            attribute_wise += 1
        else:
            other += 1

    classified = alternative_wise + attribute_wise
    total = classified + other
    score = (
        (alternative_wise - attribute_wise) / classified
        if classified
        else math.nan
    )
    return SearchIndexResult(
        score=float(score),
        alternative_wise_transitions=alternative_wise,
        attribute_wise_transitions=attribute_wise,
        other_transitions=other,
        classified_transitions=classified,
        total_transitions=total,
    )


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, encoding="utf-8-sig")


def validate_acquisitions(acquisitions: pd.DataFrame) -> None:
    """Fail on schema, sequence, or within-session design inconsistencies."""

    missing = ACQUISITION_COLUMNS.difference(acquisitions.columns)
    if missing:
        raise ValueError(f"Acquisition table is missing columns: {sorted(missing)}")
    if acquisitions.empty:
        raise ValueError("Acquisition table is empty")
    if acquisitions[list(ACQUISITION_COLUMNS)].isna().any().any():
        raise ValueError("Required acquisition columns contain missing values")

    duplicates = acquisitions.duplicated(
        ["subject_id", "apartment_id", "dimension_id"]
    )
    if duplicates.any():
        examples = acquisitions.loc[
            duplicates, ["subject_id", "apartment_id", "dimension_id"]
        ].head()
        raise ValueError(
            "Input must contain first acquisitions only; repeated session-cell rows "
            f"were found:\n{examples.to_string(index=False)}"
        )

    for subject_id, group in acquisitions.groupby("subject_id", sort=False):
        ordered = group.sort_values("acquisition_order")
        observed_order = ordered["acquisition_order"].astype(int).tolist()
        expected_order = list(range(1, len(ordered) + 1))
        if observed_order != expected_order:
            raise ValueError(
                f"Nonconsecutive acquisition_order for {subject_id}: "
                f"expected 1..{len(ordered)}"
            )
        for column in (
            "model",
            "condition",
            "n_alternatives",
            "n_dimensions",
        ):
            if ordered[column].nunique(dropna=False) != 1:
                raise ValueError(
                    f"{column} varies within acquisition session {subject_id}"
                )


def _prefixed_arc(result: ArcResult, prefix: str) -> dict[str, object]:
    values = asdict(result)
    values["score"] = values.pop("score")
    return {f"{prefix}_{key}": value for key, value in values.items()}


def build_session_scores(
    acquisitions: pd.DataFrame,
    sessions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Calculate both ARC scores and SI for every acquisition session."""

    validate_acquisitions(acquisitions)
    rows: list[dict[str, object]] = []

    for subject_id, group in acquisitions.groupby("subject_id", sort=False):
        ordered = group.sort_values("acquisition_order")
        alternatives = ordered["apartment_id"].tolist()
        attributes = ordered["dimension_id"].tolist()
        alternative_arc = compute_arc(alternatives)
        attribute_arc = compute_arc(attributes)
        search_index = compute_search_index(alternatives, attributes)

        first = ordered.iloc[0]
        row: dict[str, object] = {
            "subject_id": subject_id,
            "model": first["model"],
            "condition": first["condition"],
            "n_alternatives": int(first["n_alternatives"]),
            "n_dimensions": int(first["n_dimensions"]),
            "n_available_cells": (
                int(first["n_alternatives"]) * int(first["n_dimensions"])
            ),
            "n_acquisitions": len(ordered),
            "search_index": search_index.score,
            "alternative_wise_transitions": (
                search_index.alternative_wise_transitions
            ),
            "attribute_wise_transitions": search_index.attribute_wise_transitions,
            "other_transitions": search_index.other_transitions,
            "classified_transitions": search_index.classified_transitions,
            "total_transitions": search_index.total_transitions,
        }
        row.update(_prefixed_arc(alternative_arc, "arc_alternative"))
        row.update(_prefixed_arc(attribute_arc, "arc_attribute"))
        if alternative_arc.defined and attribute_arc.defined:
            row["arc_descriptive_contrast_alt_minus_attr"] = (
                alternative_arc.score - attribute_arc.score
            )
        else:
            row["arc_descriptive_contrast_alt_minus_attr"] = math.nan
        rows.append(row)

    scores = pd.DataFrame(rows)
    if sessions is None:
        return scores.sort_values(["condition", "model"]).reset_index(drop=True)

    if sessions["subject_id"].duplicated().any():
        raise ValueError("Session table contains duplicate subject_id values")
    extra_columns = [
        column
        for column in (
            "subject_id",
            "model_label",
            "tier",
            "unique_information_cells",
            "proportion_searched",
            "search_index",
        )
        if column in sessions.columns
    ]
    reference = sessions[extra_columns].rename(
        columns={"search_index": "search_index_reference"}
    )
    scores = scores.merge(
        reference,
        on="subject_id",
        how="left",
        validate="one_to_one",
    )

    if len(scores) != len(sessions):
        raise ValueError(
            f"Acquisition/session count mismatch: {len(scores)} vs {len(sessions)}"
        )
    if "unique_information_cells" in scores:
        count_mismatch = (
            scores["n_acquisitions"] != scores["unique_information_cells"]
        )
        if count_mismatch.any():
            raise ValueError(
                "Acquisition counts do not match unique_information_cells for "
                f"{int(count_mismatch.sum())} sessions"
            )
    if "search_index_reference" in scores:
        scores["search_index_delta_from_reference"] = (
            scores["search_index"] - scores["search_index_reference"]
        )
        finite_delta = scores["search_index_delta_from_reference"].dropna().abs()
        if not finite_delta.le(1e-12).all():
            raise ValueError(
                "Recomputed Search Index does not match the processed session table"
            )

    return scores.sort_values(["condition", "model"]).reset_index(drop=True)


def summarize_scores(
    scores: pd.DataFrame,
    group_columns: list[str],
) -> pd.DataFrame:
    """Create a compact descriptive summary without inferential p-values."""

    summary = (
        scores.groupby(group_columns, dropna=False, as_index=False)
        .agg(
            n_sessions=("subject_id", "count"),
            total_acquisitions=("n_acquisitions", "sum"),
            mean_proportion_searched=("proportion_searched", "mean"),
            mean_search_index=("search_index", "mean"),
            mean_arc_alternative=("arc_alternative_score", "mean"),
            mean_arc_attribute=("arc_attribute_score", "mean"),
            mean_arc_descriptive_contrast=(
                "arc_descriptive_contrast_alt_minus_attr",
                "mean",
            ),
            n_arc_alternative_defined=("arc_alternative_score", "count"),
            n_arc_attribute_defined=("arc_attribute_score", "count"),
        )
    )
    summary["n_arc_alternative_undefined"] = (
        summary["n_sessions"] - summary["n_arc_alternative_defined"]
    )
    summary["n_arc_attribute_undefined"] = (
        summary["n_sessions"] - summary["n_arc_attribute_defined"]
    )
    return summary


def metric_correlations(scores: pd.DataFrame) -> pd.DataFrame:
    """Return pairwise Pearson correlations as descriptive diagnostics."""

    metrics = [
        "proportion_searched",
        "search_index",
        "arc_alternative_score",
        "arc_attribute_score",
        "arc_descriptive_contrast_alt_minus_attr",
    ]
    rows: list[dict[str, object]] = []
    for index, metric_1 in enumerate(metrics):
        for metric_2 in metrics[index + 1 :]:
            pair = scores[[metric_1, metric_2]].dropna()
            rows.append(
                {
                    "metric_1": metric_1,
                    "metric_2": metric_2,
                    "pearson_r": (
                        pair[metric_1].corr(pair[metric_2])
                        if len(pair) >= 2
                        else math.nan
                    ),
                    "n_sessions": len(pair),
                    "note": "descriptive; repeated model-condition observations",
                }
            )
    return pd.DataFrame(rows)


def descriptive_complexity_coefficients(scores: pd.DataFrame) -> pd.DataFrame:
    """Estimate within-model changes per doubling of each set-size factor.

    These are balanced-design descriptive coefficients, not population-level
    inferential tests. Each model is fit separately:

        outcome ~ 1 + log2(n_alternatives) + log2(n_dimensions)
    """

    outcomes = [
        "proportion_searched",
        "search_index",
        "arc_alternative_score",
        "arc_attribute_score",
        "arc_descriptive_contrast_alt_minus_attr",
    ]
    rows: list[dict[str, object]] = []
    for model, model_data in scores.groupby("model", sort=True):
        for outcome in outcomes:
            complete = model_data[
                ["n_alternatives", "n_dimensions", outcome]
            ].dropna()
            if len(complete) < 3:
                continue
            design = np.column_stack(
                [
                    np.ones(len(complete)),
                    np.log2(complete["n_alternatives"].to_numpy(dtype=float)),
                    np.log2(complete["n_dimensions"].to_numpy(dtype=float)),
                ]
            )
            response = complete[outcome].to_numpy(dtype=float)
            coefficients, _, rank, _ = np.linalg.lstsq(
                design, response, rcond=None
            )
            fitted = design @ coefficients
            residual_sum_squares = float(np.square(response - fitted).sum())
            total_sum_squares = float(
                np.square(response - response.mean()).sum()
            )
            r_squared = (
                1 - residual_sum_squares / total_sum_squares
                if total_sum_squares > 0
                else math.nan
            )
            rows.append(
                {
                    "model": model,
                    "outcome": outcome,
                    "n_sessions": len(complete),
                    "intercept": coefficients[0],
                    "change_per_doubling_alternatives": coefficients[1],
                    "change_per_doubling_dimensions": coefficients[2],
                    "r_squared": r_squared,
                    "design_rank": int(rank),
                    "note": "descriptive within-model OLS; no inferential p-value",
                }
            )

    coefficients = pd.DataFrame(rows)
    if coefficients.empty:
        return coefficients
    mean_rows = (
        coefficients.groupby("outcome", as_index=False)
        .agg(
            n_sessions=("n_sessions", "sum"),
            intercept=("intercept", "mean"),
            change_per_doubling_alternatives=(
                "change_per_doubling_alternatives",
                "mean",
            ),
            change_per_doubling_dimensions=(
                "change_per_doubling_dimensions",
                "mean",
            ),
            r_squared=("r_squared", "mean"),
            design_rank=("design_rank", "min"),
        )
        .assign(
            model="mean_within_model",
            note="mean of four model-specific descriptive coefficients",
        )
    )
    return pd.concat([coefficients, mean_rows], ignore_index=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_outputs(
    scores: pd.DataFrame,
    acquisitions_path: Path,
    sessions_path: Path | None,
    output_dir: Path,
) -> dict[str, object]:
    """Write session scores, summaries, diagnostics, and run metadata."""

    output_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "arc_session_scores.csv": scores,
        "arc_by_model.csv": summarize_scores(scores, ["model", "model_label"]),
        "arc_by_condition.csv": summarize_scores(
            scores,
            ["condition", "n_alternatives", "n_dimensions"],
        ),
        "arc_by_model_and_alternatives.csv": summarize_scores(
            scores,
            ["model", "model_label", "n_alternatives"],
        ),
        "arc_by_model_and_dimensions.csv": summarize_scores(
            scores,
            ["model", "model_label", "n_dimensions"],
        ),
        "arc_by_alternatives.csv": summarize_scores(
            scores, ["n_alternatives"]
        ),
        "arc_by_dimensions.csv": summarize_scores(scores, ["n_dimensions"]),
        "metric_correlations.csv": metric_correlations(scores),
        "descriptive_complexity_coefficients.csv": (
            descriptive_complexity_coefficients(scores)
        ),
    }
    for filename, table in tables.items():
        table.to_csv(
            output_dir / filename,
            index=False,
            encoding="utf-8-sig",
            float_format="%.12g",
        )

    metadata: dict[str, object] = {
        "formula": {
            "arc": "(observed_repetitions - expected_repetitions) / "
            "(maximum_repetitions - expected_repetitions)",
            "expected_repetitions": "sum(category_count^2) / N - 1",
            "maximum_repetitions": "N - number_of_observed_categories",
            "search_index": "(alternative_wise - attribute_wise) / "
            "(alternative_wise + attribute_wise)",
        },
        "inputs": {
            "acquisitions": str(acquisitions_path.resolve()),
            "acquisitions_sha256": _sha256(acquisitions_path),
            "sessions": (
                str(sessions_path.resolve()) if sessions_path is not None else None
            ),
            "sessions_sha256": (
                _sha256(sessions_path) if sessions_path is not None else None
            ),
        },
        "validation": {
            "n_sessions": int(len(scores)),
            "n_acquisitions": int(scores["n_acquisitions"].sum()),
            "n_arc_alternative_defined": int(
                scores["arc_alternative_defined"].sum()
            ),
            "n_arc_attribute_defined": int(
                scores["arc_attribute_defined"].sum()
            ),
            "max_abs_search_index_audit_delta": (
                float(scores["search_index_delta_from_reference"].abs().max())
                if "search_index_delta_from_reference" in scores
                else None
            ),
        },
        "interpretation": {
            "arc_zero": "chance-level same-category clustering",
            "arc_one": "maximum same-category clustering for observed counts",
            "arc_negative": "less same-category clustering than expected by chance",
            "contrast": "alternative ARC minus attribute ARC; descriptive only",
            "sequence_scope": "ordered first acquisitions of unique information cells",
        },
        "outputs": list(tables),
    }
    (output_dir / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute alternative and attribute ARC from first-acquisition sequences"
        )
    )
    parser.add_argument(
        "--acquisitions",
        type=Path,
        default=DEFAULT_ACQUISITIONS,
        help="Acquisition-level CSV",
    )
    parser.add_argument(
        "--sessions",
        type=Path,
        default=DEFAULT_SESSIONS,
        help="Session-level CSV for cross-checking acquisition metadata",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory for generated CSV and JSON files",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    acquisitions = _read_csv(args.acquisitions)
    sessions = _read_csv(args.sessions)
    scores = build_session_scores(acquisitions, sessions)
    metadata = write_outputs(
        scores,
        acquisitions_path=args.acquisitions,
        sessions_path=args.sessions,
        output_dir=args.output_dir,
    )
    validation = metadata["validation"]
    print(
        "ARC analysis complete: "
        f"{validation['n_sessions']} sessions, "
        f"{validation['n_acquisitions']} acquisitions, "
        f"outputs={args.output_dir.resolve()}"
    )


if __name__ == "__main__":
    main()
