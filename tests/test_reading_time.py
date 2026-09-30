"""Protocol-level tests for the background专题阅读 service."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from api.reading_time import ReadingTools
from api.reading_browser import NotReadingPage
from api.course_tools import ToolCancelled


COURSE = {"courseId": "course-1", "clazzId": "class-1", "cpi": "enrollment-1", "title": "课程"}
ATTACHMENT = {
    "type": "read", "jobid": "read-1", "enc": "fresh-enc",
    "property": {"module": "insertread", "mid": "mid-1", "jobid": "read-1"},
}
RESOURCE = {
    "id": "reading-1", "course_id": "course-1", "chapter_id": "101",
    "_attachment": ATTACHMENT, "_defaults": {"courseId": "course-1", "clazzId": "class-1"},
}
READ_PAGE = (
    '<div class="readTips"><p>您的阅读总时长:<span>4.3</span>分钟</p>'
    '<p>阅读总时长达到<span>60</span>分钟</p></div>'
    '<div class="readList"><a href="/mooc-ans/course/242311696.html?_from_=course-1_class-1_user_sig">'
    '<span class="readTitle">专题书</span></a></div>'
)
BOOK_PAGE = '<script>window["courseid"] = "242311696"; var ctid = 847466162;</script><div id="pageDiv">内容</div>'


class Response:
    def __init__(self, text="", status=200, headers=None):
        self.text, self.status_code, self.headers = text, status, headers or {}
        self.closed = False

    def close(self):
        self.closed = True


class ReadingProtocolTests(unittest.TestCase):
    def setUp(self):
        self.session = SimpleNamespace(get=Mock(), get_adapter=Mock())
        self.adapter = SimpleNamespace(max_retries=SimpleNamespace(total=2))
        self.session.get_adapter.return_value = self.adapter
        client = SimpleNamespace(session_manager=SimpleNamespace(get_session=Mock(return_value=self.session)))
        self.service = ReadingTools(client)
        self.service._wait = Mock()

    def responses(self, *items):
        self.session.get.side_effect = [item if isinstance(item, Response) else Response(item) for item in items]

    def test_watch_reading_scrolls_the_book_page_instead_of_trusting_readlog(self):
        self.responses(BOOK_PAGE, READ_PAGE)
        self.session.cookies = []
        with unittest.mock.patch("api.reading_time.scroll_book", return_value=10) as scrolled:
            result = self.service.watch_reading(COURSE, {**RESOURCE, "_books": [{
                "url": "https://mooc1.chaoxing.com/mooc-ans/course/242311696.html?_from_=course-1_class-1_user_sig",
                "title": "专题书", "author": "作者"}]}, 10)
        self.assertEqual(result["seconds"], 10)
        self.assertEqual(result["after"], 4.3)
        book = scrolled.call_args.args[0]
        self.assertTrue(book.startswith("https://mooc1.chaoxing.com/mooc-ans/course/242311696.html"))
        self.assertEqual(scrolled.call_args.args[2], 10)
        readlogs = [call for call in self.session.get.call_args_list
                    if call.args and str(call.args[0]).endswith("/multimedia/readlog")]
        self.assertEqual(readlogs, [])


    def test_public_resource_excludes_private_protocol_fields(self):
        resource = {**RESOURCE, "readable": True, "required_minutes": 60, "read_minutes": 4.3,
                    "book_count": 1, "_books": [{"url": "https://mooc1.chaoxing.com/mooc-ans/course/1.html"}]}
        public = self.service.public_resource(resource)
        self.assertEqual(public["book_count"], 1)
        self.assertNotIn("_attachment", public)
        self.assertNotIn("_books", public)



    def test_task_point_page_is_not_parsed_as_reading(self):
        video = '<iframe class="ans-insertvideo-online" module="insertvideo" src="/ananas/modules/video/index.html"></iframe>'
        document = '<iframe class="ans-attach-online ans-book" src="/ananas/modules/read/indexV2.html"></iframe>'
        for page in (video, document):
            with self.subTest(page=page):
                with self.assertRaises(RuntimeError) as raised:
                    self.service._parse_read_page(page)
                self.assertIn("不是阅读页", str(raised.exception))
        self.assertEqual(self.service._parse_read_page(READ_PAGE)["required_minutes"], 60)

    def books(self):
        return [{"url": f"https://mooc1.chaoxing.com/mooc-ans/course/{ident}.html?_from_=course-1_class-1_user_sig"}
                for ident in (242311696, 242311697)]

    def test_book_context_validates_redirects_without_requesting_cards(self):
        book = self.books()[0]
        redirected = book["url"].replace("/course/", "/zt/")
        self.responses(Response(status=302, headers={"Location": redirected}), "<html>book index</html>")
        self.assertEqual(self.service._book_context(book, course=COURSE), redirected)
        self.assertEqual([call.args[0] for call in self.session.get.call_args_list], [book["url"], redirected])

    def test_book_identity_and_attribution_checks_remain(self):
        book = self.books()[0]
        for text in ('<script>window.courseid = "99";</script>', BOOK_PAGE):
            with self.subTest(text=text):
                self.responses(text)
                course = COURSE if "99" in text else {**COURSE, "courseId": "foreign"}
                with self.assertRaises(ValueError):
                    self.service._book_context(book, course=course)
        self.responses(Response(status=302, headers={"Location": "https://evil.example/book"}))
        with self.assertRaises(ValueError):
            self.service._book_context(book, course=COURSE)

    def test_browser_tries_next_book_only_for_unreadable_content(self):
        books = self.books()
        self.session.cookies = []
        self.responses("<html>first index</html>", "<html>second index</html>", READ_PAGE)
        with patch("api.reading_time.scroll_book", side_effect=[NotReadingPage("不是阅读页"), 10]) as scroll:
            result = self.service.watch_reading(COURSE, {**RESOURCE, "_books": books}, 10)
        self.assertEqual([call.args[0] for call in scroll.call_args_list], [book["url"] for book in books])
        self.assertEqual(result["seconds"], 10)
        self.assertEqual(self.session.get.call_count, 3)

    def test_all_books_unreadable_preserves_error_messages(self):
        self.session.cookies = []
        for message, expected in (("不是阅读页", "不是阅读页"), ("阅读页没有可滚动的正文", "没有可读取的专题书籍")):
            with self.subTest(message=message):
                self.responses("<html>index</html>", "<html>index</html>")
                with patch("api.reading_time.scroll_book", side_effect=NotReadingPage(message)) as scroll:
                    with self.assertRaisesRegex(RuntimeError, expected):
                        self.service.watch_reading(COURSE, {**RESOURCE, "_books": self.books()}, 10)
                self.assertEqual(scroll.call_count, 2)

    def test_network_and_cancel_errors_do_not_switch_books_or_replay_time(self):
        self.session.cookies = []
        for error in (RuntimeError("browser connection lost"), ToolCancelled("stopped")):
            with self.subTest(error=error):
                self.responses("<html>index</html>")
                with patch("api.reading_time.scroll_book", side_effect=error) as scroll:
                    with self.assertRaises(type(error)):
                        self.service.watch_reading(COURSE, {**RESOURCE, "_books": self.books()}, 10)
                scroll.assert_called_once()

    def test_scan_skips_task_point_pages_and_keeps_reading_pages(self):
        video = '<iframe module="insertaudio" src="/ananas/modules/audio/index.html"></iframe>'
        first = {**RESOURCE, "id": "video-page", "name": "video"}
        second = {**RESOURCE, "id": "reading-page"}
        self.responses(video, READ_PAGE)
        with unittest.mock.patch("api.course_tools.CourseTools.scan_course", return_value=[first, second]):
            found = self.service.scan_course(COURSE)
        self.assertEqual([item["id"] for item in found], ["reading-page"])
        self.assertEqual(found[0]["required_minutes"], 60)



if __name__ == "__main__":
    unittest.main()
