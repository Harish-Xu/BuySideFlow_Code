from concurrent.futures import Future
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spider_agent_lite import run_text2sql as cli
from spider_agent_lite.adapter.assets import sha256_file
from spider_agent_lite.adapter.reporting import export_run_reports
from spider_agent_lite.tests.batch_fixture import mixed_outcomes, write_task


def args(output, workers=2, run_name='batch-test'):
    return SimpleNamespace(
        model='fake/model', output_dir=str(output), run_name=run_name,
        num_workers=workers, max_steps=300, max_memory_length=25,
        max_tokens=65536, temperature=0.0, top_p=None,
        model_retry_attempts=20, model_retry_min_wait=10.0,
        model_retry_max_wait=120.0, max_run_code_calls=100,
    )


def tasks(n):
    return [{'id': f'bond_{i:03d}', 'difficulty': 'fixture'} for i in range(1, n+1)]


class BatchTests(unittest.TestCase):
    def batch_patches(self, task_list):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.dict(os.environ, {'DB_SNAPSHOT_ID': 'snapshot-v1', 'DB_SNAPSHOT_CUTOFF': ''}))
        stack.enter_context(patch.object(cli, 'load_tasks', return_value=task_list))
        stack.enter_context(patch.object(cli, 'check_installation', return_value={'runtime_dependencies_ready': True}))
        stack.enter_context(patch('spider_agent_lite.adapter.reporting.load_tasks', return_value=task_list))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return stack

    def test_spawned_workers_share_one_run_and_export_once(self):
        with tempfile.TemporaryDirectory() as temp, self.batch_patches(tasks(12)):
            options = args(temp, workers=5, run_name=None)
            with (
                patch.object(cli, '_run_batch_task', new=write_task),
                patch('spider_agent_lite.adapter.reporting.export_run_reports', wraps=export_run_reports) as export,
                patch.object(cli.multiprocessing, 'get_context', wraps=cli.multiprocessing.get_context) as context,
            ):
                self.assertEqual(cli.run_batch(options), 0)
            context.assert_called_once_with('spawn')
            export.assert_called_once()
            run_dirs = list(Path(temp).iterdir())
            self.assertEqual(len(run_dirs), 1)
            root = run_dirs[0] / 'spider_agent_lite__fake--model'
            raw = [json.loads(p.read_text()) for p in sorted(root.glob('*/result.json'))]
            self.assertEqual(len(raw), 12)
            self.assertEqual({r['adapter']['run_name'] for r in raw}, {run_dirs[0].name})
            self.assertLessEqual(len({r['fixture']['pid'] for r in raw}), 5)
            events = sorted((r['fixture'][key], change) for r in raw
                            for key, change in [('start_ns', 1), ('end_ns', -1)])
            active = peak = 0
            for _, change in events:
                active += change
                peak = max(peak, active)
            self.assertGreater(peak, 1)
            self.assertLessEqual(peak, 5)
            predictions = json.loads((root/'preds.json').read_text())
            self.assertEqual(set(predictions), {t['id'] for t in tasks(12)})
            report = next(root.glob('benchmark_*_generation.md')).read_text()
            self.assertIn('Expected tasks: 12', report)
            self.assertIn('Recorded tasks: 12', report)
            self.assertIn('NOT EVALUATED', report)
            for path in root.glob('*/result.json'):
                normalized = json.loads(path.with_name('trajectory.normalized.json').read_text())
                self.assertEqual(normalized['raw_result_sha256'], sha256_file(path))

    def test_failed_and_unsubmitted_tasks_are_not_dropped_or_retried(self):
        import csv
        with tempfile.TemporaryDirectory() as temp, self.batch_patches(tasks(5)):
            with patch.object(cli, '_run_batch_task', new=mixed_outcomes):
                self.assertEqual(cli.run_batch(args(temp)), 1)
            root = Path(temp)/'batch-test/spider_agent_lite__fake--model'
            manifest = json.loads((root/'batch_manifest.json').read_text())
            self.assertEqual(set(manifest['worker_errors']), {'bond_003', 'bond_004', 'bond_005'})
            preds = json.loads((root/'preds.json').read_text())
            self.assertEqual(len(preds), 5)
            for qid in ['bond_002', 'bond_003', 'bond_004', 'bond_005']:
                self.assertEqual(preds[qid]['model_patch'], '')
            self.assertFalse((root/'bond_003').exists())
            self.assertEqual((root/'bond_004/result.json').read_text(), '{')
            self.assertFalse((root/'bond_004/bond_004.traj').exists())
            report = next(root.glob('benchmark_*_generation.md')).read_text()
            self.assertIn('Expected tasks: 5', report)
            self.assertIn('Recorded tasks: 3', report)
            self.assertIn('Unsubmitted recorded tasks: 1', report)
            self.assertIn('Missing/unreadable raw results: 2', report)
            with next(root.glob('tool_use_*_generation.csv')).open() as stream:
                rows = {r['qid']: r for r in csv.DictReader(stream)}
            self.assertEqual(set(rows), set(preds))
            self.assertEqual(rows['bond_003']['total_turns'], '')
            self.assertEqual(rows['bond_004']['run_code_cnt'], '')
            self.assertTrue(all(r['score'] == r['passed'] == '' for r in rows.values()))

    def test_pool_startup_failure_still_reports_all_expected_tasks(self):
        with tempfile.TemporaryDirectory() as temp, self.batch_patches(tasks(3)):
            with patch.object(cli, 'ProcessPoolExecutor', side_effect=RuntimeError('offline pool error')):
                self.assertEqual(cli.run_batch(args(temp)), 1)
            root = Path(temp)/'batch-test/spider_agent_lite__fake--model'
            preds = json.loads((root/'preds.json').read_text())
            self.assertEqual(len(preds), 3)
            self.assertTrue(all(p['model_patch'] == '' for p in preds.values()))
            self.assertFalse(list(root.glob('*/*.traj')))
            self.assertIn('offline pool error', json.loads((root/'batch_manifest.json').read_text())['batch_error'])

    def test_interrupt_cancels_pending_work_before_export(self):
        with tempfile.TemporaryDirectory() as temp, self.batch_patches(tasks(3)):
            pending = [Future() for _ in tasks(3)]
            with (
                patch.object(cli, 'ProcessPoolExecutor') as executor,
                patch.object(cli, 'wait', side_effect=KeyboardInterrupt),
            ):
                pool = executor.return_value.__enter__.return_value
                pool.submit.side_effect = pending
                self.assertEqual(cli.run_batch(args(temp)), 1)
            self.assertTrue(all(future.cancelled() for future in pending[:2]))
            self.assertFalse(pending[2].cancelled())
            pool.shutdown.assert_called_once_with(wait=True, cancel_futures=True)
            root = Path(temp)/'batch-test/spider_agent_lite__fake--model'
            self.assertEqual(json.loads((root/'batch_manifest.json').read_text())['status'], 'interrupted')
            self.assertEqual(len(json.loads((root/'preds.json').read_text())), 3)

    def test_source_scope_keeps_dependencies_but_excludes_unused_ablation_assets(self):
        sources = cli._source_snapshot()
        for name in ('sweagent/text2sql/defaults.py', 'sweagent/text2sql/runtime.py',
                     'sweagent/text2sql/db_connector.py', 'sweagent/text2sql/evaluator.py',
                     'sweagent/config/litellm_openai_compat_models.json',
                     'sweagent/run/batch_instances.py', 'sweagent/agent/problem_statement.py'):
            self.assertIn(name, sources)
        for name in ('sweagent/config/text2sql_no_pit.yaml',
                     'sweagent/text2sql/assets/business_background_no_pit.txt',
                     'sweagent/text2sql/assets/fund_business_rules_no_pit.txt',
                     'sweagent/text2sql/assets/bond_business_rules_no_pit.txt',
                     'sweagent/agent/agents.py', 'sweagent/text2sql/picture.png',
                     'run_text2sql.py'):
            self.assertNotIn(name, sources)

    def test_worker_source_drift_stops_dispatch_and_persists_before_active_task_finishes(self):
        with tempfile.TemporaryDirectory() as temp, self.batch_patches(tasks(5)):
            options = args(temp)
            root = Path(temp)/'batch-test/spider_agent_lite__fake--model'
            first, active = Future(), Future()
            first.set_exception(cli.SourceDriftError('offline drift'))
            active.set_running_or_notify_cancel()
            calls = []

            def submit(fn, qid, settings, sources):
                calls.append((qid, settings, sources))
                return [first, active][len(calls)-1]

            def complete(futures, **kwargs):
                self.assertLessEqual(len(futures), 2)
                if first in futures:
                    return {first}, {active}
                ledger = json.loads((root/'batch_manifest.json').read_text())
                self.assertEqual(ledger['status'], 'stopping')
                self.assertEqual(set(ledger['worker_errors']), {'bond_001'})
                self.assertEqual(ledger['active_task_ids'], ['bond_002'])
                self.assertEqual(ledger['not_started_task_ids'], ['bond_003', 'bond_004', 'bond_005'])
                self.assertEqual(len(calls), 2)
                qid, settings, sources = calls[1]
                active.set_result(write_task(qid, settings, sources))
                return {active}, set()

            with (
                patch.object(cli, 'ProcessPoolExecutor') as executor,
                patch.object(cli, 'wait', side_effect=complete),
                patch('spider_agent_lite.adapter.reporting.export_run_reports') as export,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                executor.return_value.__enter__.return_value.submit.side_effect = submit
                self.assertEqual(cli.run_batch(options), 1)
            export.assert_not_called()
            self.assertEqual(len(calls), 2)
            ledger = json.loads((root/'batch_manifest.json').read_text())
            self.assertEqual(ledger['status'], 'provenance_error')
            self.assertEqual(ledger['active_task_ids'], [])
            self.assertEqual(ledger['not_started_task_ids'], ['bond_003', 'bond_004', 'bond_005'])
            self.assertFalse((root/'bond_003').exists())

    def test_parent_detects_drift_even_when_no_task_finishes(self):
        with tempfile.TemporaryDirectory() as temp, self.batch_patches(tasks(5)):
            queued = [Future(), Future()]
            with (
                patch.object(cli, 'ProcessPoolExecutor') as executor,
                patch.object(cli, 'wait', return_value=(set(), set(queued))) as wait,
                patch.object(cli, '_assert_sources_unchanged', side_effect=[None, cli.SourceDriftError('offline drift')]),
                patch('spider_agent_lite.adapter.reporting.export_run_reports') as export,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                executor.return_value.__enter__.return_value.submit.side_effect = queued
                self.assertEqual(cli.run_batch(args(temp)), 1)
            self.assertTrue(all(future.cancelled() for future in queued))
            wait.assert_called_once()
            export.assert_not_called()
            root = Path(temp)/'batch-test/spider_agent_lite__fake--model'
            ledger = json.loads((root/'batch_manifest.json').read_text())
            self.assertEqual(ledger['status'], 'provenance_error')
            self.assertEqual(ledger['not_started_task_ids'], [task['id'] for task in tasks(5)])

    def test_existing_batch_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp, self.batch_patches(tasks(1)):
            root = Path(temp)/'batch-test/spider_agent_lite__fake--model'
            root.mkdir(parents=True)
            sentinel = root/'old.txt'
            sentinel.write_text('existing evidence')
            with patch.object(cli, 'ProcessPoolExecutor') as pool:
                with self.assertRaises(FileExistsError):
                    cli.run_batch(args(temp))
            pool.assert_not_called()
            self.assertEqual(sentinel.read_text(), 'existing evidence')
            self.assertFalse((root/'batch_manifest.json').exists())

    def test_source_drift_blocks_exports(self):
        with tempfile.TemporaryDirectory() as temp, self.batch_patches(tasks(1)):
            with (
                patch.object(cli, 'ProcessPoolExecutor', side_effect=RuntimeError('offline pool error')),
                patch.object(cli, '_assert_sources_unchanged', side_effect=RuntimeError('offline source drift')),
                patch('spider_agent_lite.adapter.reporting.export_run_reports') as export,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(cli.run_batch(args(temp)), 1)
            export.assert_not_called()
            root = Path(temp)/'batch-test/spider_agent_lite__fake--model'
            self.assertEqual(json.loads((root/'batch_manifest.json').read_text())['status'], 'provenance_error')
            self.assertFalse((root/'preds.json').exists())

    def test_source_guard_detects_changed_and_missing_files(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(cli, 'PROJECT_ROOT', Path(temp)):
            source = Path(temp)/'source.txt'
            source.write_text('initial')
            fingerprint = {'source.txt': sha256_file(source)}
            cli._assert_sources_unchanged(fingerprint)
            source.write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'source.txt'):
                cli._assert_sources_unchanged(fingerprint)
            source.unlink()
            with self.assertRaisesRegex(RuntimeError, 'source.txt'):
                cli._assert_sources_unchanged(fingerprint)

    def test_worker_disables_only_global_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            result = Path(temp)/'result.json'
            result.write_text('{"error": ""}')
            settings = cli._run_settings(args(temp))
            with (
                patch.object(cli, '_load_environment'),
                patch.object(cli, '_assert_sources_unchanged'),
                patch('spider_agent_lite.adapter.runner.run_task', return_value=result) as run,
            ):
                self.assertEqual(cli._run_batch_task('bond_001', settings, {}), str(result))
            run.assert_called_once_with('bond_001', **settings, export_reports=False)

    def test_task_workspace_isolates_outputs_and_restores_process_state(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = (Path(temp)/'task-workspace').resolve()
            original_cwd = Path.cwd()
            original_temp = tempfile.tempdir
            original_env = {name: os.environ.get(name) for name in ['TMPDIR', 'TEMP', 'TMP']}
            with self.assertRaisesRegex(RuntimeError, 'offline failure'):
                with cli._task_workspace(workspace):
                    self.assertEqual(Path.cwd(), workspace)
                    self.assertEqual(tempfile.gettempdir(), str(workspace/'.tmp'))
                    self.assertTrue(all(os.environ[name] == str(workspace/'.tmp') for name in original_env))
                    Path('same-name.csv').write_text('isolated output')
                    raise RuntimeError('offline failure')
            self.assertEqual(Path.cwd(), original_cwd)
            self.assertEqual(tempfile.tempdir, original_temp)
            self.assertEqual({name: os.environ.get(name) for name in original_env}, original_env)
            self.assertEqual((workspace/'same-name.csv').read_text(), 'isolated output')

    def test_runner_save_only_preserves_original_task_artifacts(self):
        from spider_agent_lite.adapter.runner import run_task
        submission = {'mode': 'sql', 'sql_code': 'SELECT 1', 'python_code': '', 'result_vars': []}
        problem = SimpleNamespace(id='save_only_test', question='offline fixture', extra_fields={})

        class FakeAgent:
            def set_env_and_task(self, env):
                self.env = env

            def run(self):
                self.env.submission = submission
                return True, json.dumps(submission)

            def get_trajectory(self):
                return {'trajectory': [{'action': 'submit'}]}

        with (
            tempfile.TemporaryDirectory() as temp,
            patch('spider_agent_lite.adapter.runner.require_db_snapshot_id', return_value='snapshot-v1'),
            patch('spider_agent_lite.adapter.runner.build_problem_statement', return_value=problem),
            patch('spider_agent_lite.adapter.runner.create_agent', return_value=FakeAgent()),
            patch('spider_agent_lite.adapter.runner.export_run_reports') as export,
        ):
            result = run_task(problem.id, model='fake/model', output_dir=temp, export_reports=False)
            self.assertEqual(json.loads(result.read_text())['submission'], submission)
            self.assertTrue(result.with_name('submission.json').is_file())
            self.assertTrue(result.with_name('trajectory.normalized.json').is_file())
            self.assertFalse((result.parent.parent/'preds.json').exists())
            export.assert_not_called()

    def test_runtime_dependencies_fail_before_creating_batch(self):
        with tempfile.TemporaryDirectory() as temp, self.batch_patches(tasks(1)):
            with patch.object(cli, 'check_installation', return_value={
                'runtime_dependencies_ready': False, 'missing_runtime_dependencies': ['offline-sdk'],
            }):
                with self.assertRaisesRegex(RuntimeError, 'offline-sdk'):
                    cli.run_batch(args(temp))
            self.assertFalse(list(Path(temp).iterdir()))

    def test_batch_errors_do_not_copy_environment_secrets(self):
        with patch.dict(os.environ, {'EXAMPLE_API_KEY': 'offline-secret'}):
            text = cli._safe_error(RuntimeError('failure offline-secret'))
        self.assertNotIn('offline-secret', text)
        self.assertIn('[REDACTED]', text)


class CliTests(unittest.TestCase):
    def test_batch_flag_and_both_worker_aliases(self):
        for alias in ['--num_workers', '--num-workers']:
            with (
                patch.object(sys, 'argv', ['run_text2sql.py', '--all', alias, '5']),
                patch.object(cli, '_load_environment'),
                patch.object(cli, 'run_batch', return_value=0) as batch,
            ):
                self.assertEqual(cli.main(), 0)
                self.assertEqual(batch.call_args.args[0].num_workers, 5)

    def test_single_task_keeps_original_defaults(self):
        with (
            patch.object(sys, 'argv', ['run_text2sql.py', '--task', 'bond_001']),
            patch.object(cli, '_load_environment'),
            patch.object(cli, 'run_batch') as batch,
            patch('spider_agent_lite.adapter.runner.run_task', return_value=Path('result.json')) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(cli.main(), 0)
        batch.assert_not_called()
        self.assertEqual(run.call_args.kwargs['max_steps'], 300)
        self.assertEqual(run.call_args.kwargs['max_run_code_calls'], 100)
        self.assertEqual(run.call_args.kwargs['max_tokens'], 65536)
        self.assertIsNone(run.call_args.kwargs['top_p'])
        self.assertNotIn('export_reports', run.call_args.kwargs)

    def test_invalid_modes_and_worker_counts_do_not_start_work(self):
        for flags in [[], ['--all', '--task', 'bond_001'], ['--all', '--check'],
                      ['--all', '--num_workers', '0'], ['--all', '--num-workers', '-1'],
                      ['--task', 'bond_001', '--num_workers', '5']]:
            with (
                patch.object(sys, 'argv', ['run_text2sql.py', *flags]),
                patch.object(cli, '_load_environment') as environment,
                patch.object(cli, 'run_batch') as batch,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit) as error:
                    cli.main()
                self.assertEqual(error.exception.code, 2)
                environment.assert_not_called()
                batch.assert_not_called()


class CoverageTests(unittest.TestCase):
    def write_result(self, root, qid='bond_001', *, run_name='coverage', snapshot='snapshot-v1', model='fake/model'):
        target = root/qid
        target.mkdir()
        raw = {'finished': True, 'submission': {'mode': 'sql', 'sql_code': 'SELECT 1', 'python_code': '', 'result_vars': []},
               'trajectory': [{'action': 'run_code'}, {'action': 'submit'}],
               'adapter': {'model': model, 'run_name': run_name, 'db_snapshot_id': snapshot,
                           'db_snapshot_cutoff': '', 'actual_run_code_calls': 1}}
        path = target/'result.json'
        path.write_text(json.dumps(raw))
        return path

    def write_manifest(self, root, ids, **overrides):
        manifest = {'schema': 'buysideflow-spider-batch/v1', 'status': 'finished',
                    'model': 'fake/model', 'run_name': 'coverage', 'db_snapshot_id': 'snapshot-v1',
                    'db_snapshot_cutoff': '', 'expected_task_ids': ids, 'sources': {}, 'worker_errors': {}, 'num_workers': 5}
        manifest.update(overrides)
        (root/'batch_manifest.json').write_text(json.dumps(manifest))

    def test_full_canonical_coverage_does_not_fabricate_missing_submissions(self):
        import csv
        ids = [task['id'] for task in cli.load_tasks()]
        self.assertEqual(len(ids), 408)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = self.write_result(root)
            original = raw.read_bytes()
            self.write_manifest(root, ids)
            report = export_run_reports(root)
            self.assertIn('Expected tasks: 408', report.read_text())
            self.assertIn('Recorded tasks: 1', report.read_text())
            self.assertIn('Missing/unreadable raw results: 407', report.read_text())
            preds = json.loads((root/'preds.json').read_text())
            self.assertEqual(set(preds), set(ids))
            self.assertEqual(sum(bool(p['model_patch']) for p in preds.values()), 1)
            self.assertEqual(len(list(root.glob('*/*.traj'))), 1)
            self.assertEqual(raw.read_bytes(), original)
            with next(root.glob('tool_use_*_generation.csv')).open() as stream:
                records = list(csv.DictReader(stream))
            self.assertEqual(len(records), 408)
            self.assertTrue(all(r['total_turns'] == '' for r in records if r['qid'] != 'bond_001'))

    def test_mixed_runs_models_and_snapshots_rejected_before_exports(self):
        for changes in [{'run_name': 'other'}, {'snapshot': 'other'}, {'model': 'other'}]:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.write_result(root)
                self.write_result(root, 'bond_002', **changes)
                with self.assertRaisesRegex(ValueError, 'different models, runs, or database snapshots'):
                    export_run_reports(root, expected_task_ids=['bond_001', 'bond_002'])
                self.assertFalse((root/'preds.json').exists())
                self.assertFalse(list(root.glob('*/*.traj')))

    def test_snapshot_manifest_mismatch_is_not_silently_relabelled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = self.write_result(root, snapshot='other')
            original = raw.read_bytes()
            self.write_manifest(root, ['bond_001'])
            with self.assertRaisesRegex(ValueError, 'different models, runs, or database snapshots'):
                export_run_reports(root)
            self.assertEqual(raw.read_bytes(), original)
            self.assertFalse((root/'preds.json').exists())

    def test_running_and_invalid_provenance_batches_are_not_exported(self):
        for status in ['running', 'stopping', 'provenance_error']:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.write_manifest(root, ['bond_001'], status=status)
                with self.assertRaises(ValueError):
                    export_run_reports(root)
                self.assertFalse((root/'preds.json').exists())

    def test_changed_manifest_sources_block_backfill(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'public.txt'
            source.write_text('initial public input')
            self.write_manifest(root, ['bond_001'], sources={'public.txt': sha256_file(source)})
            source.write_text('changed input')
            with patch('spider_agent_lite.adapter.reporting.PROJECT_ROOT', root):
                with self.assertRaisesRegex(ValueError, 'Batch source changed'):
                    export_run_reports(root)
            self.assertFalse((root/'preds.json').exists())

    def test_extra_or_duplicate_expected_ids_are_not_merged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_result(root)
            for ids in [['bond_001', 'bond_001'], ['../bond_001'], ['bond_002']]:
                with self.assertRaises(ValueError):
                    export_run_reports(root, expected_task_ids=ids)
            self.assertFalse((root/'preds.json').exists())

    def test_completed_batch_backfill_preserves_raw_and_scored_reports(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = self.write_result(root)
            original = raw.read_bytes()
            self.write_manifest(root, ['bond_001', 'bond_002'])
            report = export_run_reports(root)
            self.assertIn('--report-only', report.read_text())
            self.assertIn(' && ', report.read_text())
            scored = root/report.name.replace('_generation.md', '.md')
            tools = root/report.name.replace('benchmark_', 'tool_use_').replace('_generation.md', '.csv')
            scored.write_text('existing independent scores')
            tools.write_text('existing independent tool-use scores')
            export_run_reports(root)
            self.assertEqual(raw.read_bytes(), original)
            self.assertEqual(scored.read_text(), 'existing independent scores')
            self.assertEqual(tools.read_text(), 'existing independent tool-use scores')


if __name__ == '__main__':
    unittest.main()
