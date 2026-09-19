import errno
import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
from requests.structures import CaseInsensitiveDict
from urllib3.util.retry import Retry

from api.course_tools import (
    CARDS_URL, COURSE_URL, STAT_INDEX_URL, STAT_TIME_URL, STATUS_URL, STUDY_URL,
    VIDEO_REFERER, VISITS_URL, CourseTools, ToolCancelled,
)
from api.session import HTTP_TIMEOUT, SessionManager


COURSE = {"courseId": "course-1", "clazzId": "class-1", "cpi": "enrollment-1", "title": "离线课程"}
DEFAULTS = {
    "courseId": "course-1", "clazzId": "class-1", "userid": "uid-1",
    "reportUrl": "https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/enrollment-1",
    "reportTimeInterval": 60,
}
VIDEO = {
    "type": "video", "objectId": "object-1", "jobid": "job-1", "isPassed": True,
    "otherInfo": "node-rt_d&courseId=obsolete", "property": {"name": "已完成 视频", "duration": 125},
}


def course_page(chapters=((101, "第一章", False),)):
    entries = []
    for identifier, title, locked in chapters:
        tips = "请先完成前置章节解锁" if locked else "已完成"
        entries.append(
            f'<li><div id="cur{identifier}"><div><a class="clicktitle">{title}</a>'
            f'<span class="bntHoverTips">{tips}</span>'
            '<input class="knowledgeJobCount" value="0"></div></div></li>'
        )
    return '<div class="fanyaChapterWhite"><div class="chapter_unit"><ul>' + "".join(entries) + "</ul></div></div>"


def card_page(attachments, defaults=None):
    data = {"defaults": DEFAULTS if defaults is None else defaults, "attachments": attachments}
    # Real card pages initialize mArg before assigning the resource object.
    return '<script>var mArg = "";\ntry { mArg = ' + json.dumps(data, ensure_ascii=False) + "; }catch (e) {}</script>"


def resource(kind="video", name="视频"):
    attachment = deepcopy(VIDEO)
    attachment["type"] = kind
    return {
        "id": "resource-1", "course_id": COURSE["courseId"], "course_title": COURSE["title"],
        "chapter_id": "101", "chapter_title": "第一章", "name": name, "kind": kind,
        "downloadable": True, "watchable": kind == "video",
        "_attachment": attachment, "_defaults": deepcopy(DEFAULTS),
    }


class Response:
    def __init__(self, payload="", *, status=200, headers=None, chunks=()):
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload
        self.status_code = status
        self.headers = CaseInsensitiveDict(headers or {})
        self.chunks = chunks
        self.closed = False
        self.chunk_sizes = []

    @property
    def content(self):
        raise AssertionError("Downloads must be streamed")

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size):
        self.chunk_sizes.append(chunk_size)
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        if duration <= 0:
            raise AssertionError("Waits must move forward")
        self.sleeps.append(duration)
        self.now += duration


class OfflineToolsCase(unittest.TestCase):
    def setUp(self):
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Offline tests only"))
        network.start()
        self.addCleanup(network.stop)
        cookies = patch("api.session.use_cookies", side_effect=AssertionError("Do not read real cookies"))
        cookies.start()
        self.addCleanup(cookies.stop)
        self.adapter = SimpleNamespace(max_retries=Retry(total=2))
        self.session = SimpleNamespace(
            get=Mock(), post=Mock(), get_adapter=Mock(return_value=self.adapter),
        )
        self.manager = SimpleNamespace(get_session=Mock(return_value=self.session))
        self.client = SimpleNamespace(
            session_manager=self.manager, get_fid=Mock(return_value="fid-1"),
            get_uid=Mock(return_value="uid-1"), get_job_list=Mock(),
        )
        self.tools = CourseTools(self.client)

    def fake_clock(self):
        clock = Clock()
        for name in ("monotonic", "sleep"):
            replacement = patch(f"api.course_tools.time.{name}", getattr(clock, name))
            replacement.start()
            self.addCleanup(replacement.stop)
        return clock

    def assert_closed(self, *responses):
        for response in responses:
            self.assertTrue(response.closed, "Response was not closed")


class ScanTests(OfflineToolsCase):
    def test_reads_every_actual_card_and_keeps_completed_and_non_job_media(self):
        original = deepcopy(VIDEO)
        original["property"]["name"] = "已完成 视频 {有空格}"
        audio = {"type": "video", "property": {"module": "insertaudio", "objectid": "audio", "name": "音频"}}
        document = {"type": "document", "isPassed": True, "property": {"objectid": "doc", "name": "讲义"}}
        file = {"type": "file", "property": {"objectid": "file", "name": "附件"}}
        batches = [[original], [original], [audio], [document], [file]] + [[]] * 5
        responses = [Response(course_page()), Response('<input id="cardcount" value="10">')]
        responses += [Response(card_page(batch)) for batch in batches]
        self.session.get.side_effect = responses
        progress = Mock()

        found = self.tools.scan_course(COURSE, progress)

        self.assertEqual([item["kind"] for item in found], ["video", "audio", "document", "file"])
        self.assertEqual(found[0]["name"], "已完成 视频 {有空格}")
        self.assertTrue(found[0]["watchable"])
        self.assertTrue(found[0]["_attachment"]["isPassed"])
        self.assertTrue(all(item["downloadable"] for item in found))
        self.assertFalse(found[1]["watchable"])
        self.assertEqual(found[0]["course_id"], COURSE["courseId"])
        self.assertEqual(found[0]["duration"], 125)
        cards = [call.kwargs["params"]["num"] for call in self.session.get.call_args_list if call.args[0] == CARDS_URL]
        self.assertEqual(cards, list(range(10)))
        self.assertEqual(self.session.get.call_args_list[1].args[0], STUDY_URL)
        progress.assert_called_once_with("第一章", 1, 1)
        self.client.get_job_list.assert_not_called()
        self.assert_closed(*responses)

    def test_identifier_survives_title_signature_and_completion_changes(self):
        changed = deepcopy(VIDEO)
        changed["isPassed"] = False
        changed["property"]["name"] = "new title"
        new_defaults = {**DEFAULTS, "ktoken": "new private signature"}
        self.session.get.side_effect = [
            Response(course_page()), Response('<input id="cardcount" value="1">'), Response(card_page([VIDEO])),
            Response(course_page()), Response('<input id="cardcount" value="1">'), Response(card_page([changed], new_defaults)),
        ]
        first = self.tools.scan_course(COURSE)[0]
        second = self.tools.scan_course(COURSE)[0]
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["name"], second["name"])

    def test_locked_chapters_are_logged_and_counted_without_opening_them(self):
        responses = [
            Response(course_page(((101, "锁定章节", True), (102, "可读章节", False)))),
            Response('<input id="cardcount" value="0">'),
        ]
        self.session.get.side_effect = responses
        progress = Mock()
        with patch("api.course_tools.logger.warning") as warning:
            self.assertEqual(self.tools.scan_course(COURSE, progress), [])
        self.assertEqual([call.args for call in progress.call_args_list], [
            ("锁定章节", 1, 2), ("可读章节", 2, 2),
        ])
        self.assertEqual(self.session.get.call_args_list[1].kwargs["params"]["chapterId"], "102")
        warning.assert_called_once()
        self.assert_closed(*responses)

    def test_missing_chapter_identity_without_explicit_lock_evidence_is_an_error(self):
        malformed = course_page(((101, "标识损坏的章节", False),)).replace(' id="cur101"', "")
        for page in (malformed, malformed.replace("已完成", "已解锁"), malformed.replace("已完成", "")):
            with self.subTest(page=page):
                response = Response(page)
                self.session.get.reset_mock()
                self.session.get.side_effect = [response]
                progress = Mock()
                with patch("api.course_tools.logger.warning") as warning:
                    with self.assertRaisesRegex(RuntimeError, "标识无法解析"):
                        self.tools.scan_course(COURSE, progress)
                progress.assert_not_called()
                warning.assert_not_called()
                self.session.get.assert_called_once()
                self.assert_closed(response)

    def test_missing_chapter_identity_with_explicit_lock_tip_is_skipped(self):
        page = course_page(((101, "真正锁定的章节", True),)).replace(' id="cur101"', "")
        response = Response(page)
        self.session.get.side_effect = [response]
        progress = Mock()
        with patch("api.course_tools.logger.warning") as warning:
            self.assertEqual(self.tools.scan_course(COURSE, progress), [])
        progress.assert_called_once_with("真正锁定的章节", 1, 1)
        warning.assert_called_once()
        self.session.get.assert_called_once()
        self.assert_closed(response)

    def test_server_locked_page_is_skipped_but_malformed_pages_raise(self):
        locked = Response("章节未开放")
        self.session.get.side_effect = [Response(course_page()), locked]
        with patch("api.course_tools.logger.warning"):
            self.assertEqual(self.tools.scan_course(COURSE), [])
        self.assert_closed(locked)
        cases = [
            [Response("<html>new unrecognized layout</html>")],
            [Response('<form action="/login"><input type="password"></form>')],
            [Response(course_page()), Response("<div>missing count</div>")],
            [Response(course_page()), Response('<input id="cardcount" value="-1">')],
            [Response(course_page()), Response('<input id="cardcount" value="10001">')],
            [Response(course_page()), Response('<input id="cardcount" value="1">'), Response("<div>no resource data</div>")],
            [Response(course_page()), Response('<input id="cardcount" value="1">'), Response("<script>mArg = {broken};</script>")],
            [Response(course_page()), Response('<input id="cardcount" value="1">'), Response('<script>mArg = {"attachments":null};</script>')],
            [Response(course_page()), Response('<input id="cardcount" value="1">'), Response('<script>mArg = {"newFormat":[]};</script>')],
            [Response(course_page()), Response('<input id="cardcount" value="1">'), Response('<script>mArg = {"status":false,"attachments":[]};</script>')],
        ]
        for responses in cases:
            with self.subTest(page=responses[-1].text):
                self.session.get.side_effect = responses
                with self.assertRaises(RuntimeError):
                    self.tools.scan_course(COURSE)
                self.assert_closed(*responses)

    def test_later_request_failure_does_not_erase_completed_chapter_progress(self):
        responses = [
            Response(course_page(((101, "第一章", False), (102, "第二章", False)))),
            Response('<input id="cardcount" value="0">'),
            Response('<input id="cardcount" value="1">'),
            Response(status=503),
        ]
        self.session.get.side_effect = responses
        progress = Mock()
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            self.tools.scan_course(COURSE, progress)
        progress.assert_called_once_with("第一章", 1, 2)
        self.assert_closed(*responses)

    def test_recognized_empty_cards_and_empty_course_are_valid(self):
        responses = [
            Response(course_page()), Response('<input id="cardcount" value="2">'),
            Response("<script>mArg = {};</script>"), Response(card_page([])),
        ]
        self.session.get.side_effect = responses
        self.assertEqual(self.tools.scan_course(COURSE), [])
        self.assert_closed(*responses)
        self.session.get.side_effect = [Response('<div class="fanyaChapterWhite">暂无章节</div>')]
        self.assertEqual(self.tools.scan_course(COURSE), [])

    def test_card_placeholder_without_resource_assignment_is_not_an_empty_catalogue(self):
        pages = (
            '<script>var mArg = "";</script>',
            '<script>var mArg = null; try {}catch (e) {}</script>',
            '<script>var mArg = ""; var other = {"defaults": {}, "attachments": []};</script>',
        )
        for page in pages:
            with self.subTest(page=page):
                responses = [
                    Response(course_page()), Response('<input id="cardcount" value="1">'), Response(page),
                ]
                self.session.get.side_effect = responses
                progress = Mock()
                with self.assertRaises(RuntimeError):
                    self.tools.scan_course(COURSE, progress)
                progress.assert_not_called()
                self.assert_closed(*responses)

    def test_malformed_resource_object_after_placeholder_still_fails(self):
        assignments = (
            "{broken}",
            '{"defaults": {}, "attachments": [}',
            '{"defaults": null, "attachments": []}',
            '{"defaults": {}, "attachments": null}',
            '{"defaults": {}, "attachments": [42]}',
            '{"defaults": {}, "attachments": [], "status": false}',
        )
        for assignment in assignments:
            with self.subTest(assignment=assignment):
                page = '<script>var mArg = ""; try { mArg = ' + assignment + "; }catch (e) {}</script>"
                responses = [
                    Response(course_page()), Response('<input id="cardcount" value="1">'), Response(page),
                ]
                self.session.get.side_effect = responses
                progress = Mock()
                with self.assertRaises(RuntimeError):
                    self.tools.scan_course(COURSE, progress)
                progress.assert_not_called()
                self.assert_closed(*responses)

    def test_public_resource_is_an_allowlist_and_does_not_publish_signed_metadata(self):
        item = {**resource(), "duration": 12, "url": "private link", "cookies": {"secret": "cookie"}}
        public = self.tools.public_resource(item)
        self.assertEqual(set(public), {
            "id", "course_id", "course_title", "chapter_id", "chapter_title",
            "name", "kind", "downloadable", "watchable", "duration",
        })
        self.assertNotIn("private", json.dumps(public))
        self.assertNotIn("reportUrl", json.dumps(public))
        public["name"] = "changed"
        self.assertNotEqual(public["name"], item["name"])

    def test_already_cancelled_operations_issue_no_requests(self):
        tools = CourseTools(self.client, cancel_check=lambda: True)
        operations = [
            lambda: tools.scan_course(COURSE),
            lambda: tools.get_statistics(COURSE),
            lambda: tools.add_visits(COURSE, 1, 1),
            lambda: tools.watch_video(COURSE, resource(), 6),
            lambda: tools.download_resource(COURSE, resource(), "unused"),
        ]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ToolCancelled):
                operation()
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()


class StatisticsAndVisitsTests(OfflineToolsCase):
    def test_statistics_use_current_year_month_and_parse_fractional_minutes(self):
        counts = Response({"total": "21"})
        index = Response("<script>var jobEnc = 'fresh-stat-signature';</script>")
        times = Response('<div class="min fl"><span>1,234.5</span> 分钟</div><p>总时长：2,345.75 分钟</p>')
        self.session.post.return_value = counts
        self.session.get.side_effect = [index, times]
        with patch("api.course_tools.datetime") as current:
            current.now.return_value = datetime(2033, 4, 15)
            result = self.tools.get_statistics(COURSE)
        self.assertEqual(result, {"visits": 21, "watched_minutes": 1234.5, "total_minutes": 2345.75, "warnings": []})
        self.assertEqual(self.session.post.call_args.args, (VISITS_URL,))
        self.assertEqual(self.session.post.call_args.kwargs["data"]["year"], 2033)
        self.assertEqual(self.session.post.call_args.kwargs["data"]["month"], "04")
        self.assertEqual(self.session.get.call_args_list[0].args[0], STAT_INDEX_URL)
        self.assertEqual(self.session.get.call_args_list[1].args[0], STAT_TIME_URL)
        self.assertEqual(self.session.get.call_args_list[1].kwargs["params"]["pEnc"], "fresh-stat-signature")
        self.assert_closed(counts, index, times)

    def test_unavailable_statistics_remain_unknown_with_warnings(self):
        counts, index = Response({"status": False, "total": 0}), Response("<div>unexpected page</div>")
        self.session.post.return_value = counts
        self.session.get.return_value = index
        result = self.tools.get_statistics(COURSE)
        self.assertIsNone(result["visits"])
        self.assertIsNone(result["watched_minutes"])
        self.assertIsNone(result["total_minutes"])
        self.assertEqual(len(result["warnings"]), 2)
        self.assert_closed(counts, index)

    def setup_visits(self, reports):
        before, after = Response({"total": 10}), Response({"total": 11})
        page = Response('<script src="https://fystat-ans.chaoxing.com/log/setlog?enc=private&amp;courseId=course-1"></script>')
        self.session.post.side_effect = [before, after]
        self.session.get.side_effect = [page, *reports]
        return before, after, page

    def test_successful_requests_are_distinct_from_actual_platform_visit_totals(self):
        reports = [Response({"status": True}), Response(""), Response(status=204)]
        before, after, page = self.setup_visits(reports)
        clock = self.fake_clock()
        progress = Mock()
        result = self.tools.add_visits(COURSE, 3, 1.25, progress)
        self.assertEqual(result, {"before": 10, "after": 11, "submitted": 3, "warnings": []})
        self.assertAlmostEqual(clock.now, 2.5)
        self.assertTrue(all(wait <= 0.2 for wait in clock.sleeps))
        self.assertEqual([call.args for call in progress.call_args_list], [(1, 3), (2, 3), (3, 3)])
        self.assertIn("&courseId=course-1", self.session.get.call_args.args[0])
        self.assertNotIn("&amp;", self.session.get.call_args.args[0])
        self.assert_closed(before, after, page, *reports)

    def test_quoted_visit_success_records_each_request_and_preserves_platform_totals(self):
        clock = self.fake_clock()
        for payload in ("'success'", '"success"'):
            with self.subTest(payload=payload):
                reports = [
                    Response(payload, headers={"Content-Type": "text/html;charset=UTF-8", "Content-Length": "9"})
                    for _ in range(2)
                ]
                before, after, page = self.setup_visits(reports)
                progress = Mock()
                started = clock.now
                result = self.tools.add_visits(COURSE, 2, 1.25, progress)
                self.assertEqual(result, {"before": 10, "after": 11, "submitted": 2, "warnings": []})
                self.assertEqual([call.args for call in progress.call_args_list], [(1, 2), (2, 2)])
                self.assertAlmostEqual(clock.now - started, 1.25)
                self.assert_closed(before, after, page, *reports)

    def test_failed_strings_and_extra_javascript_do_not_advance_visit_progress(self):
        self.fake_clock()
        for payload in (
            "'failure'", '"failed"', "'false'", "false",
            "'success'; unexpected()", '"success"; unexpected()',
            "'success' + 'extra'", "var result = 'success';",
        ):
            with self.subTest(payload=payload):
                first = Response({"status": True})
                failed = Response(payload, headers={"Content-Type": "text/html;charset=UTF-8"})
                before, _, page = self.setup_visits([first, failed])
                post_count = self.session.post.call_count
                progress = Mock()
                with self.assertRaises(RuntimeError):
                    self.tools.add_visits(COURSE, 3, 1, progress)
                progress.assert_called_once_with(1, 3)
                self.assertEqual(self.session.post.call_count - post_count, 1)
                self.assert_closed(before, page, first, failed)

    def test_non_idempotent_visit_get_disables_adapter_retries_even_on_http_failure(self):
        original_retries = self.adapter.max_retries
        before = Response({"total": 0})
        page = Response('<script src="https://fystat-ans.chaoxing.com/log/setlog?enc=private"></script>')
        denied = Response(status=503)
        observed = []

        def get(url, **kwargs):
            if "/setlog" in url:
                observed.append(self.adapter.max_retries.total)
                return denied
            return page

        self.session.get.side_effect = get
        self.session.post.return_value = before
        progress = Mock()
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            self.tools.add_visits(COURSE, 1, 1, progress)
        self.assertEqual(observed, [0])
        self.assertIs(self.adapter.max_retries, original_retries)
        progress.assert_not_called()
        self.assertEqual(self.session.get.call_count, 2)
        self.assert_closed(before, page, denied)

    def test_business_failure_stops_without_counting_failed_request(self):
        first, failed = Response({"status": True}), Response({"status": False})
        before, _, page = self.setup_visits([first, failed])
        self.fake_clock()
        progress = Mock()
        with self.assertRaisesRegex(RuntimeError, "被平台拒绝"):
            self.tools.add_visits(COURSE, 3, 1, progress)
        progress.assert_called_once_with(1, 3)
        self.assertEqual(self.session.post.call_count, 1)
        self.assert_closed(before, page, first, failed)

    def test_unknown_or_html_report_response_is_not_counted(self):
        for payload in ({}, {"code": None}, {"code": False}, {"error": "expired"}, False, "<html>login</html>"):
            with self.subTest(payload=payload):
                failed = Response(payload)
                self.setup_visits([failed])
                progress = Mock()
                with self.assertRaises(RuntimeError):
                    self.tools.add_visits(COURSE, 1, 1, progress)
                progress.assert_not_called()
                self.assert_closed(failed)

    def test_cancel_during_visit_wait_preserves_completed_callback(self):
        self.setup_visits([Response({"status": True})])
        clock = self.fake_clock()
        tools = CourseTools(self.client, lambda: clock.now >= 0.4)
        progress = Mock()
        with self.assertRaises(ToolCancelled):
            tools.add_visits(COURSE, 3, 2, progress)
        progress.assert_called_once_with(1, 3)
        self.assertLess(clock.now, 1)
        self.assertEqual(self.session.get.call_count, 2)

    def test_cancel_arriving_during_successful_request_still_checkpoints_it(self):
        cancelled = False
        page = Response('<script src="https://fystat-ans.chaoxing.com/log/setlog?enc=private"></script>')
        report = Response({"status": True})

        def get(url, **kwargs):
            nonlocal cancelled
            if "/setlog" in url:
                cancelled = True
                return report
            return page

        self.session.get.side_effect = get
        self.session.post.return_value = Response({"total": 1})
        progress = Mock()
        with self.assertRaises(ToolCancelled):
            CourseTools(self.client, lambda: cancelled).add_visits(COURSE, 2, 1, progress)
        progress.assert_called_once_with(1, 2)
        self.assert_closed(page, report)

    def test_signed_visit_address_cannot_send_account_cookies_to_another_host(self):
        for host in ("fystat-ans.chaoxing.com.evil.invalid", "s3.cldisk.com"):
            with self.subTest(host=host):
                page = Response(f'<script src="https://{host}/log/setlog?enc=private"></script>')
                self.session.post.return_value = Response({"total": 0})
                self.session.get.return_value = page
                before = self.session.get.call_count
                with self.assertRaisesRegex(ValueError, "受信任"):
                    self.tools.add_visits(COURSE, 1, 1)
                self.assertEqual(self.session.get.call_count - before, 1)
                self.assert_closed(page)

    def test_each_client_borrows_only_its_account_session_without_reading_cookie_files(self):
        services = []
        for account in ("offline-alice", "offline-bob"):
            manager = SessionManager(account)
            self.addCleanup(manager.close)
            manager.set_cookies({"_uid": account})
            session = manager.get_session()
            client = SimpleNamespace(session_manager=manager)
            service = CourseTools(client)
            observed = []

            def post(*args, current=session, observations=observed, **kwargs):
                observations.append(current.cookies.get("_uid"))
                return Response({"total": 3})

            get = patch.object(session, "get", side_effect=[
                Response('<script src="https://fystat-ans.chaoxing.com/log/setlog?enc=private"></script>'),
                Response({"status": True}),
            ])
            with get, patch.object(session, "post", side_effect=post):
                self.assertEqual(service.add_visits(COURSE, 1, 1)["submitted"], 1)
            self.assertEqual(observed, [account, account])
            services.append(session)
        self.assertIsNot(services[0], services[1])


class VideoTests(OfflineToolsCase):
    def setup_video(self, duration, report_count):
        status = Response({"status": "success", "duration": duration, "dtoken": "signed-token"})
        reports = [Response({"isPassed": True}) for _ in range(report_count)]
        self.session.get.side_effect = [status, *reports]
        return status, reports

    def test_short_target_waits_real_time_and_signs_the_actual_partial_position(self):
        status, reports = self.setup_video(12, 2)
        clock = self.fake_clock()
        progress = Mock()
        result = self.tools.watch_video(COURSE, resource(), 6, progress)
        calls = self.session.get.call_args_list[1:]
        self.assertEqual(result, {"seconds": 6})
        self.assertAlmostEqual(clock.now, 6)
        self.assertEqual([call.kwargs["params"]["playingTime"] for call in calls], [0, 6])
        self.assertEqual([call.kwargs["params"]["isdrag"] for call in calls], [3, 0])
        final = calls[-1].kwargs["params"]
        expected = "[class-1][uid-1][job-1][object-1][6000][d_yHJ!$pdA~5][12000][0_12]"
        self.assertEqual(final["enc"], hashlib.md5(expected.encode()).hexdigest())
        self.assertEqual(final["otherInfo"], "node-rt_d")
        self.assertEqual(final["courseId"], COURSE["courseId"])
        self.assertEqual([call.args for call in progress.call_args_list], [(0, 6), (6, 6)])
        self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in calls))
        self.assertTrue(all(call.kwargs["headers"]["Connection"] == "close" for call in calls))
        self.assertTrue(all(call.kwargs["headers"]["Referer"] == VIDEO_REFERER for call in calls))
        self.assert_closed(status, *reports)

    def test_video_heartbeats_close_idle_connections_and_do_not_retry_connection_errors(self):
        status = Response({"status": "success", "duration": 12, "dtoken": "signed-token"})
        original_retries = self.adapter.max_retries
        retries = []
        connections = []

        def get(url, **kwargs):
            if "/multimedia/log/" in url:
                retries.append(self.adapter.max_retries.total)
                connections.append(kwargs["headers"].get("Connection"))
                raise requests.ConnectionError("Remote end closed connection")
            return status

        self.session.get.side_effect = get
        clock = self.fake_clock()
        progress = Mock()
        with self.assertRaisesRegex(RuntimeError, "网络请求失败（ConnectionError）"):
            self.tools.watch_video(COURSE, resource(), 6, progress)
        progress.assert_not_called()
        self.assertEqual(clock.now, 0)
        self.assertEqual(retries, [0])
        self.assertEqual(connections, ["close"])
        self.assertIs(self.adapter.max_retries, original_retries)
        self.assert_closed(status)

    def test_completed_video_restarts_at_boundaries_for_requested_extra_time(self):
        status, reports = self.setup_video(7, 6)
        clock = self.fake_clock()
        progress = Mock()
        result = self.tools.watch_video(COURSE, resource(), 17, progress)
        calls = self.session.get.call_args_list[1:]
        positions = [call.kwargs["params"]["playingTime"] for call in calls]
        self.assertEqual(positions, [0, 7, 0, 7, 0, 3])
        self.assertEqual([call.kwargs["params"]["isdrag"] for call in calls], [3, 4, 3, 4, 3, 0])
        self.assertTrue(all(0 <= position <= 7 for position in positions))
        self.assertAlmostEqual(clock.now, 17)
        self.assertEqual(result["seconds"], 17)
        self.assertEqual([call.args[0] for call in progress.call_args_list], [0, 7, 14, 17])
        self.assert_closed(status, *reports)

    def test_heartbeat_interval_and_final_remainder_cover_exact_full_duration(self):
        self.setup_video(125, 4)
        clock = self.fake_clock()
        progress = Mock()
        self.tools.watch_video(COURSE, resource(), 125, progress)
        calls = self.session.get.call_args_list[1:]
        self.assertEqual([call.kwargs["params"]["playingTime"] for call in calls], [0, 60, 120, 125])
        self.assertEqual([call.kwargs["params"]["isdrag"] for call in calls], [3, 0, 0, 4])
        self.assertEqual([call.args[0] for call in progress.call_args_list], [0, 60, 120, 125])
        self.assertAlmostEqual(clock.now, 125)

    def test_subsecond_media_and_remainders_never_overrun_duration(self):
        self.setup_video(0.3, 6)
        clock = self.fake_clock()
        result = self.tools.watch_video(COURSE, resource(), 0.65)
        calls = self.session.get.call_args_list[1:]
        self.assertEqual([call.kwargs["params"]["playingTime"] for call in calls], [0, 0.3, 0, 0.3, 0, 0.05])
        self.assertEqual(result, {"seconds": 0.65})
        self.assertAlmostEqual(clock.now, 0.65)
        raw = "[class-1][uid-1][job-1][object-1][50][d_yHJ!$pdA~5][300][0_0.3]"
        self.assertEqual(calls[-1].kwargs["params"]["enc"], hashlib.md5(raw.encode()).hexdigest())

    def test_failed_heartbeat_does_not_advance_submitted_time_or_retry(self):
        status = Response({"status": "success", "duration": 12, "dtoken": "signed-token"})
        start, failed = Response({"isPassed": True}), Response({"status": False, "isPassed": False})
        original_retries = self.adapter.max_retries
        retries = []
        responses = iter([status, start, failed])

        def get(url, **kwargs):
            if "/multimedia/log/" in url:
                retries.append(self.adapter.max_retries.total)
            return next(responses)

        self.session.get.side_effect = get
        clock = self.fake_clock()
        progress = Mock()
        with self.assertRaisesRegex(RuntimeError, "被平台拒绝"):
            self.tools.watch_video(COURSE, resource(), 6, progress)
        progress.assert_called_once_with(0, 6)
        self.assertAlmostEqual(clock.now, 6)
        self.assertEqual(retries, [0, 0])
        self.assertIs(self.adapter.max_retries, original_retries)
        self.assert_closed(status, start, failed)

    def test_visit_string_acknowledgements_are_not_valid_video_heartbeats(self):
        self.fake_clock()
        for payload in ("'success'", '"success"', "success"):
            with self.subTest(payload=payload):
                status = Response({"status": "success", "duration": 12, "dtoken": "signed-token"})
                start = Response({"isPassed": False})
                failed = Response(payload, headers={"Content-Type": "text/html;charset=UTF-8"})
                self.session.get.side_effect = [status, start, failed]
                progress = Mock()
                with self.assertRaises(RuntimeError):
                    self.tools.watch_video(COURSE, resource(), 6, progress)
                progress.assert_called_once_with(0, 6)
                self.assert_closed(status, start, failed)

    def test_cancel_during_playback_stops_before_next_heartbeat(self):
        status, reports = self.setup_video(120, 1)
        clock = self.fake_clock()
        progress = Mock()
        with self.assertRaises(ToolCancelled):
            CourseTools(self.client, lambda: clock.now >= 0.4).watch_video(COURSE, resource(), 60, progress)
        self.assertEqual(self.session.get.call_count, 2)
        self.assertLess(clock.now, 1)
        progress.assert_called_once_with(0, 60)
        self.assert_closed(status, *reports)

    def test_invalid_status_duration_token_or_response_never_counts_time(self):
        cases = [
            {"status": "failed", "duration": 12, "dtoken": "token"},
            {"status": "success", "duration": 0, "dtoken": "token"},
            {"status": "success", "duration": "nan", "dtoken": "token"},
            {"status": "success", "duration": 12},
            {"status": "success", "dtoken": "token"},
        ]
        clock = self.fake_clock()
        for payload in cases:
            with self.subTest(payload=payload):
                status = Response(payload)
                self.session.get.side_effect = [status]
                progress = Mock()
                with self.assertRaises(RuntimeError):
                    self.tools.watch_video(COURSE, resource(), 6, progress)
                progress.assert_not_called()
                self.assert_closed(status)
        self.assertEqual(clock.now, 0)

    def test_foreign_account_or_course_parameters_are_rejected(self):
        for key, value in (("userid", "other-account"), ("clazzId", "other-class"), ("courseId", "other-course")):
            with self.subTest(key=key):
                item = resource()
                item["_defaults"][key] = value
                with self.assertRaises(ValueError):
                    self.tools.watch_video(COURSE, item, 6)
        self.session.get.assert_not_called()

    def test_video_report_url_cannot_target_arbitrary_host_or_path(self):
        for url in (
            "https://example.invalid/multimedia/log/a/id",
            "https://mooc1.chaoxing.com/other/path",
            "https://mooc1.chaoxing.com.evil.invalid/multimedia/log/a/id",
            "https://s3.cldisk.com/multimedia/log/a/id",
        ):
            with self.subTest(url=url):
                item = resource()
                item["_defaults"]["reportUrl"] = url
                self.session.get.side_effect = [Response({"status": "success", "duration": 10, "dtoken": "token"})]
                with self.assertRaises(ValueError):
                    self.tools.watch_video(COURSE, item, 6)
        self.assertEqual(self.session.get.call_count, 4)

    def test_invalid_completion_flag_is_not_a_successful_heartbeat(self):
        status = Response({"status": "success", "duration": 12, "dtoken": "token"})
        invalid = Response({"status": True, "isPassed": "unexpected"})
        self.session.get.side_effect = [status, invalid]
        progress = Mock()
        with self.assertRaisesRegex(RuntimeError, "无效的完成状态"):
            self.tools.watch_video(COURSE, resource(), 6, progress)
        progress.assert_not_called()
        self.assert_closed(status, invalid)


class DownloadTests(OfflineToolsCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)

    def setup_download(self, response, *, url="https://s1.ananas.chaoxing.com/file/asset.mp4", filename="asset.mp4", pdf=None):
        data = {"status": "success", "filename": filename, "http": url}
        if pdf:
            data["pdf"] = pdf
        status = Response(data)
        self.session.get.side_effect = [status, response]
        return status

    def test_streaming_pdf_conversion_uses_safe_name_and_preserves_existing_files(self):
        pdf = "https://s1.ananas.chaoxing.com/document/asset.pdf?token=private"
        first = Response(headers={"content-length": "6"}, chunks=[b"abc", b"", b"def"])
        status = self.setup_download(first, filename="source.docx", pdf=pdf)
        progress = Mock()
        result = self.tools.download_resource(COURSE, resource("document", "CON.docx"), self.output, progress)
        path = Path(result["path"])
        self.assertEqual(path.name, "_CON.pdf")
        self.assertEqual(path.read_bytes(), b"abcdef")
        self.assertEqual(result["bytes"], 6)
        self.assertEqual([call.args for call in progress.call_args_list], [(0, 6), (3, 6), (6, 6)])
        self.assertEqual(first.chunk_sizes, [256 * 1024])
        self.assertEqual(self.session.get.call_args.kwargs["timeout"], HTTP_TIMEOUT)
        self.assertTrue(self.session.get.call_args.kwargs["stream"])
        self.assertNotIn("Host", self.session.get.call_args.kwargs["headers"])
        self.assertNotIn("cookies", self.session.get.call_args.kwargs)
        self.assert_closed(status, first)

        second = Response(headers={"Content-Length": "3"}, chunks=[b"new"])
        self.setup_download(second, filename="source.docx", pdf=pdf)
        next_result = self.tools.download_resource(COURSE, resource("document", "CON.docx"), self.output)
        self.assertEqual(Path(next_result["path"]).name, "_CON (1).pdf")
        self.assertEqual(path.read_bytes(), b"abcdef")
        self.assertEqual(Path(next_result["path"]).read_bytes(), b"new")
        self.assertFalse(list(self.output.glob("*.part")))

    def test_unknown_length_still_downloads_with_indeterminate_progress(self):
        body = Response(chunks=[b"", b"abc"])
        status = self.setup_download(body)
        progress = Mock()
        result = self.tools.download_resource(COURSE, resource(name="../../evil\\name:*?.mp4"), self.output, progress)
        path = Path(result["path"])
        self.assertEqual(path.parent, self.output.resolve())
        self.assertEqual(path.read_bytes(), b"abc")
        self.assertFalse(any(character in path.name for character in '<>:"/\\|?*'))
        self.assertEqual([call.args for call in progress.call_args_list], [(0, None), (3, None)])
        self.assert_closed(status, body)

    def test_empty_resource_is_valid_when_declared_length_is_zero(self):
        self.setup_download(Response(headers={"Content-Length": "0"}))
        result = self.tools.download_resource(COURSE, resource(name="empty"), self.output)
        self.assertEqual(result["bytes"], 0)
        self.assertEqual(Path(result["path"]).read_bytes(), b"")

    def test_book_file_with_only_a_pdf_link_gets_the_converted_extension(self):
        status = Response({
            "status": "success", "pagenum": 5, "filename": "source.docx",
            "pdf": "https://s1.ananas.chaoxing.com/document/book.pdf",
        })
        body = Response(headers={"Content-Length": "4"}, chunks=[b"%PDF"])
        self.session.get.side_effect = [status, body]
        result = self.tools.download_resource(COURSE, resource("file", "教材.docx"), self.output)
        self.assertEqual(Path(result["path"]).name, "教材.pdf")
        self.assertEqual(Path(result["path"]).read_bytes(), b"%PDF")
        self.assert_closed(status, body)

    def test_document_metadata_can_select_the_cldisk_pdf(self):
        pdf = "https://s3.cldisk.com/document/book.pdf?token=private"
        body = Response(headers={"Content-Length": "8", "Content-Type": "application/pdf"}, chunks=[b"%PDF-1.7"])
        status = self.setup_download(
            body, url="http://cs.cldisk.com/document/book", filename="source.docx", pdf=pdf,
        )
        result = self.tools.download_resource(COURSE, resource("document", "教材.docx"), self.output)
        self.assertEqual(self.session.get.call_args.args[0], pdf)
        self.assertEqual(Path(result["path"]).name, "教材.pdf")
        self.assertEqual(Path(result["path"]).read_bytes(), b"%PDF-1.7")
        self.assertNotIn("cookies", self.session.get.call_args.kwargs)
        self.assertNotIn("Cookie", self.session.get.call_args.kwargs["headers"])
        self.assert_closed(status, body)

    def test_cldisk_initial_urls_and_redirects_are_upgraded_to_https(self):
        status = Response({"status": "success", "filename": "asset.pdf", "download": "http://d0.cldisk.com/asset"})
        redirect = Response(status=302, headers={"Location": "http://s3.cldisk.com/document/asset.pdf"})
        body = Response(headers={"Content-Length": "8"}, chunks=[b"%PDF-1.7"])
        self.session.get.side_effect = [status, redirect, body]
        result = self.tools.download_resource(COURSE, resource("file", "教材"), self.output)
        calls = self.session.get.call_args_list[1:]
        self.assertEqual([call.args[0] for call in calls], [
            "https://d0.cldisk.com/asset", "https://s3.cldisk.com/document/asset.pdf",
        ])
        self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in calls))
        self.assertTrue(all("cookies" not in call.kwargs and "Cookie" not in call.kwargs["headers"] for call in calls))
        self.assertEqual(Path(result["path"]).read_bytes(), b"%PDF-1.7")
        self.assert_closed(status, redirect, body)

    def test_bad_status_length_truncation_and_error_html_leave_no_files(self):
        bodies = [
            Response(status=500), Response(status=206),
            Response(headers={"Content-Length": "-1"}, chunks=[b"abc"]),
            Response(headers={"Content-Length": "unknown"}, chunks=[b"abc"]),
            Response(headers={"Content-Length": "5"}, chunks=[b"abc"]),
            Response(headers={"Content-Length": "2"}, chunks=[b"abc"]),
            Response(headers={"Content-Type": "text/html; charset=utf-8"}, chunks=[b"<html>login</html>"]),
            Response(headers={"Content-Type": "application/json"}, chunks=[b'{"status":false}']),
            Response(headers={"Content-Encoding": "gzip"}, chunks=[b"abc"]),
        ]
        for body in bodies:
            with self.subTest(status=body.status_code, headers=body.headers):
                status = self.setup_download(body)
                with self.assertRaises(RuntimeError):
                    self.tools.download_resource(COURSE, resource(), self.output)
                self.assertEqual(list(self.output.iterdir()), [])
                self.assert_closed(status, body)

    def test_broken_stream_closes_response_removes_parts_and_hides_signed_url(self):
        body = Response(chunks=[b"abc", requests.exceptions.ReadTimeout("signed-token-secret")])
        status = self.setup_download(body)
        with self.assertRaisesRegex(RuntimeError, "下载连接中断") as caught:
            self.tools.download_resource(COURSE, resource(), self.output)
        self.assertNotIn("signed-token-secret", str(caught.exception))
        self.assertEqual(list(self.output.iterdir()), [])
        self.assert_closed(status, body)

    def test_cancellation_and_callback_failure_both_clean_partial_download(self):
        for failure in ("cancel", "callback"):
            with self.subTest(failure=failure):
                stopped = False
                body = Response(headers={"Content-Length": "6"}, chunks=[b"abc", b"def"])
                status = self.setup_download(body)

                def progress(received, total):
                    nonlocal stopped
                    if received:
                        if failure == "callback":
                            raise RuntimeError("checkpoint failed")
                        stopped = True

                tools = CourseTools(self.client, lambda: stopped)
                with self.assertRaises(ToolCancelled if failure == "cancel" else RuntimeError):
                    tools.download_resource(COURSE, resource(), self.output, progress)
                self.assertEqual(list(self.output.iterdir()), [])
                self.assert_closed(status, body)

    def test_every_redirect_is_validated_and_closed_before_next_request(self):
        redirect = Response(status=302, headers={"Location": "http://s2.ananas.chaoxing.com/file.mp4?token=new"})
        body = Response(headers={"Content-Length": "3"}, chunks=[b"abc"])
        status = Response({"status": "success", "filename": "asset.mp4", "http": "https://s1.ananas.chaoxing.com/file.mp4"})
        self.session.get.side_effect = [status, redirect, body]
        result = self.tools.download_resource(COURSE, resource(), self.output)
        self.assertEqual(Path(result["path"]).read_bytes(), b"abc")
        self.assertTrue(self.session.get.call_args.args[0].startswith("https://s2.ananas.chaoxing.com/"))
        self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in self.session.get.call_args_list))
        self.assert_closed(status, redirect, body)

    def test_untrusted_initial_urls_and_redirects_never_receive_a_request(self):
        urls = [
            "https://127.0.0.1/resource", "https://example.invalid/resource",
            "https://s1.ananas.chaoxing.com.evil.invalid/resource",
            "https://evilchaoxing.com/resource", "file:///tmp/private",
            "https://s3.cldisk.com.evil.invalid/resource", "https://evilcldisk.com/resource",
            "https://user:password@s1.ananas.chaoxing.com/resource",
            "https://s1.ananas.chaoxing.com:8080/resource",
            "https://s1.ananas.chaoxing.com\\@example.invalid/resource",
        ]
        for url in urls:
            for redirect in (False, True):
                with self.subTest(url=url, redirect=redirect):
                    if redirect:
                        reply = Response(status=302, headers={"Location": url})
                        status = self.setup_download(reply)
                    else:
                        status = Response({"status": "success", "filename": "asset.mp4", "http": url})
                        self.session.get.side_effect = [status]
                    before = self.session.get.call_count
                    with self.assertRaises(ValueError):
                        self.tools.download_resource(COURSE, resource(), self.output)
                    self.assertEqual(self.session.get.call_count - before, 2 if redirect else 1)
                    self.assertEqual(list(self.output.iterdir()), [])
                    self.assert_closed(status)
                    if redirect:
                        self.assert_closed(reply)

    def test_redirect_loop_is_bounded_and_all_responses_close(self):
        status = Response({"status": "success", "http": "https://s1.ananas.chaoxing.com/loop"})
        redirects = [Response(status=302, headers={"Location": "/loop"}) for _ in range(6)]
        self.session.get.side_effect = [status, *redirects]
        with self.assertRaisesRegex(RuntimeError, "重定向次数过多"):
            self.tools.download_resource(COURSE, resource(), self.output)
        self.assert_closed(status, *redirects)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_missing_media_url_and_business_failure_close_metadata_response(self):
        for payload in ({"status": "success"}, {"status": "error", "http": "https://s1.ananas.chaoxing.com/a"}):
            with self.subTest(payload=payload):
                status = Response(payload)
                self.session.get.side_effect = [status]
                with self.assertRaises((ValueError, RuntimeError)):
                    self.tools.download_resource(COURSE, resource(), self.output)
                self.assert_closed(status)
                self.assertEqual(list(self.output.iterdir()), [])

    def test_publication_on_filesystems_without_hard_links_is_exclusive(self):
        existing = self.output / "same.mp4"
        existing.write_bytes(b"original")
        body = Response(headers={"Content-Length": "3"}, chunks=[b"new"])
        self.setup_download(body)
        with patch("api.course_tools.os.link", side_effect=OSError(errno.ENOTSUP, "unsupported")):
            result = self.tools.download_resource(COURSE, resource(name="same.mp4"), self.output)
        self.assertEqual(existing.read_bytes(), b"original")
        self.assertEqual(Path(result["path"]).name, "same (1).mp4")
        self.assertEqual(Path(result["path"]).read_bytes(), b"new")
        self.assertEqual(len(list(self.output.iterdir())), 2)

    def test_symlinked_download_directory_is_rejected(self):
        outside = self.output / "actual"
        outside.mkdir()
        link = self.output / "link"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Symlink creation unavailable: {exc}")
        status = Response({"status": "success", "http": "https://s1.ananas.chaoxing.com/file.mp4"})
        self.session.get.side_effect = [status]
        with self.assertRaisesRegex(ValueError, "链接"):
            self.tools.download_resource(COURSE, resource(), link)
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_closed(status)


if __name__ == "__main__":
    unittest.main()
