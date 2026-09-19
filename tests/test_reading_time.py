"""Protocol-level tests for the background专题阅读 service."""

from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock

from api.reading_time import ReadingTools, _scroll_after


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

    def test_readlog_requires_exact_empty_json_and_does_not_retry(self):
        context = {"courseid": "242311696", "chapterid": "847466162", "height": "470",
                   "query": {"_from_": "course-1_class-1_user_sig"}, "url": "https://mooc1.chaoxing.com/mooc-ans/course/242311696.html"}
        self.responses("{}")
        self.service._readlog(context, 0)
        self.assertEqual(self.session.get.call_args.args[0], "https://mooc1.chaoxing.com/multimedia/readlog")
        self.assertEqual(self.session.get.call_args.kwargs["params"]["h"], "0")
        self.assertEqual(self.adapter.max_retries.total, 2)
        self.assertTrue(self.session.get.call_args.kwargs["allow_redirects"] is False)
        for payload in ("", "<html></html>", json.dumps({"status": False}), json.dumps({"status": True})):
            with self.subTest(payload=payload):
                self.responses(payload)
                with self.assertRaises(RuntimeError):
                    self.service._readlog(context, 380)

    def test_watch_reading_counts_only_acknowledged_five_second_periods(self):
        self.responses(BOOK_PAGE, CARDS_PAGE, "{}", "{}", "{}", READ_PAGE)
        result = self.service.watch_reading(COURSE, {**RESOURCE, "_books": [{
            "url": "https://mooc1.chaoxing.com/mooc-ans/course/242311696.html?_from_=course-1_class-1_user_sig",
            "title": "专题书", "author": "作者"}]}, 10)
        self.assertEqual(result["seconds"], 10)
        self.assertEqual(self.service._wait.call_args_list[0].args, (5.0,))
        self.assertEqual(self.service._wait.call_count, 2)
        scroll = [call.kwargs["params"]["h"] for call in self.session.get.call_args_list
                  if call.args and str(call.args[0]).endswith("/multimedia/readlog")]
        self.assertEqual(scroll, ["0", "380", "760"])


    def test_scroll_sequence_matches_captured_page(self):
        position, seen = 0, [0]
        for index in range(7):
            position = _scroll_after(position, index)
            seen.append(position)
        self.assertEqual(seen, [0, 380, 760, 480, 860, 1240, 960, 1340])

    def test_readlog_uses_book_origin_and_rejects_static_zero_as_only_input(self):
        context = {"courseid": "242311696", "chapterid": "847466162", "height": "470",
                   "query": {"_from_": "course-1_class-1_user_sig"},
                   "url": "https://mooc2.chaoxing.com/mooc-ans/zt/242311696.html"}
        self.responses("{}", "{}")
        self.service._readlog(context, 0)
        self.service._readlog(context, 380)
        urls = [call.args[0] for call in self.session.get.call_args_list]
        self.assertEqual(urls, ["https://mooc2.chaoxing.com/multimedia/readlog"] * 2)
        self.assertEqual([call.kwargs["params"]["h"] for call in self.session.get.call_args_list], ["0", "380"])
        with self.assertRaises(ValueError):
            self.service._readlog(context, -1)

    def test_public_resource_excludes_private_protocol_fields(self):
        resource = {**RESOURCE, "readable": True, "required_minutes": 60, "read_minutes": 4.3,
                    "book_count": 1, "_books": [{"url": "https://mooc1.chaoxing.com/mooc-ans/course/1.html"}]}
        public = self.service.public_resource(resource)
        self.assertEqual(public["book_count"], 1)
        self.assertNotIn("_attachment", public)
        self.assertNotIn("_books", public)


if __name__ == "__main__":
    unittest.main()
