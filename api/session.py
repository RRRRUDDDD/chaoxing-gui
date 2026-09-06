"""Account-owned HTTP sessions, with one mutable session per worker thread."""

import functools
import threading
from contextlib import contextmanager
from contextvars import ContextVar

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from api.config import GlobalConst as gc
from api.cookies import use_cookies


HTTP_TIMEOUT = (5, 15)
_current_manager = ContextVar('chaoxing_session_manager', default=None)


class SessionManager:
    def __init__(self, account=None):
        self.account = account
        self._lock = threading.RLock()
        self._local = threading.local()
        self._sessions = set()
        self._cookies = requests.cookies.RequestsCookieJar()
        self._revision = 0
        self._closed = False

    def get_session(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('HTTP session manager is closed')
            session = getattr(self._local, 'session', None)
            if session is None:
                session = requests.Session()
                # Retry only safe reads. Login/submission POSTs must not be replayed.
                retries = Retry(total=2, connect=2, read=0, status=2,
                                status_forcelist=(429, 502, 503, 504),
                                allowed_methods=frozenset({'GET', 'HEAD'}),
                                backoff_factor=0.3, respect_retry_after_header=False)
                for scheme in ('https://', 'http://'):
                    session.mount(scheme, HTTPAdapter(max_retries=retries))
                session.request = functools.partial(session.request, timeout=HTTP_TIMEOUT)
                session.headers.clear()
                session.headers.update(gc.HEADERS)
                self._local.session = session
                self._local.revision = -1
                self._sessions.add(session)
            if self._local.revision != self._revision:
                session.cookies.clear()
                session.cookies.update(self._cookies)
                self._local.revision = self._revision
            return session

    def set_cookies(self, cookies):
        with self._lock:
            if self._closed:
                raise RuntimeError('HTTP session manager is closed')
            # Publish a copy, never the jar being mutated by a live request.
            self._cookies = requests.cookies.RequestsCookieJar()
            if isinstance(cookies, requests.cookies.RequestsCookieJar):
                self._cookies.update(cookies)
            else:
                # Older cookie files stored only names/values. Limit those to the
                # trusted service instead of creating hostless cookies.
                for name, value in cookies.items():
                    self._cookies.set(name, value, domain='.chaoxing.com', path='/')
            self._revision += 1

    def update_cookies(self):
        self.set_cookies(use_cookies(self.account))

    @contextmanager
    def context(self):
        token = _current_manager.set(self)
        try:
            yield self
        finally:
            _current_manager.reset(token)

    def close_current_session(self):
        with self._lock:
            session = getattr(self._local, 'session', None)
            if session is None:
                return
            self._local.session = None
            self._sessions.discard(session)
        session.close()

    def close(self):
        # Owners call this after joining their workers, not during live requests.
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = tuple(self._sessions)
            self._sessions.clear()
            self._cookies.clear()
        for session in sessions:
            session.close()


def get_current_session():
    """Borrow the current account's thread session; the manager owns its lifetime."""
    manager = _current_manager.get()
    return manager.get_session() if manager is not None else None
