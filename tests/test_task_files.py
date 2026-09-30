"""Incremental task files, failure isolation and restart-safe legacy migration."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api.task_state import TaskNotFound, TaskStore
from tests.storage_fixtures import task_path

SETTINGS = {'course_list': ['course'], 'jobs': 1, 'speed': 1, 'retry_interval': 1,
            'notopen_action': 'retry', 'tiku_config': {}, 'notification_config': {}}


def record(account='alice', **status):
    return {'account': account, 'status': {'status': 'running', **status}, 'details': {},
            'resume_config': deepcopy(SETTINGS), 'logs': [], 'sequence': 0}


class TaskFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.legacy = self.root / 'study_tasks.json'
        self.directory = self.legacy.with_suffix('')
        self.now = 1000.0

    def store(self):
        store = TaskStore(state_file=self.legacy, wall_time=lambda: self.now,
                          clock=lambda: self.now, ttl_seconds=10)
        self.addCleanup(store.close)
        return store

    def old_file(self, tasks):
        self.legacy.write_text(json.dumps({'version': 1, 'tasks': tasks}), encoding='utf-8')
        return self.legacy.read_bytes()

    def new_file(self, task_id, payload):
        self.directory.mkdir(exist_ok=True)
        task_path(self.legacy, task_id).write_text(json.dumps(payload), encoding='utf-8')

    def test_migrates_all_tasks_then_preserves_old_file_verbatim(self):
        raw = self.old_file({'a': record(), 'b': record('bob', status='completed', end_time=self.now)})
        store = self.store()
        self.assertEqual(store.get_status('a')['status'], 'interrupted')
        self.assertEqual(store.get_status('b')['status'], 'completed')
        self.assertFalse(self.legacy.exists())
        self.assertEqual(self.legacy.with_name('study_tasks.json.migrated').read_bytes(), raw)
        self.assertEqual({path.name for path in self.directory.iterdir()}, {'a.json', 'b.json'})

    def test_partial_migration_keeps_old_file_and_reuses_published_records(self):
        raw = self.old_file({'a': record(), 'b': record('bob')})
        store = self.store()
        replace = os.replace

        def fail_second(source, target):
            if Path(target).name == 'b.json':
                raise OSError('disk full')
            return replace(source, target)

        with patch('api.task_state.os.replace', side_effect=fail_second):
            with self.assertRaises(OSError):
                store.get_status('a')
        self.assertEqual(self.legacy.read_bytes(), raw)
        self.assertFalse(self.legacy.with_name('study_tasks.json.migrated').exists())
        first = task_path(self.legacy, 'a').stat().st_mtime_ns
        self.assertEqual(self.store().get_status('b')['status'], 'interrupted')
        self.assertEqual(task_path(self.legacy, 'a').stat().st_mtime_ns, first)
        self.assertEqual(list(self.directory.glob('*.tmp')), [])

    def test_migration_refuses_to_overwrite_a_backup_or_conflicting_target(self):
        raw = self.old_file({'a': record()})
        backup = self.legacy.with_name('study_tasks.json.migrated')
        backup.write_bytes(b'previous backup')
        with self.assertRaises(OSError):
            self.store().get_status('a')
        self.assertEqual(backup.read_bytes(), b'previous backup')
        backup.unlink()
        self.new_file('a', {'version': 1, 'task': record('different-account')})
        with self.assertRaises(OSError):
            self.store().get_status('a')
        self.assertEqual(self.legacy.read_bytes(), raw)

    def test_checkpoint_only_rewrites_its_task_not_other_logs(self):
        store = self.store()
        a = store.create('alice', {}, {}, resume_config=SETTINGS)
        b = store.create('bob', {}, {}, resume_config=SETTINGS)
        store.append_log(b, 'not checkpointed yet')
        original = task_path(self.legacy, b).read_bytes()
        store.append_log(a, 'a checkpoint')
        with patch('api.task_state.os.replace', wraps=os.replace) as replace:
            store.checkpoint(a)
        self.assertEqual(replace.call_count, 1)
        self.assertEqual(Path(replace.call_args.args[1]), task_path(self.legacy, a))
        self.assertEqual(task_path(self.legacy, b).read_bytes(), original)
        loaded = json.loads(task_path(self.legacy, a).read_text(encoding='utf-8'))
        self.assertEqual(loaded['task']['logs'][0]['message'], 'a checkpoint')
        self.assertFalse(self.legacy.exists())

    def test_corrupt_file_does_not_block_another_account_or_get_overwritten(self):
        self.new_file('good', {'version': 1, 'task': record()})
        broken = task_path(self.legacy, 'bad')
        broken.write_text('{broken', encoding='utf-8')
        with patch('api.task_state.logger') as log:
            store = self.store()
            self.assertEqual(store.get_status('good')['status'], 'interrupted')
        self.assertIn('bad.json', log.error.call_args.args)
        with self.assertRaises(TaskNotFound):
            store.get_status('bad')
        store.create('bob', {}, {}, resume_config=SETTINGS)
        self.assertEqual(broken.read_text(encoding='utf-8'), '{broken')

    def test_bad_version_log_sequence_and_record_fields_are_isolated(self):
        for mutate in (
            lambda payload: payload.update(version=2),
            lambda payload: payload.update(version=True),
            lambda payload: payload['task'].update(sequence=-1),
            lambda payload: payload['task'].update(sequence=1, logs=[{'seq': 2, 'message': 'bad'}]),
            lambda payload: payload['task'].update(resume_config={'password': 'not-allowed'}),
            lambda payload: payload['task'].update(account=[]),
        ):
            payload = {'version': 1, 'task': record('bob')}
            mutate(payload)
            self.new_file('bad', payload)
            self.new_file('good', {'version': 1, 'task': record()})
            store = self.store()
            with self.assertRaises(TaskNotFound):
                store.get_status('bad')
            self.assertEqual(store.get_status('good')['status'], 'interrupted')

    def test_expiry_deletes_only_the_finished_task_file(self):
        store = self.store()
        a = store.create('alice', {}, {}, resume_config=SETTINGS)
        b = store.create('bob', {}, {}, resume_config=SETTINGS)
        store.finish(a, 'completed')
        self.now += 11
        self.assertEqual(store.cleanup(), 1)
        self.assertFalse(task_path(self.legacy, a).exists())
        self.assertTrue(task_path(self.legacy, b).exists())
        self.assertEqual(self.store().get_status(b)['status'], 'interrupted')

    def test_cancelled_migration_has_a_stable_terminal_time(self):
        self.old_file({'a': record(cancel_requested=True)})
        self.assertEqual(self.store().get_status('a')['end_time'], self.now)
        self.now += 2
        self.assertEqual(self.store().get_status('a')['end_time'], self.now - 2)
        self.now += 10
        with self.assertRaises(TaskNotFound):
            self.store().get_status('a')
