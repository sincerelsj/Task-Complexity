"""Unit tests for the standalone ARC implementation."""

from __future__ import annotations

import math
import unittest

from compute_arc import compute_arc, compute_search_index


class ComputeArcTests(unittest.TestCase):
    def test_perfect_balanced_clustering_is_one(self) -> None:
        result = compute_arc(["A", "A", "B", "B"])
        self.assertTrue(result.defined)
        self.assertEqual(result.observed_repetitions, 2)
        self.assertAlmostEqual(result.expected_repetitions, 1.0)
        self.assertAlmostEqual(result.score, 1.0)

    def test_chance_level_clustering_is_zero(self) -> None:
        result = compute_arc(["A", "B", "B", "A"])
        self.assertTrue(result.defined)
        self.assertEqual(result.observed_repetitions, 1)
        self.assertAlmostEqual(result.expected_repetitions, 1.0)
        self.assertAlmostEqual(result.score, 0.0)

    def test_below_chance_clustering_is_negative(self) -> None:
        result = compute_arc(["A", "B", "A", "B"])
        self.assertTrue(result.defined)
        self.assertEqual(result.observed_repetitions, 0)
        self.assertAlmostEqual(result.score, -1.0)

    def test_unequal_category_counts_still_reach_one(self) -> None:
        result = compute_arc(["A", "A", "A", "B", "C"])
        self.assertTrue(result.defined)
        self.assertAlmostEqual(result.expected_repetitions, 1.2)
        self.assertAlmostEqual(result.score, 1.0)

    def test_single_category_is_explicitly_undefined(self) -> None:
        result = compute_arc(["A", "A", "A"])
        self.assertFalse(result.defined)
        self.assertTrue(math.isnan(result.score))
        self.assertEqual(result.undefined_reason, "single_category_sequence")

    def test_all_unique_categories_are_explicitly_undefined(self) -> None:
        result = compute_arc(["A", "B", "C"])
        self.assertFalse(result.defined)
        self.assertTrue(math.isnan(result.score))
        self.assertEqual(result.undefined_reason, "all_categories_unique")

    def test_fewer_than_two_observations_are_undefined(self) -> None:
        result = compute_arc(["A"])
        self.assertFalse(result.defined)
        self.assertEqual(
            result.undefined_reason, "fewer_than_two_observations"
        )


class SearchIndexTests(unittest.TestCase):
    def test_alternative_wise_sequence_is_positive(self) -> None:
        result = compute_search_index(
            alternatives=["A", "A", "A", "B", "B"],
            attributes=["x", "y", "z", "x", "y"],
        )
        self.assertEqual(result.alternative_wise_transitions, 3)
        self.assertEqual(result.attribute_wise_transitions, 0)
        self.assertAlmostEqual(result.score, 1.0)

    def test_attribute_wise_sequence_is_negative(self) -> None:
        result = compute_search_index(
            alternatives=["A", "B", "C", "A"],
            attributes=["x", "x", "x", "y"],
        )
        self.assertEqual(result.alternative_wise_transitions, 0)
        self.assertEqual(result.attribute_wise_transitions, 2)
        self.assertAlmostEqual(result.score, -1.0)

    def test_unclassified_transitions_do_not_enter_denominator(self) -> None:
        result = compute_search_index(
            alternatives=["A", "A", "B"],
            attributes=["x", "y", "z"],
        )
        self.assertEqual(result.alternative_wise_transitions, 1)
        self.assertEqual(result.other_transitions, 1)
        self.assertAlmostEqual(result.score, 1.0)


if __name__ == "__main__":
    unittest.main()
