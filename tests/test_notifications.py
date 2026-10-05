import configparser
import os
import unittest
from unittest.mock import Mock, patch

import requests

from api.notification import PROVIDER_REGISTRY, Bark, Qmsg, ServerChan, Telegram, NOTIFICATION_TIMEOUT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NotificationTests(unittest.TestCase):
    def test_all_http_notifications_are_bounded_and_retry_exactly_once(self):
        for provider in (Bark, Qmsg, ServerChan, Telegram):
            with self.subTest(provider=provider.__name__):
                service = provider()
                service.url = 'https://example.invalid/notification'
                with patch('api.notification.requests.post', side_effect=requests.Timeout) as post:
                    self.assertFalse(service.send('offline test'))
                self.assertEqual(post.call_count, 2)
                for call in post.call_args_list:
                    self.assertEqual(call.kwargs['timeout'], NOTIFICATION_TIMEOUT)

    def test_send_recovers_when_the_retry_succeeds(self):
        service = Bark()
        service.url = 'https://example.invalid/notification'
        ok = Mock(status_code=200)
        with patch('api.notification.requests.post', side_effect=[requests.Timeout('first attempt'), ok]) as post:
            self.assertTrue(service.send('retry works'))
        self.assertEqual(post.call_count, 2)

    def test_disabled_service_reports_success_without_sending(self):
        service = ServerChan()
        service.disabled = True
        with patch('api.notification.requests.post') as post:
            self.assertTrue(service.send('not configured'))
        post.assert_not_called()


class NotificationConfigContractTests(unittest.TestCase):
    """config.ini.example must only advertise keys the code actually reads."""

    def test_config_example_notification_keys_match_the_code(self):
        consumed_keys = {'provider', 'url', 'tg_chat_id'}
        expected_names = {'ServerChan', 'Qmsg', 'Bark', 'Telegram', 'Windows'}
        self.assertEqual(set(PROVIDER_REGISTRY), expected_names,
                         'PROVIDER_REGISTRY 必须恰好包含五个通知服务类名')
        parser = configparser.ConfigParser()
        with open(os.path.join(ROOT, 'config.ini.example'), encoding='utf8') as fh:
            parser.read_file(fh)
        self.assertIn('notification', parser, 'config.ini.example 必须保留 [notification] 段')

        section = parser['notification']
        self.assertLessEqual(set(section), consumed_keys,
                             f'config.ini.example 出现代码不消费的键: {set(section) - consumed_keys}')

        provider = section.get('provider', '').strip()
        self.assertTrue(provider == '' or provider in PROVIDER_REGISTRY,
                        f'provider 示例值 {provider!r} 不是可实例化的通知服务类名')


if __name__ == '__main__':
    unittest.main()
