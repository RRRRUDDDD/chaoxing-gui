import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from loguru import logger as direct_logger
from api import privacy
from api.logger import logger
from api.notification import Bark
from api.task_logging import task_log_sink
from api.task_state import TaskStore


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.registry = patch.object(privacy, '_secrets', set())
        self.registry.start()
        self.addCleanup(self.registry.stop)

    def test_structures_urls_headers_and_registered_values(self):
        privacy.register_config({'headers': {'Authorization': 'Bearer canary-key'},
                                 'password': 'canary-password'})
        for text in ['canary-password', 'Authorization: Bearer canary-key',
                     'Cookie: first=canary-cookie; next=another-secret',
                     'https://example.invalid/push/canary-key?q=secret',
                     '{"password": "canary-password"}']:
            with self.subTest(text=text):
                safe = privacy.redact(text)
                self.assertNotIn('canary-', safe)
                self.assertNotIn('another-secret', safe)
        privacy.register_secret('task-123')
        extra = privacy.safe_extra({'task_id': 'task-123', 'count': 4,
                                    'headers': {'Authorization': 'canary-key'}})
        self.assertEqual(extra['task_id'], 'task-123')
        self.assertEqual(extra['count'], 4)
        self.assertNotIn('canary-key', str(extra))

    def test_all_sinks_receive_safe_exceptions_without_losing_context(self):
        privacy.register_secret('canary-secret')
        store = TaskStore()
        self.addCleanup(store.close)
        task_id = store.create('account', {}, {})
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'safe.log'
            ids = [logger.add(stream), logger.add(target)]
            try:
                with task_log_sink(store, task_id):
                    try:
                        raise ValueError('canary-secret https://host.invalid/token')
                    except ValueError:
                        direct_logger.bind(password='canary-secret').exception('failed canary-secret')
                logger.complete()
                outputs = [stream.getvalue(), target.read_text(encoding='utf8'),
                           str(store.read_logs(task_id))]
                for output in outputs:
                    self.assertNotIn('canary-secret', output)
                    self.assertNotIn('/token', output)
                    self.assertIn('ValueError', output)
                    self.assertIn('test_all_sinks', output)
            finally:
                for sink in ids:
                    logger.remove(sink)

    def test_notification_sanitizes_body_not_destination(self):
        service = Bark()
        url = 'https://example.invalid/canary-push-key'
        service.config_set({'url': url})
        service.init_notification()
        with patch('api.notification.requests.post') as post:
            service.send('failed ' + url)
        self.assertEqual(post.call_args.args[0], url)
        self.assertNotIn('canary-push-key', str(post.call_args.kwargs['params']))

    def test_task_error_fields_are_safe_without_mutating_runtime_config(self):
        privacy.register_secret('canary-password')
        store = TaskStore()
        self.addCleanup(store.close)
        task_id = store.create('account', {}, {})
        with store.edit(task_id) as task:
            task.status['notification_error'] = 'failed canary-password'
            task.details['courses'] = [{'error': 'canary-password'}]
        store.finish(task_id, 'error', error='canary-password')
        self.assertNotIn('canary-password', str(store.get_status(task_id)))
        self.assertNotIn('canary-password', str(store.get_details(task_id)))
