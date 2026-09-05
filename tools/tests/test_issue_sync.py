"""Issue同期の冪等性と既存進捗保持。ネットワークへの書込は行わない。"""
import contextlib
import io
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'issues'))
import sync_github_issues as sync
import sync_project as project


class IssueSyncTests(unittest.TestCase):
    def test_plan_doc_second_run_is_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'docs').mkdir()
            with patch.object(sync, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                sync.update_plan_doc({})
                once = (root/'docs/build_plan.md').read_bytes()
                sync.update_plan_doc({})
                self.assertEqual(once, (root/'docs/build_plan.md').read_bytes())

    def test_duplicate_remote_key_refuses_overwrite(self):
        issues = [dict(number=n, node_id=str(n), title='task', state='open',
                       body='<!-- tachikoma-key: L-01 -->') for n in (1, 2)]
        with patch.object(sync, 'api', return_value=issues):
            with self.assertRaisesRegex(RuntimeError, 'Issueキー重複'):
                sync.load_existing()

    def test_manual_status_is_preserved(self):
        spec = dict(key='A', labels=[], blocked_by=['B'])
        for current in ('In Progress', 'Done', 'Blocked', 'Ready', 'Todo'):
            self.assertIsNone(project.status_change(current, spec, {'A':'open', 'B':'open'}))

    def test_ready_uses_actual_closed_dependencies(self):
        spec = dict(key='A', labels=[], blocked_by=['B'])
        self.assertEqual(project.initial_status(spec, {'A':'open','B':'closed'}), 'Ready')
        self.assertEqual(project.initial_status(spec, {'A':'open','B':'open'}), 'Blocked')
        self.assertEqual(project.initial_status(spec, {'A':'open'}), 'Blocked')
        self.assertEqual(project.initial_status(spec, {'A':'closed','B':'open'}), 'Done')

    def test_dry_run_does_not_invent_existing_relations(self):
        spec = dict(key='A', parent='P', blocked_by=['B'])
        existing = {k:dict(number=n, node_id=str(n)) for k,n in [('A',1),('P',2),('B',3)]}
        relations = {1:dict(parent=2, blocked_by={3})}
        with patch.object(sync, 'fetch_relations', return_value=relations), contextlib.redirect_stdout(io.StringIO()) as out:
            sync.sync_relations(True, existing, [spec])
        self.assertEqual(out.getvalue(), '')

    def test_conflicting_modes_fail_before_network(self):
        result = subprocess.run([sys.executable, sync.__file__, '--dry-run', '--apply'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('not allowed with argument', result.stderr)

    def test_reconcile_preserves_retired_key_dependencies(self):
        spec = dict(key='A', parent=None, blocked_by=[])
        existing = {k:dict(number=n,node_id=str(n)) for k,n in [('A',1),('B',2),('RETIRED',3)]}
        relations = {1:dict(parent=None,blocked_by={2,3})}
        with patch.object(sync.plan,'ISSUES',[spec,dict(key='B')]), \
             patch.object(sync,'fetch_relations',return_value=relations), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            sync.sync_relations(True,existing,[spec],reconcile=True)
        self.assertIn('by #2',out.getvalue())
        self.assertNotIn('by #3',out.getvalue())

    def test_dry_run_reports_existing_body_change_without_writing(self):
        spec = dict(key='A', title='new', body='new body', parent=None, milestone=None,
                    labels=['type/不具合', 'prio/P0'], blocked_by=[])
        existing = {'A': dict(number=1, node_id='1', state='open', _new=False)}
        remote = dict(body='old body', title='old', labels=[{'name':'prio/P1'}, {'name':'user-label'}])
        with patch.object(sync, 'load_existing', return_value=existing), \
             patch.object(sync, 'api', return_value=remote) as api, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            sync.sync_issues(True, True, {}, [spec])
        self.assertIn('body/meta A',out.getvalue())
        self.assertEqual([c.args[0] for c in api.call_args_list], ['GET'])

    def test_managed_label_change_preserves_custom_labels(self):
        spec = dict(key='A', title='new', body='new body', parent=None, milestone=None,
                    labels=['type/不具合', 'prio/P0'], blocked_by=[])
        existing = {'A': dict(number=1, node_id='1', state='open', _new=False)}
        remote = dict(body='old body', title='old', labels=[{'name':'prio/P1'}, {'name':'user-label'}])
        with patch.object(sync, 'load_existing', return_value=existing), \
             patch.object(sync, 'api', return_value=remote) as api, \
             patch.object(sync.time, 'sleep'), contextlib.redirect_stdout(io.StringIO()):
            sync.sync_issues(False, True, {}, [spec])
        payload = api.call_args_list[-1].args[2]
        self.assertEqual(set(payload['labels']), {'type/不具合', 'prio/P0', 'user-label'})


if __name__ == '__main__':
    unittest.main()
