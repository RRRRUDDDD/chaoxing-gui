"""Tests for scrolling a real reading page instead of trusting readlog."""

import json
import os
import random
import re
import shutil
import subprocess
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch
from requests.cookies import RequestsCookieJar

import tempfile

from api.reading_browser import (
    CdpSocket, CdpTimeout, ChromeReadingPage, NotReadingPage, TaskPointPage,
    allow_reading_url, browser_executable, cdp_cookies, chrome_command,
    _CONTENT_JS, _STATE_JS, _ScrollPlan, _load_next_chapter,
    navigation_ready, scroll_book, scroll_expression, scroll_reading_page,
)


BOOK = "https://mooc1.chaoxing.com/mooc-ans/course/242311696.html?_from_=course-1_class-1_user"
CHAPTER = "https://mooc1.chaoxing.com/mooc-ans/ztnodedetailcontroller/visitnodedetail?courseId=242311696"


def cookies():
    jar = RequestsCookieJar()
    jar.set("UID", "student", domain=".chaoxing.com", path="/")
    jar.set("other", "nope", domain="example.com", path="/")
    return jar


class Page:
    def __init__(self, state):
        self.states = state if isinstance(state, list) else [state]
        self.index = 0
        self.urls = []
        self.scripts = []
        self.closed = False
        self.top = 0
        self.maximum = 2000
        self.loaded = ["knowledge-1"]
        self.next_chapter = ""
        self.actions = []
        self.scroll_chapters = []

    def goto(self, url):
        self.urls.append(url)

    def evaluate(self, expression):
        if expression == _STATE_JS:
            state = self.states[min(self.index, len(self.states) - 1)]
            if self.index < len(self.states) - 1:
                self.index += 1
            return state
        if expression == _CONTENT_JS:
            return self.content()
        self.scripts.append(expression)
        self.scroll_chapters.append(len(self.loaded))
        delta = int(re.search(r"const delta = (-?\d+);", expression)[1])
        before = self.top
        self.top = max(0, min(self.maximum, before + delta))
        if abs(self.top - before) < 1:
            self.top = max(0, min(self.maximum, before - delta))
        return self.top if abs(self.top - before) >= 1 else None

    def content(self):
        return {"top": self.top, "max": self.maximum, "viewport": 600,
                "hasCards": True, "loaded": list(self.loaded),
                "hasMore": bool(self.next_chapter), "visible": bool(self.next_chapter),
                "next": self.next_chapter, "nearEnd": self.maximum - self.top <= 120}

    def close(self):
        self.closed = True


class LoadingPage(Page):
    def __init__(self, chapters=3, height=600, delay=0):
        super().__init__({"hasBox": True, "reading": True})
        self.chapters = chapters
        self.height = height
        self.maximum = height
        self.next_chapter = "2" if chapters > 1 else ""
        self.delay = delay
        self.pending = None
        self.polls = 0
        self.stalled = False
        self.repeat = False
        self.missing = False

    def evaluate(self, expression):
        if expression == _CONTENT_JS and self.pending:
            self.polls += 1
            if self.missing:
                return None
            if self.polls > self.delay and not self.stalled:
                chapter = self.pending
                self.loaded.append("knowledge-" + chapter)
                self.maximum += self.height
                self.next_chapter = str(int(chapter) + 1) if int(chapter) < self.chapters else ""
                if self.repeat:
                    self.next_chapter = chapter
                self.pending = None
        if "button.click();" in expression:
            self.actions.append(expression)
            self.top = self.maximum
            self.pending = self.next_chapter
            self.next_chapter = ""
            self.polls = 0
            return True
        return super().evaluate(expression)


class ReadingBrowserTests(unittest.TestCase):
    def test_reading_urls_stay_on_mooc_book_or_chapter_pages(self):
        self.assertEqual(allow_reading_url(BOOK), BOOK)
        self.assertTrue(allow_reading_url(CHAPTER).endswith("courseId=242311696"))
        for value in ("http://mooc1.chaoxing.com/mooc-ans/course/1.html",
                      "https://evil.example/mooc-ans/course/1.html",
                      "https://mooc1.chaoxing.com/mooc-ans/api/work",
                      "https://user:pw@mooc1.chaoxing.com/mooc-ans/course/1.html"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    allow_reading_url(value)

    def test_cookies_are_limited_to_chaoxing(self):
        exported = cdp_cookies(cookies())
        self.assertEqual([item["name"] for item in exported], ["UID"])
        self.assertEqual(exported[0]["domain"], ".chaoxing.com")

    def test_browser_command_is_a_visible_separate_profile(self):
        command = chrome_command("chrome", 9222, r"C:\temp\profile")
        self.assertNotIn("--headless", command)
        self.assertNotIn("--remote-allow-origins=*", command)
        self.assertIn("--remote-allow-origins=http://127.0.0.1:9222", command)
        self.assertIn("--user-data-dir=C:\\temp\\profile", command)
        self.assertIn("--remote-debugging-address=127.0.0.1", command)

    def test_browser_override_must_be_a_real_file(self):
        missing = os.path.join(os.getcwd(), "missing-browser.exe")
        previous = os.environ.get("CHAOXING_BROWSER")
        os.environ["CHAOXING_BROWSER"] = missing
        try:
            self.assertNotEqual(browser_executable(), missing)
        finally:
            if previous is None:
                os.environ.pop("CHAOXING_BROWSER", None)
            else:
                os.environ["CHAOXING_BROWSER"] = previous

    def test_scroll_interval_matches_the_page_reporter(self):
        page = Page({"hasBox": True, "chapter": ""})
        waited = []
        progress = []
        done = scroll_reading_page(page, 6, on_progress=lambda done, total: progress.append((done, total)),
                                   wait=waited.append, check=lambda: None)
        self.assertEqual(done, 6)
        self.assertEqual(waited, [5.0, 1.0])
        self.assertEqual(progress, [(0, 6), (5.0, 6), (6, 6)])
        self.assertEqual(len(page.scripts), 2)
        self.assertGreater(page.top, 0)

    def test_index_without_reading_box_opens_the_chapter(self):
        page = Page([
            {"hasBox": False, "chapter": CHAPTER},
            {"hasBox": True, "reading": True},
        ])
        scroll_book(BOOK, cookies(), 5, wait=lambda seconds: None, opener=lambda jar: page)
        self.assertEqual(page.urls, [BOOK, allow_reading_url(CHAPTER)])
        self.assertTrue(page.closed)
        self.assertEqual(len(page.scripts), 1)

    def test_video_task_page_is_not_used_as_the_reading_page(self):
        video = CHAPTER + "&knowledgeId=1"
        reading = CHAPTER + "&knowledgeId=2"
        page = Page([
            {"hasBox": True, "reading": False, "taskPoint": True, "chapters": [video, reading]},
            {"hasBox": True, "reading": False, "taskPoint": True},
            {"hasBox": True, "reading": True, "taskPoint": False},
        ])
        scroll_book(BOOK, cookies(), 5, wait=lambda seconds: None, opener=lambda jar: page)
        self.assertEqual(page.urls, [BOOK, allow_reading_url(video), allow_reading_url(reading)])
        self.assertEqual(len(page.scripts), 1)
        self.assertTrue(page.closed)

    def test_only_task_point_pages_are_rejected(self):
        video = CHAPTER + "&knowledgeId=1"
        page = Page([
            {"hasBox": True, "reading": False, "taskPoint": True, "chapter": video},
            {"hasBox": False, "reading": False, "taskPoint": True},
        ])
        with self.assertRaises(RuntimeError) as raised:
            scroll_book(BOOK, cookies(), 5, opener=lambda jar: page)
        self.assertIn("不是阅读页", str(raised.exception))
        self.assertIsInstance(raised.exception, TaskPointPage)
        self.assertEqual(page.scripts, [])
        self.assertTrue(page.closed)

    def test_scroll_expression_scrolls_the_book_body_without_page_globals(self):
        expression = scroll_expression(380)
        # The box only gates the page; body#outerBody is what logs.js reads.
        self.assertIn("#courseMainBox", expression)
        self.assertIn("document.body", expression)
        self.assertNotIn("box = document.querySelector('#courseMainBox')", expression)
        # MooTools on the book page replaces window.Event.
        self.assertNotIn("new Event", expression)
        self.assertIn("insertvideo", _STATE_JS)
        self.assertIn("logs.js", _STATE_JS)

    def test_state_js_inspects_the_dom_without_serializing_the_page(self):
        self.assertIn("logs.js", _STATE_JS)
        self.assertIn("/multimedia/readlog", _STATE_JS)
        self.assertNotIn("innerHTML", _STATE_JS)

    def test_reading_box_is_not_replaced_by_a_chapter_link(self):
        page = Page({"hasBox": True, "reading": True, "chapter": "https://evil.example/steal"})
        scroll_book(BOOK, cookies(), 5, wait=lambda seconds: None, opener=lambda jar: page)
        self.assertEqual(page.urls, [BOOK])
        self.assertTrue(page.closed)

    def test_foreign_chapter_closes_the_page(self):
        page = Page({"hasBox": False, "chapter": "https://evil.example/steal"})
        with self.assertRaises(ValueError):
            scroll_book(BOOK, cookies(), 5, opener=lambda jar: page)
        self.assertTrue(page.closed)
        self.assertEqual(page.urls, [BOOK])

    def test_missing_login_does_not_open_a_browser(self):
        opened = []
        with self.assertRaises(RuntimeError):
            scroll_book(BOOK, [], 5, opener=lambda jar: opened.append(jar))
        self.assertEqual(opened, [])

    def test_scroll_expression_only_interpolates_a_nonzero_integer(self):
        self.assertIn("const delta = -280;", scroll_expression(-280))
        with self.assertRaises(ValueError):
            scroll_expression("0;alert(1)")
        with self.assertRaises(ValueError):
            scroll_expression(0)


    def test_blank_or_error_pages_are_not_ready(self):
        self.assertFalse(navigation_ready("about:blank", "complete"))
        self.assertFalse(navigation_ready("chrome-error://chromewebdata/", "complete"))
        self.assertFalse(navigation_ready(BOOK, "loading"))
        self.assertTrue(navigation_ready(BOOK, "complete"))
        with self.assertRaises(ValueError):
            navigation_ready("https://evil.example/mooc-ans/course/1.html", "complete")

    def test_index_without_a_chapter_is_not_counted(self):
        page = Page({"hasBox": False, "chapter": ""})
        with self.assertRaises(RuntimeError):
            scroll_book(BOOK, cookies(), 5, opener=lambda jar: page)
        self.assertTrue(page.closed)
        self.assertEqual(page.scripts, [])

    def test_browser_override_must_be_chrome_or_edge(self):
        previous = os.environ.get("CHAOXING_BROWSER")
        try:
            with tempfile.TemporaryDirectory() as directory:
                evil = os.path.join(directory, "evil.exe")
                chrome = os.path.join(directory, "chrome.exe")
                open(evil, "wb").close()
                open(chrome, "wb").close()
                os.environ["CHAOXING_BROWSER"] = evil
                self.assertNotEqual(browser_executable(), evil)
                os.environ["CHAOXING_BROWSER"] = chrome
                self.assertEqual(browser_executable(), chrome)
        finally:
            if previous is None:
                os.environ.pop("CHAOXING_BROWSER", None)
            else:
                os.environ["CHAOXING_BROWSER"] = previous

    def _serve_cdp(self, delay=0.0, reply=True):
        """Accept one DevTools client, answer its first command after ``delay``."""
        received = []
        ready = []
        done = threading.Event()

        def serve():
            server = socket.socket()
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            ready.append(server.getsockname()[1])
            connection, _ = server.accept()
            try:
                header = b""
                while b"\r\n\r\n" not in header:
                    header += connection.recv(4096)
                connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
                frame = b""
                while len(frame) < 2:
                    frame += connection.recv(4096)
                length = frame[1] & 0x7F
                mask_and_body = frame[2:]
                while len(mask_and_body) < 4 + length:
                    mask_and_body += connection.recv(4096)
                mask, body = mask_and_body[:4], mask_and_body[4:4 + length]
                payload = bytes(item ^ mask[index % 4] for index, item in enumerate(body))
                received.append(json.loads(payload))
                if done.wait(delay) or not reply:
                    done.wait(5)
                    return
                message = json.dumps({"id": received[0]["id"], "result": {"success": True}}).encode()
                connection.sendall(bytes([0x81, len(message)]) + message)
                done.wait(5)
            finally:
                connection.close()
                server.close()

        thread = threading.Thread(target=serve)
        thread.start()
        while not ready:
            thread.join(0.01)

        def stop():
            done.set()
            thread.join(5)

        self.addCleanup(stop)
        return ready[0], received, stop

    def test_cdp_call_roundtrip_on_localhost(self):
        port, received, stop = self._serve_cdp()
        client = CdpSocket(f"ws://127.0.0.1:{port}/devtools/page/1", port)
        try:
            result = client.call("Network.setCookie", {"name": "UID", "value": "student"})
        finally:
            client.close()
            stop()
        self.assertEqual(result, {"success": True})
        self.assertEqual(received[0]["method"], "Network.setCookie")
        self.assertEqual(received[0]["params"]["name"], "UID")

    def test_cdp_call_waits_past_one_second_of_silence(self):
        port, _, stop = self._serve_cdp(delay=1.5)
        client = CdpSocket(f"ws://127.0.0.1:{port}/devtools/page/1", port)
        try:
            self.assertEqual(client.call("Runtime.evaluate", timeout=5), {"success": True})
        finally:
            client.close()
            stop()

    def test_cdp_call_still_times_out_at_its_deadline(self):
        port, _, stop = self._serve_cdp(reply=False)
        client = CdpSocket(f"ws://127.0.0.1:{port}/devtools/page/1", port)
        started = time.monotonic()
        try:
            with self.assertRaises(RuntimeError) as raised:
                client.call("Runtime.evaluate", timeout=0.6)
        finally:
            client.close()
            stop()
        elapsed = time.monotonic() - started
        self.assertIn("超时", str(raised.exception))
        self.assertGreaterEqual(elapsed, 0.55)
        self.assertLess(elapsed, 3)

    def test_box_without_reporter_is_not_reading(self):
        page = Page({"hasBox": True})
        with self.assertRaises(NotReadingPage):
            scroll_book(BOOK, cookies(), 5, opener=lambda jar: page)
        self.assertEqual(page.scripts, [])
        self.assertTrue(page.closed)

    def test_lost_body_after_progress_is_not_a_reason_to_restart_on_another_book(self):
        page = Page({"reading": True})
        page.evaluate = Mock(side_effect=[page.content(), 380, None])
        progress = []
        with self.assertRaises(RuntimeError) as raised:
            scroll_reading_page(page, 10, wait=lambda seconds: None,
                                on_progress=lambda done, total: progress.append(done))
        self.assertNotIsInstance(raised.exception, NotReadingPage)
        self.assertEqual(progress, [0, 5])
        page.evaluate.side_effect = [None]
        with self.assertRaises(NotReadingPage):
            scroll_reading_page(page, 10, wait=lambda seconds: None)

    def test_cdp_rejects_a_remote_debugger(self):
        with self.assertRaises(RuntimeError):
            CdpSocket("ws://example.com/devtools/page/1", 80)
        with self.assertRaises(RuntimeError):
            CdpSocket("ws://127.0.0.1:9/devtools/page/1", 10)


class ChromeReadingPageReconnectTests(unittest.TestCase):
    TARGETS = [{"id": "T1", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/T1"}]

    def page(self, socket_client):
        return ChromeReadingPage(None, socket_client, "profile-unused",
                                 port=9222, target_id="T1")

    def test_transient_timeout_reconnects_to_the_same_target_and_retries(self):
        dead, fresh = Mock(), Mock()
        dead.call.side_effect = CdpTimeout("阅读浏览器指令超时")
        fresh.call.return_value = {"result": {"value": 42}}
        with patch("api.reading_browser._debugger_json", return_value=self.TARGETS), \
             patch("api.reading_browser.CdpSocket", return_value=fresh) as socket_cls:
            self.assertEqual(self.page(dead).evaluate("1+1"), 42)
        socket_cls.assert_called_once_with("ws://127.0.0.1:9222/devtools/page/T1", 9222)
        dead.close.assert_called_once_with()
        self.assertEqual(fresh.call.call_count, 1)

    def test_missing_target_lets_the_timeout_stand(self):
        dead = Mock()
        dead.call.side_effect = CdpTimeout("阅读浏览器指令超时")
        with patch("api.reading_browser._debugger_json", return_value=[]):
            with self.assertRaises(CdpTimeout):
                self.page(dead).evaluate("1+1")
        dead.close.assert_not_called()

    def test_retry_after_reconnect_times_out_only_once_more(self):
        dead, fresh = Mock(), Mock()
        dead.call.side_effect = CdpTimeout("阅读浏览器指令超时")
        fresh.call.side_effect = CdpTimeout("阅读浏览器指令超时")
        with patch("api.reading_browser._debugger_json", return_value=self.TARGETS), \
             patch("api.reading_browser.CdpSocket", return_value=fresh):
            with self.assertRaises(CdpTimeout):
                self.page(dead).evaluate("1+1")
        self.assertEqual(fresh.call.call_count, 1)
        dead.close.assert_called_once_with()


class ChapterScrollingTests(unittest.TestCase):
    def setUp(self):
        self.random = patch("api.reading_browser.random.Random", return_value=random.Random(24))
        self.random.start()
        self.addCleanup(self.random.stop)

    def test_loads_all_chapters_including_beyond_twelve_then_keeps_scrolling(self):
        page = LoadingPage(chapters=15)
        progress, waits = [], []
        done = scroll_book(BOOK, cookies(), 300, opener=lambda jar: page, wait=waits.append,
                           on_progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(done, 300)
        self.assertEqual(len(page.actions), 14)
        self.assertEqual(len(page.loaded), 15)
        self.assertEqual(len(page.scripts), 60)
        self.assertEqual(progress, [(i, 300) for i in range(0, 301, 5)])
        self.assertEqual(page.urls, [BOOK])
        self.assertTrue(page.closed)
        self.assertTrue(any("const delta = -" in script for script in page.scripts))

    def test_short_chapters_do_not_reset_the_random_reread_pattern(self):
        page = LoadingPage(chapters=30)
        scroll_reading_page(page, 60, wait=lambda seconds: None)
        upwards = [count for count, script in zip(page.scroll_chapters, page.scripts)
                   if "const delta = -" in script]
        self.assertTrue(upwards)
        self.assertLess(upwards[0], page.chapters)
        self.assertGreater(len(page.actions), 1)

    def test_short_first_chapter_is_expanded_before_scrolling(self):
        page = LoadingPage(height=600)
        page.maximum = 0
        scroll_reading_page(page, 5, wait=lambda seconds: None)
        self.assertEqual(len(page.actions), 1)
        self.assertEqual(page.loaded, ["knowledge-1", "knowledge-2"])

    def test_initial_async_content_is_ready_before_counting(self):
        page = LoadingPage(chapters=1, delay=2)
        page.loaded = []
        page.pending = "1"
        page.maximum = 0
        waits = []
        scroll_reading_page(page, 5, wait=waits.append)
        self.assertEqual(waits, [0.2, 5])
        self.assertEqual(page.actions, [])

    def test_async_load_waits_do_not_count_as_reading_time(self):
        page = LoadingPage(chapters=2, delay=3)
        page.top = page.maximum
        waits, progress = [], []
        scroll_reading_page(page, 6, wait=waits.append,
                            on_progress=lambda done, total: progress.append(done))
        self.assertEqual(waits, [0.2, 0.2, 0.2, 5, 1])
        self.assertEqual(progress, [0, 5, 6])
        self.assertEqual(len(page.actions), 1)

    def test_disappeared_button_does_not_hide_load_timeout(self):
        page = LoadingPage()
        page.top = page.maximum
        page.stalled = True
        progress = []
        with self.assertRaisesRegex(RuntimeError, "加载超时"):
            scroll_book(BOOK, cookies(), 5, wait=lambda seconds: None, opener=lambda jar: page,
                        on_progress=lambda done, total: progress.append(done))
        self.assertEqual(len(page.actions), 1)
        self.assertEqual(page.scripts, [])
        self.assertEqual(progress, [0])
        self.assertTrue(page.closed)

    def test_duplicate_loader_is_never_clicked_twice(self):
        page = LoadingPage()
        page.repeat = True
        with self.assertRaisesRegex(RuntimeError, "重复"):
            scroll_reading_page(page, 120, wait=lambda seconds: None)
        self.assertEqual(len(page.actions), 1)

    def test_unrecognized_loader_is_not_executed(self):
        page = LoadingPage()
        page.top = page.maximum
        page.next_chapter = "unexpected-handler"
        with self.assertRaisesRegex(RuntimeError, "不受支持"):
            scroll_reading_page(page, 5, wait=lambda seconds: None)
        self.assertEqual(page.actions, [])

    def test_changed_button_aborts_without_counting(self):
        page = Mock()
        page.evaluate.return_value = False
        with self.assertRaisesRegex(RuntimeError, "已变化"):
            _load_next_chapter(page, LoadingPage().content(), set(), Mock(), lambda: None)
        page.evaluate.assert_called_once()


    def test_cancelled_loading_closes_browser_and_preserves_progress(self):
        from api.course_tools import ToolCancelled
        page = LoadingPage(delay=10)
        progress = []

        def wait(seconds):
            if seconds < 1:
                raise ToolCancelled("stopped during loading")

        with self.assertRaises(ToolCancelled):
            scroll_book(BOOK, cookies(), 120, wait=wait, opener=lambda jar: page,
                        on_progress=lambda done, total: progress.append(done))
        self.assertEqual(progress, [0, 5])
        self.assertEqual(len(page.actions), 1)
        self.assertTrue(page.closed)

    def test_content_loss_during_loading_is_not_replayed_on_another_book(self):
        page = LoadingPage()
        page.missing = True
        with self.assertRaisesRegex(RuntimeError, "正文已丢失") as raised:
            scroll_reading_page(page, 120, wait=lambda seconds: None)
        self.assertNotIsInstance(raised.exception, NotReadingPage)

    def test_new_task_point_content_aborts_without_resetting_progress(self):
        page = LoadingPage()
        page.states = [{"reading": True}, {"reading": False, "taskPoint": True}]
        progress = []
        with self.assertRaisesRegex(RuntimeError, "不是可用阅读页") as raised:
            scroll_book(BOOK, cookies(), 120, wait=lambda seconds: None, opener=lambda jar: page,
                        on_progress=lambda done, total: progress.append(done))
        self.assertNotIsInstance(raised.exception, NotReadingPage)
        self.assertEqual(progress, [0, 5])
        self.assertTrue(page.closed)

    def test_finished_budget_does_not_load_another_chapter(self):
        page = LoadingPage()
        scroll_reading_page(page, 5, wait=lambda seconds: None)
        self.assertEqual(page.actions, [])

    def test_no_more_chapters_still_scrolls_in_both_directions(self):
        page = LoadingPage(chapters=1)
        scroll_reading_page(page, 60, wait=lambda seconds: None)
        distances = [int(re.search(r"const delta = (-?\d+);", script)[1]) for script in page.scripts]
        self.assertTrue(any(d < 0 for d in distances))
        self.assertTrue(any(d > 0 for d in distances))
        self.assertGreater(len(set(map(abs, distances))), 2)
        self.assertEqual(page.actions, [])

    def test_scroll_plan_reflects_at_both_edges(self):
        plan = _ScrollPlan()
        state = LoadingPage().content()
        self.assertGreater(plan.distance(state), 0)
        state["top"] = state["max"]
        self.assertLess(plan.distance(state), 0)


@unittest.skipUnless(shutil.which("node"), "Node is required to execute the page JavaScript")
class ReadingJavaScriptTests(unittest.TestCase):
    def evaluate(self, expressions, *, top=0, maximum=660, body_scrollable=True, next_chapter="2"):
        script = r"""
const input = JSON.parse(process.argv[1]);
const scroller = {scrollTop: input.top, scrollHeight: input.maximum + 600, clientHeight: 600};
const emptyBody = {scrollTop: 0, scrollHeight: 600, clientHeight: 600};
const visibleBody = {scrollTop: 0, scrollHeight: 10000, clientHeight: 600};
// MooTools 1.4 replaces Array.from with its single-argument conversion.
Array.from = item => Array.prototype.slice.call(item);
const loaded = [{id: 'knowledge-1'}];
let button, clicks = 0, placed = false;
button = {
    getClientRects: () => [{}],
    getAttribute: () => 'loadMoreChapter(' + input.next + ')',
    scrollIntoView: () => {placed = true; scroller.scrollTop = input.maximum;},
    click: () => {clicks++; loaded.push({id: 'knowledge-' + input.next}); button = null;}
};
const cards = {querySelectorAll: () => loaded};
const main = {querySelector: s => s === '#cardview' ? cards : button};
global.document = {body: input.body === 'visible' ? visibleBody : (input.body ? scroller : emptyBody),
    scrollingElement: scroller,
    querySelector: () => main};
global.window = {innerHeight: 600, Event: () => {throw Error('MooTools Event must not be used');}};
global.getComputedStyle = el => ({visibility: 'visible', overflowY: el === visibleBody ? 'visible' : 'auto'});
const results = input.expressions.map(expression => eval(expression));
console.log(JSON.stringify({results, top: scroller.scrollTop, clicks, placed}));
"""
        result = subprocess.run([shutil.which("node"), "-e", script, json.dumps({
            "expressions": expressions, "top": top, "maximum": maximum,
            "body": body_scrollable, "next": next_chapter,
        })], capture_output=True, text=True, timeout=10, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_previous_stuck_boundary_now_moves_and_reflects(self):
        result = self.evaluate([scroll_expression(380), scroll_expression(380), scroll_expression(-280)], top=380)
        self.assertEqual(result["results"], [660, 280, 0])

    def test_every_step_moves_even_on_a_short_page(self):
        result = self.evaluate([scroll_expression(d) for d in [400, 500, -300, -400, 800]], maximum=30)
        self.assertEqual(result["results"], [30, 0, 30, 0, 30])

    def test_falls_back_to_document_scroller(self):
        result = self.evaluate([scroll_expression(200)], body_scrollable=False)
        self.assertEqual(result["results"], [200])

    def test_overflowing_but_non_scrollable_body_is_not_selected(self):
        result = self.evaluate([scroll_expression(200)], body_scrollable="visible")
        self.assertEqual(result["results"], [200])

    def test_non_scrollable_content_returns_null(self):
        self.assertEqual(self.evaluate([scroll_expression(200)], maximum=0)["results"], [None])

    def test_content_snapshot_and_native_loader_execute_on_the_dom(self):
        page = LoadingPage()
        _load_next_chapter(page, page.content(), set(), lambda seconds: None, lambda: None)
        result = self.evaluate([_CONTENT_JS, page.actions[0], _CONTENT_JS])
        before, clicked, after = result["results"]
        self.assertEqual(before["next"], "2")
        self.assertEqual(before["loaded"], ["knowledge-1"])
        self.assertTrue(clicked)
        self.assertEqual(after["loaded"], ["knowledge-1", "knowledge-2"])
        self.assertFalse(after["hasMore"])
        self.assertTrue(result["placed"])
        self.assertEqual(result["clicks"], 1)

    def test_changed_native_handler_is_not_clicked(self):
        page = LoadingPage()
        _load_next_chapter(page, page.content(), set(), lambda seconds: None, lambda: None)
        result = self.evaluate([page.actions[0]], next_chapter="3")
        self.assertEqual(result["results"], [False])
        self.assertEqual(result["clicks"], 0)


if __name__ == "__main__":
    unittest.main()
