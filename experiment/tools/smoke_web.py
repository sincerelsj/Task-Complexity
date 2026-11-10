"""Check the experiment using temporary logs and no provider calls."""
import csv
import importlib.util
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    spec = importlib.util.spec_from_file_location('paper_experiment', ROOT / 'app.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as tmp:
        module.LOGS_DIR = Path(tmp)
        module.SUMMARY_CSV = Path(tmp) / 'summary.csv'
        client = module.app.test_client()
        assert client.get('/').status_code == 200
        for i in range(1, 13):
            condition = f'C{i}'
            payload = module.get_experiment_payload(condition)
            response = client.get('/experiment', query_string={'subject': f'TEST__{i}', 'condition': condition})
            assert response.status_code == 200, condition
            apt = payload['apartments'][0]['apartment_id']
            dim = payload['dimensions'][0]['column_name']
            base = {'subject_id': f'TEST__{i}', 'condition_id': condition, 'started_at_ms': i * 1000}
            result = client.post('/reveal', json={**base, 'apartment_id': apt, 'dimension_id': dim, 'elapsed_ms': 10})
            assert result.status_code == 200 and result.json['ok'], condition
            result = client.post('/submit', json={**base, 'final_choice': apt, 'total_clicks': 1, 'unique_info_viewed': 1, 'total_time_ms': 20})
            assert result.status_code == 200 and result.json['ok'], condition
            bad = client.post('/submit', json={**base, 'final_choice': 'INVALID'})
            assert bad.status_code == 400 and bad.is_json
        for route in ['/reveal', '/log', '/submit']:
            response = client.post(route, json={})
            assert response.status_code == 400 and response.is_json, route
        with module.SUMMARY_CSV.open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 12
        assert all(int(r['unique_info_viewed']) == 1 for r in rows)
        assert len(list(Path(tmp).glob('events_*.csv'))) == 12
    print('Web smoke passed: 12 boards, reveal/submit, JSON error paths, temporary logs only.')


if __name__ == '__main__':
    main()
