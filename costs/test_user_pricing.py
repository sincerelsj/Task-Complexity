"""Synthetic accounting fixtures; numbers are not model prices."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents._shared.pricing import estimate_usage


class UserPricingTests(unittest.TestCase):
    def setUp(self):
        self.usage = dict(token_usage_status='exact_provider', input_tokens_uncached=100,
                          output_tokens_total=10, cache_read_tokens=200, cache_creation_tokens=50)
        self.rates = {'test-model': dict(input=2, output=7, cache_read=1, cache_write=3)}

    def test_separate_cache_buckets_are_counted_once(self):
        self.assertAlmostEqual(estimate_usage(self.usage, 'test-model', self.rates)['usd'], .00062)

    def test_unconfigured_rates_are_missing(self):
        result = estimate_usage(self.usage, 'test-model', {})
        self.assertIsNone(result['usd'])
        self.assertEqual(result['status'], 'rates_not_configured')

    def test_explicit_zero_is_allowed(self):
        result = estimate_usage(self.usage, 'test-model', {'test-model': dict.fromkeys(self.rates['test-model'], 0)})
        self.assertEqual(result['usd'], 0)

    def test_missing_usage_never_becomes_free(self):
        self.usage['token_usage_status'] = 'unavailable'
        self.assertIsNone(estimate_usage(self.usage, 'test-model', self.rates)['usd'])

    def test_nonfinite_and_negative_prices_are_rejected(self):
        for value in [math.nan, math.inf, -1, True, '2']:
            self.rates['test-model']['input'] = value
            self.assertIsNone(estimate_usage(self.usage, 'test-model', self.rates)['usd'])

    def test_rates_do_not_fall_back_to_another_model(self):
        self.assertIsNone(estimate_usage(self.usage, 'different-model', self.rates)['usd'])

    def test_missing_and_negative_counts_are_rejected(self):
        for value in [None, -1, True]:
            self.usage['input_tokens_uncached'] = value
            self.assertIsNone(estimate_usage(self.usage, 'test-model', self.rates)['usd'])


if __name__ == '__main__':
    unittest.main()
