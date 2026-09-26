"""Tests for scrolling a real reading page instead of trusting readlog."""

import json
import os
import socket
import threading
import unittest
from requests.cookies import RequestsCookieJar

import tempfile

from api.reading_browser import (
    CdpSocket, allow_reading_url, browser_executable, cdp_cookies, chrome_command,
    _STATE_JS, navigation_ready, scroll_book, scroll_expression, scroll_reading_page,
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

    def goto(self, url):
        self.urls.append(url)

    def evaluate(self, expression):
        if "hasBox" in expression:
            state = self.states[min(self.index, len(self.states) - 1)]
            if self.index < len(self.states) - 1:
                self.index += 1
            return state
        self.scripts.append(expression)
        return 1

    def close(self):
        self.closed = True


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
        self.assertIn("[380, 380, -280][0]", page.scripts[0])
        self.assertIn("[380, 380, -280][1]", page.scripts[1])

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
        self.assertEqual(page.scripts, [])
        self.assertTrue(page.closed)

    def test_scroll_expression_stays_inside_the_reading_box(self):
        expression = scroll_expression(0)
        self.assertIn("#courseMainBox", expression)
        self.assertNotIn("scrollingElement", expression)
        self.assertNotIn("documentElement", expression)
        self.assertIn("insertvideo", _STATE_JS)
        self.assertIn("logs.js", _STATE_JS)

    def test_reading_box_is_not_replaced_by_a_chapter_link(self):
        page = Page({"hasBox": True, "chapter": "https://evil.example/steal"})
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

    def test_scroll_expression_only_interpolates_the_step_index(self):
        self.assertIn("[380, 380, -280][2]", scroll_expression(2))
        self.assertIn("[380, 380, -280][0]", scroll_expression(3))


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

    def test_cdp_call_roundtrip_on_localhost(self):
        received = []

        def serve(port_ready):
            server = socket.socket()
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port_ready.append(server.getsockname()[1])
            connection, _ = server.accept()
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
            reply = json.dumps({"id": received[0]["id"], "result": {"success": True}}).encode()
            connection.sendall(bytes([0x81, len(reply)]) + reply)
            connection.close()
            server.close()

        ready = []
        thread = threading.Thread(target=serve, args=(ready,))
        thread.start()
        while not ready:
            thread.join(0.01)
        client = CdpSocket(f"ws://127.0.0.1:{ready[0]}/devtools/page/1", ready[0])
        try:
            result = client.call("Network.setCookie", {"name": "UID", "value": "student"})
        finally:
            client.close()
            thread.join(2)
        self.assertEqual(result, {"success": True})
        self.assertEqual(received[0]["method"], "Network.setCookie")
        self.assertEqual(received[0]["params"]["name"], "UID")

    def test_cdp_rejects_a_remote_debugger(self):
        with self.assertRaises(RuntimeError):
            CdpSocket("ws://example.com/devtools/page/1", 80)
        with self.assertRaises(RuntimeError):
            CdpSocket("ws://127.0.0.1:9/devtools/page/1", 10)


if __name__ == "__main__":
    unittest.main()
