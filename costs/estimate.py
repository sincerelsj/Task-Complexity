"""Estimate normalized token costs with explicitly entered per-model rates."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agents._shared.pricing import BUCKETS, estimate_usage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', type=Path, required=True)
    parser.add_argument('--rates', type=Path, help='User-created JSON keyed by full model ID')
    args = parser.parse_args()
    try:
        rows = [json.loads(line) for line in args.ledger.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        if not rows:
            raise ValueError('Empty token ledger')
        if any(not isinstance(r, dict) or not str(r.get('token_usage_status', '')).startswith('exact') for r in rows):
            raise ValueError('Ledger contains missing or non-exact usage')
        models = sorted({r['model'] for r in rows})
        if args.rates:
            rates = json.loads(args.rates.read_text(encoding='utf-8-sig'))
        else:
            rates = {}
            for model in models:
                rates[model] = {}
                print(f'Enter your USD prices per 1 million tokens for {model}.')
                for count_field, rate_field in BUCKETS.items():
                    if any(r.get(count_field, 0) for r in rows if r['model'] == model):
                        rates[model][rate_field] = float(input(f'{rate_field}: '))
        totals = {}
        for row in rows:
            result = estimate_usage(row, row['model'], rates)
            if result['usd'] is None:
                raise ValueError('Cannot estimate: ' + result['status'])
            totals[row['model']] = totals.get(row['model'], 0) + result['usd']
        print(json.dumps({'status': 'token_only_estimate', 'models_usd': totals,
                          'total_usd': sum(totals.values()), 'calls': len(rows)}, indent=2))
        return 0
    except (ValueError, KeyError, OSError, EOFError, TypeError):
        print('Could not estimate: check normalized usage and explicit non-negative user rates.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
