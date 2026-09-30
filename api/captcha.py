"""Chaoxing "please verify" captcha, solved with the bundled OCR.

When requests are throttled the platform can answer with a verification page
that loads ``/processVerifyPng.ac`` and submits to ``/html/processVerify.ac``.
The image and the submission use the account's own session so that any cookie
the platform issues on success reaches every worker.

Detection is deliberately narrow: only a response that points at those two
endpoints (a redirect Location or a page body) counts. A plain 403 stays a
plain 403. No real throttled sample has been captured yet, so the rule is not
validated against the live platform; widen it only with captured evidence.
"""

import threading
import time
from random import randint
from typing import Optional
from urllib.parse import urljoin, urlsplit

from loguru import logger

from api.captcha_ocr import CaptchaOcr
from api.cookies import save_cookies

HOST = "https://mooc1.chaoxing.com"
IMAGE_PATH = "/processVerifyPng.ac"
SUBMIT_PATH = "/html/processVerify.ac"
_MARKERS = ("processverifypng.ac", "processverify.ac")
_PEEK_BYTES = 64 * 1024
# Only synthetic fixtures exist. Enable after real rejection/pass samples prove
# detection, success and cookie semantics; this is not a user-facing bypass flag.
CAPTCHA_PROTOCOL_VERIFIED = False

_OCR_RETRY_SECONDS = 60.0
_ocr_lock = threading.Lock()
_ocr_engine: Optional[CaptchaOcr] = None
_ocr_retry_at = 0.0


def captcha_ocr() -> Optional[CaptchaOcr]:
    """Process-wide OCR engine; a failed start is retried after 60 seconds."""
    global _ocr_engine, _ocr_retry_at
    with _ocr_lock:
        if _ocr_engine is not None:
            return _ocr_engine
        if time.monotonic() < _ocr_retry_at:
            return None
        try:
            _ocr_engine = CaptchaOcr()
        except Exception as exc:
            _ocr_retry_at = time.monotonic() + _OCR_RETRY_SECONDS
            logger.warning("验证码 OCR 初始化失败: {}", exc)
        return _ocr_engine


def is_captcha_response(response, *, inspect_body=True) -> bool:
    """True only when the response sends the user to the verification page."""
    if response is None:
        return False
    location = response.headers.get("Location")
    if isinstance(location, str) and response.status_code in (301, 302, 303, 307, 308, 403):
        try:
            target = urlsplit(urljoin(getattr(response, "url", None) or HOST, location))
            if (target.scheme == "https" and target.hostname == "mooc1.chaoxing.com"
                    and target.username is None and target.password is None and target.port in (None, 443)
                    and target.path.lower() in (IMAGE_PATH.lower(), SUBMIT_PATH.lower())):
                return True
        except ValueError:
            pass
    # Streamed downloads must never be eagerly read for HTML markers.
    if not inspect_body or response.status_code not in (200, 403):
        return False
    content_type = str(response.headers.get("Content-Type") or "").lower()
    if content_type and "html" not in content_type:
        return False
    try:
        body = response.text[:_PEEK_BYTES].lower()
    except Exception:
        return False
    return any(marker in body for marker in _MARKERS)


class CxCaptcha:
    """Fetch, recognize and submit one verification image for an account."""

    def __init__(self, session_manager, account=None, ocr=None):
        self.session_manager = session_manager
        self.account = account
        self.ocr = ocr

    def fetch_image(self, session) -> Optional[bytes]:
        response = session.get(HOST + IMAGE_PATH, params={"t": randint(0, 2147483647)},
                               allow_redirects=False)
        try:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if response.status_code == 200 and content_type.startswith("image/"):
                return response.content
            return None
        finally:
            response.close()

    def submit(self, session, code: str) -> bool:
        response = session.get(HOST + SUBMIT_PATH, params={"ucode": code, "app": 0},
                               allow_redirects=False)
        try:
            # A passed check redirects back; a failed one redraws the page.
            return response.status_code in (301, 302, 303) and not is_captcha_response(response)
        finally:
            response.close()

    def attempt(self, cancel_check=None) -> bool:
        """One fetch-recognize-submit round; publishes cookies on success."""
        cancelled = cancel_check or (lambda: False)
        if cancelled():
            return False
        engine = self.ocr or captcha_ocr()
        if engine is None or cancelled():
            return False
        session = self.session_manager.get_session()
        image = self.fetch_image(session)
        if not image or cancelled():
            return False
        code = engine.classification(image)
        if cancelled() or not code or not self.submit(session, code):
            return False
        # Any cookie issued with the pass must reach the other worker threads.
        self.session_manager.set_cookies(session.cookies)
        try:
            save_cookies(session, self.account)
        except OSError as exc:
            logger.warning("验证码通过后保存 Cookie 失败: {}", exc)
        return True
