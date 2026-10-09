import configparser
import os
import unittest
from unittest.mock import Mock, patch

import requests

from api.notification import PROVIDER_REGISTRY, Bark, Qmsg, ServerChan, Telegram, Windows, NOTIFICATION_TIMEOUT

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


class WindowsToastHereStringTests(unittest.TestCase):
    def test_leading_at_quote_terminator_is_neutralized(self):
        script = Windows._build_script("line one\n'@evil\nline two")
        # The here-string terminator (@\n followed by ';) must appear only once,
        # as the template's own closing terminator — not from message content.
        inner = script.split("$xmlText = @'", 1)[1].split("'@;", 1)[0]
        self.assertNotIn("'@\n", inner)
        # The '@ prefix in the message must be replaced with a space so it
        # can't act as a terminator.
        self.assertIn("evil", inner)
        self.assertNotIn("'@", inner)

    def test_clean_multiline_message_is_preserved(self):
        script = Windows._build_script("hello\nworld")
        self.assertIn("hello\nworld", script)


class TelegramContractTests(unittest.TestCase):
    def test_failure_returns_false_and_retries(self):
        service = Telegram()
        service.url = 'https://example.invalid/notification'
        service.tg_chat_id = 'cid'
        bad = Mock(status_code=500)
        bad.raise_for_status.side_effect = requests.HTTPError('500')
        ok = Mock(status_code=200)
        ok.raise_for_status.return_value = None
        ok.json.return_value = {'ok': False}
        with patch('api.notification.requests.post', side_effect=[bad, ok]) as post:
            self.assertFalse(service.send('retry fails'))
        self.assertEqual(post.call_count, 2)
        for call in post.call_args_list:
            self.assertEqual(call.kwargs['timeout'], NOTIFICATION_TIMEOUT)

    def test_special_characters_are_escaped(self):
        service = Telegram()
        service.url = 'https://example.invalid/notification'
        service.tg_chat_id = 'cid'
        ok = Mock(status_code=200)
        ok.raise_for_status.return_value = None
        ok.json.return_value = {'ok': True}
        with patch('api.notification.requests.post', return_value=ok) as post:
            service.send('<tag>&"weird"')
        body = post.call_args.kwargs['data']
        self.assertNotIn('<tag>', body)
        self.assertIn('&lt;tag&gt;', body['text'])
        self.assertIn('&amp;', body['text'])


if __name__ == '__main__':
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
