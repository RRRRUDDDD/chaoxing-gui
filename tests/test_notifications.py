import configparser
import os
import unittest
from unittest.mock import patch

import requests

from api.notification import Bark, Qmsg, ServerChan, Telegram, NOTIFICATION_TIMEOUT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NotificationTests(unittest.TestCase):
    def test_all_http_notifications_are_bounded_and_do_not_retry_posts(self):
        for provider in (Bark, Qmsg, ServerChan, Telegram):
            with self.subTest(provider=provider.__name__):
                service = provider()
                service.url = 'https://example.invalid/notification'
                with patch('api.notification.requests.post', side_effect=requests.Timeout) as post:
                    service.send('offline test')
                self.assertEqual(post.call_args.kwargs['timeout'], NOTIFICATION_TIMEOUT)
                post.assert_called_once()


class NotificationConfigContractTests(unittest.TestCase):
    """config.ini.example must only advertise keys the code actually reads."""

    def test_config_example_notification_keys_match_the_code(self):
        consumed_keys = {'provider', 'url', 'tg_chat_id'}
        provider_names = {'ServerChan', 'Qmsg', 'Bark', 'Telegram', 'Windows'}
        parser = configparser.ConfigParser()
        with open(os.path.join(ROOT, 'config.ini.example'), encoding='utf8') as fh:
            parser.read_file(fh)
        self.assertIn('notification', parser, 'config.ini.example 必须保留 [notification] 段')

        section = parser['notification']
        self.assertLessEqual(set(section), consumed_keys,
                             f'config.ini.example 出现代码不消费的键: {set(section) - consumed_keys}')

        provider = section.get('provider', '').strip()
        self.assertTrue(provider == '' or provider in provider_names,
                        f'provider 示例值 {provider!r} 不是可实例化的通知服务类名')


if __name__ == '__main__':
    unittest.main()
