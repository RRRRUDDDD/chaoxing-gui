import unittest
from unittest.mock import Mock, patch

import requests

from api.notification import Bark, Qmsg, ServerChan, Telegram, NOTIFICATION_TIMEOUT


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


if __name__ == '__main__':
    unittest.main()
