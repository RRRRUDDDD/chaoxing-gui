import contextvars
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from loguru import logger

from api.base import Account, Chaoxing
from api.cookies import _cookie_path, save_cookies, use_cookies
from api.paths import data_dir
from api.session import HTTP_TIMEOUT, SessionManager, get_current_session


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cookie_path = Path(self.directory.name) / 'cookies.txt'
        self.patch = patch('api.cookies.gc.COOKIES_PATH', str(self.cookie_path))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.directory.cleanup)

    def test_reuses_thread_session_without_reloading_cookies(self):
        manager = SessionManager('alice')
        self.addCleanup(manager.close)
        with patch('api.session.use_cookies', return_value={'_uid': 'alice'}) as read:
            manager.update_cookies()
            first = manager.get_session()
            self.assertIs(first, manager.get_session())
            self.assertIs(first, manager.get_session())
        read.assert_called_once_with('alice')
        self.assertEqual(first.cookies.get('_uid'), 'alice')

    def test_threads_have_separate_jars_and_close_their_sessions(self):
        manager = SessionManager('alice')
        self.addCleanup(manager.close)
        manager.set_cookies({'_uid': 'alice'})
        gate = threading.Barrier(2)
        sessions = []
        observed = []
        def work(value):
            with manager.context():
                session = get_current_session()
                sessions.append(session)
                session.cookies.set('worker', value)
                gate.wait(timeout=5)
                observed.append(session.cookies.get('worker'))
                self.assertIs(session, manager.get_session())
            manager.close_current_session()
        threads = [threading.Thread(target=work, args=(value,)) for value in ('one', 'two')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertIsNot(sessions[0], sessions[1])
        self.assertEqual(sorted(observed), ['one', 'two'])
        self.assertEqual(len(manager._sessions), 0)

    def test_account_cookies_never_fall_back_to_another_account(self):
        self.cookie_path.write_text('_uid=legacy', encoding='utf-8')
        for account in ('alice', 'bob'):
            with requests.Session() as session:
                session.cookies.set('_uid', account)
                save_cookies(session, account)
        self.assertEqual(use_cookies('alice'), {'_uid': 'alice'})
        self.assertEqual(use_cookies('bob'), {'_uid': 'bob'})
        self.assertEqual(use_cookies('missing'), {})
        self.assertEqual(use_cookies(), {'_uid': 'legacy'})

    def test_cookie_replacement_clears_old_values(self):
        manager = SessionManager('alice')
        self.addCleanup(manager.close)
        manager.set_cookies({'_uid': 'old', 'stale': 'cookie'})
        session = manager.get_session()
        manager.set_cookies({'_uid': 'new'})
        self.assertIs(session, manager.get_session())
        self.assertEqual(session.cookies.get_dict(), {'_uid': 'new'})

    def test_context_is_scoped_and_can_be_copied(self):
        manager = SessionManager('alice')
        self.addCleanup(manager.close)
        self.assertIsNone(get_current_session())
        with manager.context():
            copied = contextvars.copy_context()
        self.assertIsNone(get_current_session())
        self.assertIs(copied.run(get_current_session), manager.get_session())
        manager.close()
        with self.assertRaises(RuntimeError):
            manager.get_session()

    def test_login_has_timeout_and_owns_a_reusable_session(self):
        client = Chaoxing(account=Account('alice', 'secret'), tiku=Mock())
        self.addCleanup(client.close)
        session = client.session_manager.get_session()
        response = Mock(status_code=200)
        response.json.return_value = {'status': True}
        with patch.object(session, 'post', return_value=response) as post:
            self.assertTrue(client.login()['status'])
        self.assertEqual(post.call_args.kwargs['timeout'], HTTP_TIMEOUT)
        self.assertIs(session, client.session_manager.get_session())
        with patch.object(session, 'close') as close:
            client.close()
            client.close()
        close.assert_called_once()
        client.tiku.close.assert_called_once()

    def test_cookie_only_login_uses_selected_account(self):
        with requests.Session() as session:
            session.cookies.set('_uid', 'alice')
            save_cookies(session, 'alice')
        client = Chaoxing(account=Account('alice', ''), tiku=None)
        self.addCleanup(client.close)
        response = Mock(status_code=200, url='https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata',
                        text='<div>course list</div>')
        with patch.object(client.session_manager.get_session(), 'post', return_value=response):
            self.assertTrue(client.login(login_with_cookies=True)['status'])
        self.assertEqual(client.get_uid(), 'alice')
        other = Chaoxing(account=Account('bob', ''), tiku=None)
        self.addCleanup(other.close)
        self.assertFalse(other.login(login_with_cookies=True)['status'])

    def test_cookie_attributes_survive_persistence(self):
        with requests.Session() as session:
            session.cookies.set('token', 'first', domain='.chaoxing.com', path='/', secure=True)
            session.cookies.set('token', 'second', domain='mooc1.chaoxing.com', path='/work')
            save_cookies(session, 'alice')
        restored = use_cookies('alice')
        self.assertEqual([(cookie.domain, cookie.path, cookie.secure, cookie.value) for cookie in restored],
                         [('.chaoxing.com', '/', True, 'first'), ('mooc1.chaoxing.com', '/work', False, 'second')])

    def test_legacy_cookie_refresh_is_published_without_hostless_duplicates(self):
        with requests.Session() as session:
            session.cookies.set('_uid', 'alice')
            save_cookies(session, 'alice')
        path = next((self.cookie_path.parent / '.cookies').glob('*.json'))
        path.write_text(json.dumps({'_uid': 'alice', 'token': 'old'}), encoding='utf-8')
        client = Chaoxing(account=Account('alice', ''), tiku=None)
        self.addCleanup(client.close)
        session = client.session_manager.get_session()
        def validate(*args, **kwargs):
            session.cookies.set('_uid', 'alice', domain='.chaoxing.com', path='/')
            session.cookies.set('token', 'rotated', domain='.chaoxing.com', path='/')
            return Mock(status_code=200, url='https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata',
                        text='course list')
        with patch.object(session, 'post', side_effect=validate):
            self.assertTrue(client.login(login_with_cookies=True)['status'])
        self.assertEqual(client.get_uid(), 'alice')
        self.assertEqual(len([cookie for cookie in session.cookies if cookie.name == '_uid']), 1)
        observed = []
        def worker():
            observed.append(client.session_manager.get_session().cookies.get('token'))
            client.session_manager.close_current_session()
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        self.assertEqual(observed, ['rotated'])
        self.assertEqual(use_cookies('alice').get('token'), 'rotated')

    def test_forbidden_recovery_keeps_its_own_login_session(self):
        client = Chaoxing(account=Account('alice', ''), tiku=None)
        self.addCleanup(client.close)
        client.session_manager.set_cookies({'_uid': 'alice', 'token': 'login-one'})
        session = client.session_manager.get_session()
        with requests.Session() as other:
            other.cookies.set('token', 'login-two')
            save_cookies(other, 'alice')
        with patch.object(client, '_refresh_video_status', side_effect=lambda current, *_: current.cookies.get('token')):
            self.assertEqual(client._recover_after_forbidden(session, {}, 'Video'), 'login-one')

    def test_same_uid_on_multiple_service_domains_is_unambiguous(self):
        client = Chaoxing(account=Account('alice', ''), tiku=None)
        self.addCleanup(client.close)
        session = client.session_manager.get_session()
        session.cookies.set('_uid', 'alice', domain='.chaoxing.com')
        session.cookies.set('_uid', 'alice', domain='mooc1.chaoxing.com')
        self.assertEqual(client.get_uid(), 'alice')

    def test_cookie_validation_tightened_login_page_heuristic(self):
        client = Chaoxing(account=Account('alice', ''), tiku=None)
        self.addCleanup(client.close)
        client.session_manager.set_cookies({'_uid': 'alice'})
        session = client.session_manager.get_session()
        url = 'https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata'

        def page(text, target=url):
            return Mock(status_code=200, url=target, text=text)

        # 正常课程页里出现 "login" 字样不再误判为失效会话。
        with patch.object(session, 'post',
                          return_value=page('<script>if (!user) showLogin()</script>')):
            self.assertTrue(client._validate_cookie_session())
        for text, target in (
            ('<form action="https://passport2.chaoxing.com/fanyalogin">', url),
            ('<script src="//passport2.chaoxing.com/login"></script>', url),
            ('clean text', 'https://passport2.chaoxing.com/fanyalogin'),
        ):
            with patch.object(session, 'post', return_value=page(text, target)):
                self.assertFalse(client._validate_cookie_session())

    def test_thread_local_hit_does_not_take_the_global_lock(self):
        manager = SessionManager('alice')
        self.addCleanup(manager.close)
        manager.set_cookies({'_uid': 'alice'})

        class CountingLock:
            def __init__(self, lock):
                self._lock = lock
                self.acquires = 0

            def acquire(self, *args):
                self.acquires += 1
                return self._lock.acquire(*args)

            def release(self):
                self._lock.release()

            def __enter__(self):
                return self.acquire()

            def __exit__(self, *args):
                self.release()

        counting = CountingLock(manager._lock)
        manager._lock = counting
        first = manager.get_session()
        self.assertEqual(counting.acquires, 1, 'first use builds the session under the lock')
        self.assertIs(first, manager.get_session())
        self.assertEqual(counting.acquires, 1, 'a fresh thread-local hit must skip the lock')
        manager.set_cookies({'_uid': 'bob'})
        self.assertIs(first, manager.get_session())
        self.assertEqual(first.cookies.get('_uid'), 'bob')

    def test_corrupt_account_cookie_file_warns_and_returns_empty(self):
        path = _cookie_path('alice')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"version": 1, "cookies": [', encoding='utf-8')
        records = []
        handler = logger.add(records.append, level='WARNING')
        try:
            self.assertEqual(use_cookies('alice'), {})
        finally:
            logger.remove(handler)
        self.assertTrue(any('cookie' in str(record) for record in records))

    def test_legacy_cookie_path_anchors_to_data_dir_not_cwd(self):
        # gc.COOKIES_PATH is patched to an absolute temp path; the legacy path
        # must resolve *through* data_dir (not CWD), so when the patch value
        # is a bare filename it lands inside data_dir.
        from api import cookies as ck
        with patch('api.cookies.gc.COOKIES_PATH', 'cookies.txt'), \
             patch('os.getcwd', return_value='/tmp/should_not_be_used'):
            self.assertEqual(ck._cookie_path(), Path(data_dir()) / 'cookies.txt')


if __name__ == '__main__':
    unittest.main()
