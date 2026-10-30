"""Offline continuation checks: no provider or database calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from spider_agent_lite import run_text2sql as cli
from spider_agent_lite.adapter import resume
from spider_agent_lite.adapter.actions import ACTION_CLASSES
from spider_agent_lite.adapter.assets import PROJECT_ROOT, shared_asset_manifest, sha256_file
from spider_agent_lite.adapter.problem import build_problem_statement
from spider_agent_lite.adapter.prompt import build_system_prompt
from spider_agent_lite.adapter.reporting import export_run_reports
from spider_agent_lite.adapter.runner import _runtime_versions
from spider_agent_lite.tests.batch_fixture import write_task
from spider_agent_lite.tests.test_batch import args, tasks


def compatible_task(qid, settings, sources):
    """Rich offline result, suitable for a later segment's compatibility check."""
    result = Path(write_task(qid, settings, sources))
    raw = json.loads(result.read_text())
    problem = build_problem_statement(qid)
    raw.update(Task=problem.question, system_message=build_system_prompt(
        problem, ''.join(cls.get_action_description() for cls in ACTION_CLASSES), settings['max_steps']))
    raw['adapter'].update({key: settings[key] for key in resume.BUDGET_KEYS})
    raw['adapter'].update(
        requested_model=settings['model'], shared_assets=shared_asset_manifest(),
        runtime_packages=_runtime_versions(), python_version=sys.version.split()[0],
        platform=platform.platform(), finished_at='2026-10-06',
        upstream_commit=json.loads((PROJECT_ROOT/'spider_agent_lite/UPSTREAM_MANIFEST.json').read_text())['commit'],
    )
    result.write_text(json.dumps(raw))
    return str(result)


def first_partial(qid, settings, sources):
    if qid in {'bond_004', 'bond_005'}:
        raise RuntimeError('offline failure before task started')
    return compatible_task(qid, settings, sources)


def second_partial(qid, settings, sources):
    if qid == 'bond_005':
        raise RuntimeError('offline failure before task started')
    return compatible_task(qid, settings, sources)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {'DB_SNAPSHOT_ID': 'snapshot-v1', 'DB_SNAPSHOT_CUTOFF': ''}))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.options = args(self.temp.name, workers=2)
        self.settings = cli._run_settings(self.options)
        self.settings['output_dir'] = str(Path(self.temp.name).resolve())
        self.ids = [t['id'] for t in tasks(5)]
        self.root = Path(self.temp.name)/'batch-test/spider_agent_lite__fake--model'
        self.root.mkdir(parents=True)
        self.sources = cli._source_snapshot()
        self.archive = Path(self.temp.name)/'rules.json'
        self.archive.write_bytes((PROJECT_ROOT/'data/dataset/comparison_rules.json').read_bytes())
        self.original = {
            'schema': 'buysideflow-spider-batch/v1', 'status': 'provenance_error',
            'settings': self.settings, 'sources': self.sources, 'model': 'fake/model',
            'run_name': 'batch-test', 'db_snapshot_id': 'snapshot-v1', 'db_snapshot_cutoff': '',
            'expected_task_ids': self.ids, 'worker_errors': {qid: 'historical guard' for qid in self.ids[2:]},
        }
        self.save_original()
        for qid in self.ids[:2]:
            result = Path(write_task(qid, self.settings, self.sources))
            raw = json.loads(result.read_text())
            problem = build_problem_statement(qid)
            raw.update(Task=problem.question, system_message=build_system_prompt(
                problem, ''.join(cls.get_action_description() for cls in ACTION_CLASSES), 300))
            raw['adapter'].update({key: self.settings[key] for key in resume.BUDGET_KEYS})
            raw['adapter'].update(
                requested_model='fake/model', shared_assets=shared_asset_manifest(),
                runtime_packages=_runtime_versions(), python_version=sys.version.split()[0],
                platform=platform.platform(), finished_at='2026-10-06',
                upstream_commit=json.loads((PROJECT_ROOT/'spider_agent_lite/UPSTREAM_MANIFEST.json').read_text())['commit'],
            )
            if qid == self.ids[1]:
                raw.update(submission=None, finished=False)
            result.write_text(json.dumps(raw))
            (result.parent/'trajectory.normalized.json').write_text('preserve this historical normalization\n')
        self.before = {qid: resume.file_inventory(self.root/qid) for qid in self.ids[:2]}
        self.original_sha = sha256_file(self.root/'batch_manifest.json')

    def save_original(self):
        (self.root/'batch_manifest.json').write_text(json.dumps(self.original))

    def prepare(self, sources=None, settings=None):
        return resume.prepare_resume(self.root, settings or self.settings,
                                     sources or self.sources, self.ids, 'snapshot-v1', self.archive)

    def mutate_raw(self, qid, fn):
        path = self.root/qid/'result.json'
        raw = json.loads(path.read_text())
        fn(raw)
        path.write_text(json.dumps(raw))

    def run_segment(self, worker):
        self.options.resume_from = str(self.root)
        self.options.resume_rules_archive = str(self.archive)
        with (
            patch.object(cli, 'load_tasks', return_value=tasks(5)),
            patch.object(cli, 'check_installation', return_value={'runtime_dependencies_ready': True}),
            patch.object(cli, '_run_batch_task', new=worker),
            patch('spider_agent_lite.adapter.reporting.load_tasks', return_value=tasks(5)),
        ):
            return cli.run_batch(self.options)

    def test_three_continuations_preserve_each_segment_and_generate_only_missing_tasks(self):
        # q3's gold changed before it was first generated. Later reuse must bind
        # to segment 1's gold, not the original batch's obsolete q3 fingerprint.
        self.original['sources'] = dict(self.sources)
        self.original['sources']['data/dataset/results/bond_003/refer.sql'] = '0'*64
        self.save_original()
        self.original_sha = sha256_file(self.root/'batch_manifest.json')
        self.assertEqual(self.run_segment(first_partial), 1)
        first = self.root/'resume_manifest.json'
        first_hash = sha256_file(first)
        first_raw = sha256_file(self.root/'bond_003/result.json')
        self.assertEqual(self.run_segment(second_partial), 1)
        second = self.root/'resume_manifest.002.json'
        second_hash = sha256_file(second)
        ledger = json.loads(second.read_text())
        self.assertEqual(ledger['scheduled_task_ids'], ['bond_004', 'bond_005'])
        self.assertEqual(ledger['task_origins']['bond_003'], first.name)
        self.assertEqual(ledger['history_manifests'], [{'path': first.name, 'sha256': first_hash}])
        self.assertEqual(self.run_segment(compatible_task), 0)
        third = self.root/'resume_manifest.003.json'
        ledger = json.loads(third.read_text())
        self.assertEqual(ledger['scheduled_task_ids'], ['bond_005'])
        self.assertEqual(ledger['task_origins']['bond_004'], second.name)
        self.assertEqual(ledger['task_origins']['bond_005'], third.name)
        self.assertEqual(sha256_file(first), first_hash)
        self.assertEqual(sha256_file(second), second_hash)
        self.assertEqual(sha256_file(self.root/'bond_003/result.json'), first_raw)
        self.assertEqual(sha256_file(self.root/'batch_manifest.json'), self.original_sha)
        for qid, inventory in self.before.items():
            current = resume.file_inventory(self.root/qid)
            self.assertTrue(all(current[name] == digest for name, digest in inventory.items()))
        with patch('spider_agent_lite.adapter.reporting.load_tasks', return_value=tasks(5)):
            report = export_run_reports(self.root).read_text()
        self.assertIn('Generation source segments: 4', report)
        self.assertIn('resume_manifest.003.json', report)
        self.assertEqual(len(json.loads((self.root/'preds.json').read_text())), 5)
        with self.assertRaises(FileExistsError):
            self.prepare()
        first.write_text(first.read_text()+'\n')
        with self.assertRaisesRegex(ValueError, 'Historical continuation ledger'):
            export_run_reports(self.root)

    def test_legacy_first_ledger_can_continue_without_rewriting_it(self):
        self.assertEqual(self.run_segment(first_partial), 1)
        path = self.root/'resume_manifest.json'
        ledger = json.loads(path.read_text())
        for key in ('manifest_name', 'history_manifests', 'generation_rule_hashes'):
            del ledger[key]
        ledger['task_origins'] = {qid: 'original_batch' if qid in ledger['reused_task_ids']
                                 else 'continuation' for qid in self.ids}
        ledger['status'] = 'provenance_error'
        path.write_text(json.dumps(ledger))
        before = sha256_file(path)
        next_segment = self.prepare()
        self.assertEqual(next_segment['manifest_name'], 'resume_manifest.002.json')
        self.assertEqual(next_segment['reused_task_ids'], self.ids[:3])
        self.assertEqual(next_segment['scheduled_task_ids'], self.ids[3:])
        self.assertEqual(sha256_file(path), before)

    def test_second_resume_rejects_active_segment_corruption_or_rule_drift(self):
        self.assertEqual(self.run_segment(first_partial), 1)
        path = self.root/'resume_manifest.json'
        text = path.read_text()
        ledger = json.loads(text)
        ledger['status'] = 'stopping'
        path.write_text(json.dumps(ledger))
        with self.assertRaises(FileExistsError):
            self.prepare()
        ledger['status'] = 'failed'
        ledger['generation_rule_hashes']['bond_003'] = '0'*64
        path.write_text(json.dumps(ledger))
        with self.assertRaisesRegex(ValueError, 'Comparison rules'):
            self.prepare()
        path.write_text(text)
        self.mutate_raw('bond_003', lambda raw: raw.update(Task='corrupt'))
        with self.assertRaisesRegex(ValueError, 'Continuation raw results'):
            self.prepare()

    def test_ledger_gaps_and_unowned_saved_results_are_rejected(self):
        (self.root/'resume_manifest.002.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'gap'):
            self.prepare()
        (self.root/'resume_manifest.002.json').unlink()
        self.assertEqual(self.run_segment(first_partial), 1)
        compatible_task('bond_004', self.settings, self.sources)
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.prepare()

    def crashed_fixture(self):
        ledger = self.prepare()
        path = self.root/'resume_manifest.json'
        path.write_text(json.dumps(ledger))
        compatible_task('bond_003', self.settings, self.sources)
        workspace = self.root/'.workspaces/bond_004'
        workspace.mkdir(parents=True)
        (workspace/'partial.txt').write_text('preserve interrupted evidence')
        (self.root/'bond_004').mkdir()
        return path

    def test_crash_recovery_seals_results_without_rewriting_active_ledger(self):
        path = self.crashed_fixture()
        before = sha256_file(path)
        with patch.object(resume.subprocess, 'run', return_value=SimpleNamespace(stdout='PID PPID COMMAND\n')):
            with self.assertRaisesRegex(ValueError, 'approval'):
                resume.recover_crashed_segment(self.root)
            receipt = resume.recover_crashed_segment(self.root, restart_incomplete=True)
        self.assertEqual(sha256_file(path), before)
        self.assertFalse((self.root/'.workspaces/bond_004').exists())
        record = json.loads(receipt.read_text())
        self.assertEqual(record['restarted_incomplete_task_ids'], ['bond_004'])
        self.assertEqual((self.root/record['preserved_partial_artifacts']['bond_004'][1]['path']/'partial.txt').read_text(), 'preserve interrupted evidence')
        next_segment = self.prepare()
        self.assertEqual(next_segment['reused_task_ids'], self.ids[:3])
        self.assertEqual(next_segment['scheduled_task_ids'], self.ids[3:])
        self.assertEqual(next_segment['recovery_records'], [{'path': receipt.name, 'sha256': sha256_file(receipt)}])
        self.mutate_raw('bond_003', lambda raw: raw.update(Task='corrupted after recovery'))
        with self.assertRaisesRegex(ValueError, 'Continuation raw results'):
            self.prepare()

    def test_crash_recovery_refuses_a_live_process(self):
        path = self.crashed_fixture()
        before = sha256_file(path)
        with patch.object(resume.subprocess, 'run', return_value=SimpleNamespace(
                stdout='PID PPID COMMAND\n99999999 1 python run_text2sql.py --run-name batch-test\n')):
            with self.assertRaisesRegex(RuntimeError, 'still active'):
                resume.recover_crashed_segment(self.root, restart_incomplete=True)
        self.assertEqual(sha256_file(path), before)
        self.assertFalse(path.with_suffix('.recovery.json').exists())
        self.assertTrue((self.root/'.workspaces/bond_004/partial.txt').exists())

    def test_gpt_only_gateway_addition_must_reconstruct_exact_deepseek_bytes(self):
        import hashlib
        prefix = 'from __future__ import annotations\n\ndef call_current_deepseek(payload):\n    return True, "same"\n'
        install = ('\n\ndef install_model_gateway(module):\n'
                   '    """Route DeepSeek requests through the current benchmark model backend."""\n'
                   '    def routed_call_llm(payload):\n'
                   '        if model.startswith("deepseek/"):\n'
                   '            return call_current_deepseek(routed_payload)\n'
                   '        return original_call_llm(routed_payload)\n')
        historical = prefix + install
        expected = hashlib.sha256(historical.encode()).hexdigest()
        current = (prefix + '\n\ndef call_current_gpt55(payload):\n    return True, "GPT"\n' + install)
        current = current.replace('Route DeepSeek requests through the current benchmark model backend.',
                                  'Route models needing benchmark-specific API parameters.')
        current = current.replace('        return original_call_llm',
                                  '        if model == "gpt-5.5":\n            return call_current_gpt55(routed_payload)\n        return original_call_llm')
        self.assertTrue(resume._gpt_only_addition_matches(current, expected))
        self.assertFalse(resume._gpt_only_addition_matches(current.replace('"same"', '"changed"'), expected))
        self.assertFalse(resume._gpt_only_addition_matches(current.replace('\n\ndef install_model_gateway',
                            '\nraise RuntimeError("side effect")\n\ndef install_model_gateway'), expected))

    def test_historical_scalar_comparison_is_process_local_and_hash_verified(self):
        import pandas as pd
        from sweagent.text2sql import semantic_comparison as module
        historical = 'eebd8d7ef513d82153b2a7bbbd8809a815c3c5e3f82f3f6e4294582193281a9e'
        path = PROJECT_ROOT/'sweagent/text2sql/semantic_comparison.py'
        before = sha256_file(path)
        current_function = module._record
        frames = [pd.DataFrame([1, 2], columns=['0'])]
        rule = {'record': True, 'columns': [{'name': 'a'}, {'name': 'b'}], 'scalar_order': ['a', 'b']}
        self.assertEqual(current_function(frames, rule)[0].shape, (1, 2))
        try:
            resume.install_historical_comparison(historical)
            self.assertEqual(module._record(frames, rule)[0].shape, (2, 1))
            self.assertEqual(sha256_file(path), before)
        finally:
            module._record = current_function

    def test_crash_recovery_can_defer_partial_tasks_and_run_only_untouched_tasks(self):
        path = self.crashed_fixture()
        before = sha256_file(path)
        self.options.recover_crashed = True
        self.options.defer_incomplete = True
        with patch.object(resume.subprocess, 'run', return_value=SimpleNamespace(stdout='PID PPID COMMAND\n')):
            self.assertEqual(self.run_segment(compatible_task), 0)
        self.assertEqual(sha256_file(path), before)
        ledger = json.loads((self.root/'resume_manifest.002.json').read_text())
        self.assertEqual(ledger['deferred_task_ids'], ['bond_004'])
        self.assertFalse((self.root/'bond_004/result.json').exists())
        self.assertTrue((self.root/'bond_005/result.json').exists())

    def test_spawn_continuation_skips_all_saved_including_unsubmitted(self):
        self.options.resume_from = str(self.root)
        self.options.resume_rules_archive = str(self.archive)
        with (
            patch.object(cli, 'load_tasks', return_value=tasks(5)),
            patch.object(cli, 'check_installation', return_value={'runtime_dependencies_ready': True}),
            patch.object(cli, '_run_batch_task', new=write_task),
            patch('spider_agent_lite.adapter.reporting.load_tasks', return_value=tasks(5)),
        ):
            self.assertEqual(cli.run_batch(self.options), 0)
            self.assertEqual(sha256_file(self.root/'batch_manifest.json'), self.original_sha)
            for qid, inventory in self.before.items():
                current = resume.file_inventory(self.root/qid)
                self.assertTrue(all(current[name] == digest for name, digest in inventory.items()))
            ledger = json.loads((self.root/'resume_manifest.json').read_text())
            self.assertEqual(ledger['reused_task_ids'], self.ids[:2])
            self.assertEqual(ledger['scheduled_task_ids'], self.ids[2:])
            self.assertEqual(ledger['status'], 'finished')
            preds = json.loads((self.root/'preds.json').read_text())
            self.assertEqual(list(preds), self.ids)
            self.assertEqual(preds['bond_002']['model_patch'], '')
            report = next(self.root.glob('benchmark_*_generation.md')).read_text()
            self.assertIn('Reused historical tasks: 2', report)
            self.assertIn('Newly scheduled tasks: 3', report)
            self.assertIn('Generation source segments: 2', report)
            self.assertIn('NOT EVALUATED', report)
            with self.assertRaises(FileExistsError):
                cli.run_batch(self.options)
            (self.root/'bond_003/result.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'Continuation raw results'):
                export_run_reports(self.root)

    def test_reject_changed_prompt_budget_runtime_or_snapshot(self):
        for mutation in [
            lambda raw: raw.update(system_message='changed'),
            lambda raw: raw.update(Task='changed'),
            lambda raw: raw['adapter'].update(max_steps=301),
            lambda raw: raw['adapter'].update(runtime_packages={}),
            lambda raw: raw['adapter'].update(db_snapshot_id='other'),
            lambda raw: raw.update(error='partial run'),
        ]:
            with self.subTest(mutation=mutation):
                path = self.root/'bond_001/result.json'
                before = path.read_bytes()
                self.mutate_raw('bond_001', mutation)
                with self.assertRaises(ValueError):
                    self.prepare()
                path.write_bytes(before)
        changed = dict(self.settings, max_tokens=1)
        with self.assertRaisesRegex(ValueError, 'budgets'):
            self.prepare(settings=changed)

    def test_reject_rules_archive_or_saved_rule_drift(self):
        self.archive.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.prepare()
        self.original['sources'] = dict(self.sources)
        self.original['sources']['data/dataset/comparison_rules.json'] = sha256_file(self.archive)
        self.save_original()
        with self.assertRaisesRegex(ValueError, 'Comparison rules'):
            self.prepare()

    def test_reject_saved_reference_or_generation_source_drift(self):
        for name in ['data/dataset/results/bond_001/refer.sql', 'spider_agent_lite/adapter/agent.py']:
            altered = dict(self.sources)
            altered[name] = '0'*64
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.prepare(sources=altered)

    def test_pending_reference_changes_are_recorded_not_rebound(self):
        # The archived original is allowed to differ only for untouched tasks.
        self.original['sources'] = dict(self.sources)
        name = 'data/dataset/results/bond_003/refer.sql'
        self.original['sources'][name] = '0'*64
        self.save_original()
        ledger = self.prepare()
        self.assertEqual(ledger['source_changes']['original_batch'][name]['original'], '0'*64)
        self.assertEqual(ledger['source_changes']['original_batch'][name]['current'], self.sources[name])
        self.assertEqual(json.loads((self.root/'batch_manifest.json').read_text())['sources'][name], '0'*64)

    def test_reject_partial_pending_unknown_task_or_running_original(self):
        (self.root/'.workspaces/bond_003').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'partial'):
            self.prepare()
        (self.root/'.workspaces/bond_003').rmdir()
        (self.root/'unknown').mkdir()
        (self.root/'unknown/result.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            self.prepare()
        (self.root/'unknown/result.json').unlink()
        self.original['status'] = 'running'
        self.save_original()
        with self.assertRaisesRegex(ValueError, 'stopped'):
            self.prepare()

    def test_report_rejects_running_ledger_and_changed_original_or_reused_bytes(self):
        ledger = self.prepare()
        path = self.root/'resume_manifest.json'
        path.write_text(json.dumps(ledger))
        with self.assertRaisesRegex(ValueError, 'running'):
            export_run_reports(self.root)
        ledger['status'] = 'finished'
        path.write_text(json.dumps(ledger))
        (self.root/'bond_002/trajectory.normalized.json').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'Reused evidence'):
            export_run_reports(self.root)
        (self.root/'batch_manifest.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'Original batch'):
            export_run_reports(self.root)

    def test_bad_resume_partition_is_rejected(self):
        ledger = self.prepare()
        ledger['status'] = 'finished'
        ledger['scheduled_task_ids'].append('bond_001')
        with self.assertRaisesRegex(ValueError, 'coverage'):
            resume.validate_resume(self.root, ledger)


if __name__ == '__main__':
    unittest.main()
