# -*- coding: utf-8 -*-
import json
import os
import tempfile
import threading
from hashlib import sha256
from http.cookiejar import Cookie
from pathlib import Path

import requests

from api.config import GlobalConst as gc


cookie_lock = threading.RLock()


def _serialize_cookie(cookie):
    fields = ('version', 'name', 'value', 'port', 'port_specified', 'domain',
              'domain_specified', 'domain_initial_dot', 'path', 'path_specified',
              'secure', 'expires', 'discard', 'comment', 'comment_url', 'rfc2109')
    result = {field: getattr(cookie, field) for field in fields}
    result['rest'] = dict(cookie._rest)
    return result


def _cookie_path(account=None):
    legacy = Path(gc.COOKIES_PATH)
    if account:
        account_key = sha256(str(account).strip().encode('utf-8')).hexdigest()
        return legacy.parent / '.cookies' / f'{account_key}.json'
    return legacy


def save_cookies(session: requests.Session, account=None):
    with cookie_lock:
        path = _cookie_path(account)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                             prefix=path.name + '.', suffix='.tmp',
                                             delete=False) as stream:
                temporary = stream.name
                if account:
                    json.dump({'version': 1, 'cookies': [_serialize_cookie(cookie)
                                                        for cookie in session.cookies]},
                              stream, ensure_ascii=False)
                else:
                    stream.write(';'.join(f'{key}={value}' for key, value in session.cookies.items()))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def use_cookies(account=None):
    with cookie_lock:
        path = _cookie_path(account)
        if not path.exists():
            return {}

        cookies = {}
        try:
            with path.open('r', encoding='utf-8') as f:
                if account:
                    data = json.load(f)
                    if isinstance(data, dict) and data.get('version') == 1 and isinstance(data.get('cookies'), list):
                        jar = requests.cookies.RequestsCookieJar()
                        for entry in data['cookies']:
                            cookie = Cookie(**entry)
                            if not cookie.domain:
                                cookie.domain = '.chaoxing.com'
                                cookie.domain_specified = True
                                cookie.domain_initial_dot = True
                            jar.set_cookie(cookie)
                        return jar
                    if isinstance(data, dict) and all(isinstance(k, str) and isinstance(v, str)
                                                      for k, v in data.items()):
                        return data
                    return {}
                buffer = f.read().strip()
                if not buffer:
                    return {}
                for item in buffer.split(";"):
                    item = item.strip()
                    if not item:
                        continue
                    parts = item.split("=", 1)
                    if len(parts) == 2:
                        cookies[parts[0]] = parts[1]
        except Exception:
            return {}

        return cookies
