import contextvars
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from api.base import Account, Chaoxing
from api.cookies import save_cookies, use_cookies
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
        response = Mock(status_code=200, text='<div>course list</div>')
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
            return Mock(status_code=200, text='course list')
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


if __name__ == '__main__':
    unittest.main()
