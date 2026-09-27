"""Protocol-level tests for the background专题阅读 service."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from api.reading_time import ReadingTools


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
CARDS_PAGE = '<div id="pageDiv">内容</div>'


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
        self.responses(BOOK_PAGE, CARDS_PAGE, READ_PAGE)
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

    def test_video_chapter_is_skipped_for_a_later_reading_chapter(self):
        book = '<script>window["courseid"] = "242311696"; var ctid = 11; var courseChapterId = 22;</script>'
        video = '<div class="ans-cc"><iframe module="insertvideo" src="/ananas/modules/video/index.html"></iframe></div>'
        reading = '<div id="pageDiv"><div id="courseMainBox">正文</div></div>'
        self.responses(book, video, reading)
        context = self.service._book_context({
            "url": "https://mooc1.chaoxing.com/mooc-ans/course/242311696.html?_from_=course-1_class-1_user_sig",
        }, course=COURSE)
        self.assertEqual(context["chapterid"], "22")
        params = [call.kwargs.get("params", {}).get("knowledgeid") for call in self.session.get.call_args_list]
        params = [item for item in params if item]
        self.assertEqual(params, ["11", "22"])

    def test_only_task_point_chapters_are_rejected(self):
        book = '<script>window["courseid"] = "242311696"; var ctid = 11;</script>'
        video = '<iframe module="insertdoc" src="/ananas/modules/pdf/index.html"></iframe>'
        self.responses(book, video)
        with self.assertRaises(RuntimeError) as raised:
            self.service._book_context({
                "url": "https://mooc1.chaoxing.com/mooc-ans/course/242311696.html?_from_=course-1_class-1_user_sig",
            }, course=COURSE)
        self.assertIn("不是阅读页", str(raised.exception))

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
