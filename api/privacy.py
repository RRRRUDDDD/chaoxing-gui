"""Shared redaction for logs, notifications and public task errors."""

from collections.abc import Mapping
import re
import threading
from urllib.parse import urlsplit

_SECRETS_LIMIT = 512
# 有界集合（dict 模拟保序 set）：超限淘汰最旧的注册值。注册源是无界增长的
# 动态配置（订阅、cookie 等），不封顶会让每条日志的全量替换开销持续上升。
_secrets: dict = {}
_lock = threading.RLock()
_FIELDS = r'(?:password|passwd|pwd|cookies?|set-cookie|authorization|api[_-]?key|key|tokens?|[a-z_]*token|secret|enc|aienc|username|account|_?uid|fid|cpi|clazzid|classid|courseid|tg_chat_id|chat_id)'
_SENSITIVE = re.compile(_FIELDS, re.I)
_ASSIGNMENT = re.compile(rf'''(?i)(["']?\b{_FIELDS}["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;&}}\]]+)''')
_URL = re.compile(r'''https?://[^\s<>"']+''', re.I)


def register_secret(value):
    if isinstance(value, (str, int)) and len(str(value)) >= 4:
        with _lock:
            _secrets[str(value)] = None
            while len(_secrets) > _SECRETS_LIMIT:
                del _secrets[next(iter(_secrets))]


def register_config(config):
    if isinstance(config, Mapping):
        for key, value in config.items():
            if _SENSITIVE.fullmatch(str(key)) or str(key).lower() in {'url', 'subscription'}:
                register_secret(value)
                if str(key).lower() == 'tokens' and isinstance(value, str):
                    for token in re.split(r'[,;\s]+', value):
                        register_secret(token)
            register_config(value)
    elif isinstance(config, (list, tuple)):
        for item in config:
            register_config(item)


def redact(value):
    text = str(value)
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, '[redacted]')
    text = re.sub(r'<RequestsCookieJar\[.*?\]>', '[redacted cookies]', text, flags=re.S)
    text = re.sub(r'''(?i)\bBearer\s+[^\s,;"']+''', 'Bearer [redacted]', text)
    text = re.sub(r'''(?is)(["']?\b(?:set-cookie|cookies?)["']?\s*[:=]\s*).*''',
                  lambda m: m[1] + '[redacted cookies]', text)
    text = re.sub(rf'''(?is)(["']?\b{_FIELDS}["']?\s*[:=]\s*)[{{\[].*''',
                  lambda m: m[1] + '[redacted container]', text)
    text = _ASSIGNMENT.sub(lambda m: m[1] + '[redacted]', text)

    def hide_url(match):
        try:
            host = urlsplit(match[0]).hostname or 'service'
            return 'https://' + host + '/[redacted]'
        except ValueError:
            return '[redacted URL]'

    text = _URL.sub(hide_url, text)
    return re.sub(r'(?<!\d)1[3-9]\d{9}(?!\d)', '[redacted account]', text)


def safe_extra(value):
    if isinstance(value, dict):
        return {key: item if key == 'task_id' else
                '[redacted]' if _SENSITIVE.fullmatch(str(key)) else safe_extra(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_extra(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(value)


def sanitize_errors(value):
    """Clean public error fields only; never alter account identities or resume secrets."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and (key == 'error' or key.endswith('_error')) and isinstance(item, str):
                value[key] = redact(item)
            else:
                sanitize_errors(item)
    elif isinstance(value, list):
        for item in value:
            sanitize_errors(item)


def patch_record(record):
    record['message'] = redact(record['message'])
    record['extra'] = safe_extra(record['extra'])
    exc = record.get('exception')
    if exc:
        # Keep frame locations, never locals, source lines or an unsanitized chain.
        frames = []
        tb = exc.traceback
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append(f'{code.co_filename}:{tb.tb_lineno} in {code.co_name}')
            tb = tb.tb_next
        detail = f'{exc.type.__name__}: {exc.value}'
        record['message'] += '\n' + redact('\n'.join(frames[-20:] + [detail]))
        record['exception'] = None
