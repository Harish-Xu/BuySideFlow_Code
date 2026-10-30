"""Spawn-safe offline test workers. Never imported by the production runner."""
import json
import os
from pathlib import Path
import time


def write_task(qid, settings, sources):
    start = time.monotonic_ns()
    time.sleep(0.15)
    model = settings['model'].replace('/', '--')
    root = Path(settings['output_dir']) / settings['run_name'] / ('spider_agent_lite__' + model)
    target = root / qid
    target.mkdir()
    raw = {
        'finished': True, 'steps': 2, 'result': 'offline fixture',
        'submission': {'mode': 'sql', 'sql_code': 'SELECT 1', 'python_code': '', 'result_vars': []},
        'trajectory': [{'action': 'run_code\n{}\nEND_RUN_CODE'}, {'action': 'submit\n{}\nEND_SUBMIT'}],
        'adapter': {'model': settings['model'], 'run_name': settings['run_name'],
                    'db_snapshot_id': os.environ['DB_SNAPSHOT_ID'],
                    'db_snapshot_cutoff': os.environ.get('DB_SNAPSHOT_CUTOFF', ''),
                    'actual_run_code_calls': 1},
        'fixture': {'pid': os.getpid(), 'start_ns': start, 'end_ns': time.monotonic_ns()},
    }
    result = target / 'result.json'
    result.write_text(json.dumps(raw))
    return str(result)


def mixed_outcomes(qid, settings, sources):
    if qid == 'bond_003':
        raise RuntimeError('offline worker failed before saving a result')
    result = Path(write_task(qid, settings, sources))
    raw = json.loads(result.read_text())
    if qid == 'bond_002':
        raw.update(finished=False, submission=None)
        result.write_text(json.dumps(raw))
    if qid == 'bond_004':
        result.write_text('{')
        raise RuntimeError('offline truncated write')
    if qid == 'bond_005':
        raw.update(finished=False, submission=None, error='offline agent exception')
        result.write_text(json.dumps(raw))
        raise RuntimeError('offline agent exception with preserved evidence')
    return str(result)
