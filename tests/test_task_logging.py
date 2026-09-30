import threading
import unittest
from unittest.mock import patch

from api.logger import logger
from api.task_logging import task_log_sink
from api.task_state import TaskStore


class TaskLogSinkTests(unittest.TestCase):
    def test_simultaneous_sinks_filter_context_and_normalize_critical(self):
        store = TaskStore()
        self.addCleanup(store.close)
        ids = [store.create(account, {}, {}) for account in ('alice', 'bob')]
        barrier = threading.Barrier(2)
        errors = []

        def worker(task_id):
            try:
                with task_log_sink(store, task_id):
                    barrier.wait(timeout=3)
                    logger.critical('tail-{}', task_id)
                store.finish(task_id, 'completed')
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(task_id,)) for task_id in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        for task_id in ids:
            entries = store.read_logs(task_id)['data']
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['level'], 'error')
            self.assertIn('tail-' + task_id, entries[0]['message'])

    def test_sink_removed_even_when_drain_fails(self):
        store = TaskStore()
        self.addCleanup(store.close)
        task_id = store.create('alice', {}, {})
        with patch.object(logger, 'complete', side_effect=RuntimeError('drain failed')), \
                patch.object(logger, 'remove', wraps=logger.remove) as remove:
            with self.assertRaisesRegex(RuntimeError, 'drain failed'):
                with task_log_sink(store, task_id):
                    pass
            remove.assert_called_once()
