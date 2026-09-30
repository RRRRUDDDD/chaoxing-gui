"""Offline tests for solving the verification page inside the study flow.

The fixtures under tests/fixtures/captcha are synthetic; see their README.
"""

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from requests.cookies import RequestsCookieJar

from api import captcha
from api.base import Account, Chaoxing, StudyResult
from api.course_tools import CourseTools

FIXTURES = Path(__file__).parent / "fixtures" / "captcha"
VERIFY_PAGE = (FIXTURES / "verify-page.html").read_text(encoding="utf-8")
PLAIN_403 = (FIXTURES / "plain-403.html").read_text(encoding="utf-8")


def response(status=200, text="", headers=None, content=b""):
    item = requests.Response()
    item.status_code = status
    item._content = text.encode("utf-8") if text else content
    item.encoding = "utf-8"
    item.headers.update(headers or {})
    item.url = "https://mooc1.chaoxing.com/offline"
    item.raw = Mock()
    return item


def verify_page():
    return response(200, VERIFY_PAGE, {"Content-Type": "text/html;charset=UTF-8"})


def redirect_to_verify():
    return response(302, headers={"Location": "https://mooc1.chaoxing.com/html/processVerify.ac?ucode="})


class DetectionTests(unittest.TestCase):
    def test_verification_page_and_redirect_are_detected(self):
        self.assertTrue(captcha.is_captcha_response(verify_page()))
        self.assertTrue(captcha.is_captcha_response(redirect_to_verify()))
        self.assertTrue(captcha.is_captcha_response(response(403, VERIFY_PAGE)))

    def test_streamed_html_is_not_consumed_and_foreign_redirect_is_not_verification(self):
        streamed = response(200, headers={"Content-Type": "text/html"})
        from unittest.mock import PropertyMock
        with patch.object(requests.Response, "text", new_callable=PropertyMock, side_effect=AssertionError("body read")):
            self.assertFalse(captcha.is_captcha_response(streamed, inspect_body=False))
        for url in ("https://evil.example/html/processVerify.ac", "https://mooc1.chaoxing.com/other?next=processVerify.ac"):
            self.assertFalse(captcha.is_captcha_response(response(302, headers={"Location": url})))

    def test_plain_errors_and_normal_pages_are_not(self):
        for item in (
            response(403, PLAIN_403, {"Content-Type": "text/html"}),
            response(200, '{"isPassed": false}', {"Content-Type": "application/json"}),
            response(200, "<html><div id='mArg'></div></html>", {"Content-Type": "text/html"}),
            response(302, headers={"Location": "https://mooc1.chaoxing.com/mooc-ans/course/1.html"}),
            response(500, VERIFY_PAGE),
            response(200, content=b"\x89PNG processVerify.ac", headers={"Content-Type": "image/png"}),
            None,
        ):
            with self.subTest(item=item):
                self.assertFalse(captcha.is_captcha_response(item))


class SolverTests(unittest.TestCase):
    def setUp(self):
        self.manager = Mock()
        self.session = Mock()
        self.session.cookies = RequestsCookieJar()
        self.manager.get_session.return_value = self.session
        self.engine = Mock()
        self.engine.classification.return_value = "a1b2"
        self.solver = captcha.CxCaptcha(self.manager, "alice", ocr=self.engine)

    def test_pass_publishes_and_saves_the_new_cookie(self):
        def get(url, params=None, allow_redirects=None):
            self.assertIs(allow_redirects, False)
            if url.endswith(captcha.IMAGE_PATH):
                return response(200, content=b"png", headers={"Content-Type": "image/png"})
            self.assertEqual(params, {"ucode": "a1b2", "app": 0})
            self.session.cookies.set("verify", "passed", domain=".chaoxing.com", path="/")
            return response(302, headers={"Location": "https://mooc1.chaoxing.com/mooc-ans/course/1.html"})

        self.session.get.side_effect = get
        with patch.object(captcha, "save_cookies") as save:
            self.assertTrue(self.solver.attempt())
        published = self.manager.set_cookies.call_args.args[0]
        self.assertEqual(published.get("verify"), "passed")
        save.assert_called_once_with(self.session, "alice")

    def test_wrong_code_or_missing_image_fails_without_publishing(self):
        self.session.get.side_effect = [
            response(200, content=b"png", headers={"Content-Type": "image/png"}),
            redirect_to_verify(),
            response(200, VERIFY_PAGE, {"Content-Type": "text/html"}),
        ]
        self.assertFalse(self.solver.attempt())
        self.assertFalse(self.solver.attempt())
        self.manager.set_cookies.assert_not_called()

    def test_cancel_after_recognition_does_not_submit(self):
        stopped = threading.Event()
        self.session.get.return_value = response(200, content=b"png", headers={"Content-Type": "image/png"})
        self.engine.classification.side_effect = lambda data: (stopped.set() or "code")
        self.assertFalse(self.solver.attempt(stopped.is_set))
        self.session.get.assert_called_once()
        self.manager.set_cookies.assert_not_called()

    def test_pass_cookie_is_seen_by_an_existing_thread_session(self):
        from api.session import SessionManager
        manager = SessionManager("offline")
        self.addCleanup(manager.close)
        ready, published = threading.Event(), threading.Event()
        seen = []
        def worker():
            session = manager.get_session()
            ready.set()
            published.wait(3)
            refreshed = manager.get_session()
            seen.append((session is refreshed, refreshed.cookies.get("verified")))
            manager.close_current_session()
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(ready.wait(3))
            manager.get_session()
            def submit(current, code):
                current.cookies.set("verified", "yes", domain=".chaoxing.com", path="/")
                return True
            solver = captcha.CxCaptcha(manager, "offline", ocr=self.engine)
            with patch.object(solver, "fetch_image", return_value=b"png"),                     patch.object(solver, "submit", side_effect=submit), patch.object(captcha, "save_cookies"):
                self.assertTrue(solver.attempt())
        finally:
            published.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen, [(True, "yes")])

    def test_no_ocr_engine_means_no_request(self):
        solver = captcha.CxCaptcha(self.manager, "alice")
        with patch.object(captcha, "captcha_ocr", return_value=None):
            self.assertFalse(solver.attempt())
        self.session.get.assert_not_called()


class ChaoxingCaptchaTests(unittest.TestCase):
    def setUp(self):
        verified = patch("api.base.CAPTCHA_PROTOCOL_VERIFIED", True)
        verified.start()
        self.addCleanup(verified.stop)
        self.client = Chaoxing(account=Account("offline", "secret"), tiku=None)
        self.client.rate_limiter = Mock()
        self.client.video_log_limiter = Mock()
        self.addCleanup(self.client.close)
        self.client.session_manager.set_cookies({"_uid": "offline"})
        wait = patch("api.base._wait_for_cancel", return_value=False)
        wait.start()
        self.addCleanup(wait.stop)

    def report(self, session, cancel_check=None):
        job = {"jobid": "j", "objectid": "o", "otherinfo": "nodeId_1-x", "videoFaceCaptureEnc": "",
               "attDuration": "", "attDurationEnc": "", "rt": "0.9", "name": "video"}
        course = {"clazzId": "c", "courseId": "k", "cpi": "p"}
        return self.client.video_progress_log(session, course, job, {}, "token", 60, 10,
                                              headers={}, cancel_check=cancel_check)

    def test_rejected_report_is_resent_once_after_the_pass(self):
        session = Mock()
        session.headers = {}
        passed = response(200, '{"isPassed": true}', {"Content-Type": "application/json"})
        session.get.side_effect = [verify_page(), passed]
        with patch.object(self.client, "solve_captcha", return_value=True) as solve:
            self.assertEqual(self.report(session), (True, 200))
        solve.assert_called_once()
        self.assertEqual(session.get.call_count, 2)

    def test_failed_captcha_is_forbidden_after_three_attempts(self):
        session = Mock()
        session.headers = {}
        session.get.return_value = verify_page()
        with patch("api.base.CxCaptcha") as solver:
            solver.return_value.attempt.return_value = False
            self.assertEqual(self.report(session), (False, 403))
        self.assertEqual(solver.return_value.attempt.call_count, Chaoxing.CAPTCHA_ATTEMPTS)
        self.assertEqual(session.get.call_count, 1, "a failed pass must not resend the report")

    def test_concurrent_threads_solve_the_page_once(self):
        started = threading.Event()
        release = threading.Event()
        calls = []

        def attempt(**kwargs):
            calls.append(threading.current_thread().name)
            started.set()
            release.wait(5)
            return True

        results = []
        with patch("api.base.CxCaptcha") as solver:
            solver.return_value.attempt.side_effect = attempt
            first = threading.Thread(target=lambda: results.append(self.client.solve_captcha()), name="first")
            first.start()
            self.assertTrue(started.wait(5))
            second = threading.Thread(target=lambda: results.append(self.client.solve_captcha()), name="second")
            second.start()
            time.sleep(0.1)
            release.set()
            first.join(5)
            second.join(5)
        self.assertEqual(calls, ["first"])
        self.assertEqual(results, [True, True])
        with patch("api.base.CxCaptcha") as solver:
            solver.return_value.attempt.return_value = False
            self.assertFalse(self.client.solve_captcha(), "a later page is solved again")
            solver.return_value.attempt.assert_called()

    def test_stop_during_the_captcha_skips_without_more_attempts(self):
        stop = threading.Event()

        def attempt(**kwargs):
            stop.set()
            return False

        with patch("api.base.CxCaptcha") as solver:
            solver.return_value.attempt.side_effect = attempt
            self.assertFalse(self.client.solve_captcha(stop.is_set))
        self.assertEqual(solver.return_value.attempt.call_count, 1)

    def test_live_submission_stays_disabled_until_real_protocol_is_verified(self):
        with patch("api.base.CAPTCHA_PROTOCOL_VERIFIED", False), patch("api.base.CxCaptcha") as solver:
            self.assertFalse(self.client.solve_captcha())
            solver.assert_not_called()

    def test_cancel_does_not_wait_for_another_workers_solver(self):
        stop = threading.Event()
        self.client._captcha_lock.acquire()
        worker = threading.Thread(target=lambda: self.client.solve_captcha(stop.is_set))
        try:
            worker.start()
            stop.set()
            worker.join(1)
            self.assertFalse(worker.is_alive())
        finally:
            self.client._captcha_lock.release()
            worker.join(3)

    def test_card_page_captcha_is_not_an_empty_chapter(self):
        session = Mock()
        session.get.return_value = verify_page()
        course = {"clazzId": "c", "courseId": "k", "cpi": "p"}
        with patch.object(self.client.session_manager, "get_session", return_value=session), \
                patch.object(self.client, "solve_captcha", return_value=False), \
                patch.object(self.client, "study_emptypage") as empty:
            with self.assertRaises(requests.RequestException):
                self.client.get_job_list(course, {"id": "1", "title": "chapter"})
        empty.assert_not_called()

    def test_card_page_is_read_again_after_the_pass(self):
        session = Mock()
        session.get.side_effect = [verify_page(), response(200, "no cards", {"Content-Type": "text/html"})]
        course = {"clazzId": "c", "courseId": "k", "cpi": "p"}
        with patch.object(self.client.session_manager, "get_session", return_value=session), \
                patch.object(self.client, "solve_captcha", return_value=True), \
                patch.object(self.client, "study_emptypage", return_value=StudyResult.SUCCESS):
            jobs, info = self.client.get_job_list(course, {"id": "1", "title": "chapter"})
        self.assertEqual((jobs, info.get("empty")), ([], True))
        self.assertEqual(session.get.call_count, 2)


class CourseToolCaptchaTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.get_adapter.return_value = Mock()
        self.chaoxing = Mock()
        self.chaoxing.session_manager.get_session.return_value = self.session

    def test_report_is_resent_once_after_the_pass(self):
        self.session.get.side_effect = [verify_page(), response(200, "{}")]
        self.chaoxing.solve_captcha.return_value = True
        tools = CourseTools(self.chaoxing)
        self.assertEqual(tools._text("get", "https://mooc1.chaoxing.com/report", "视频时长上报", no_retry=True), "{}")
        self.assertEqual(self.session.get.call_count, 2)

    def test_failed_pass_is_reported_and_not_resent(self):
        self.session.get.return_value = verify_page()
        self.chaoxing.solve_captcha.return_value = False
        tools = CourseTools(self.chaoxing)
        with self.assertRaisesRegex(RuntimeError, "手动完成验证"):
            tools._text("get", "https://mooc1.chaoxing.com/report", "视频时长上报", no_retry=True)
        self.assertEqual(self.session.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
