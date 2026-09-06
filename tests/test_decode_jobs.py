import json
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import main
from api.base import Account, Chaoxing, StudyResult
from api.decode import decode_course_card


def card_page(attachments, **defaults):
    data = {"attachments": attachments, "defaults": defaults}
    return "<script>mArg=" + json.dumps(data, separators=(",", ":")) + ";</script>"


def identity(job):
    return {key: job[key] for key in ("type", "jobid", "objectid", "id") if job.get(key)}


class DecodeJobsTests(unittest.TestCase):
    def setUp(self):
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Offline parsing only"))
        network.start()
        self.addCleanup(network.stop)

    def test_mixed_pending_and_passed_tasks_keep_separate_identities(self):
        cards = [
            {"type": "document", "job": True, "jobid": "pending", "isPassed": "false"},
            {"type": "document", "jobid": "finished", "isPassed": "true"},
        ]
        pending, info = decode_course_card(card_page(cards, knowledgeid="chapter", ktoken="offline"))
        self.assertEqual([job["jobid"] for job in pending], ["pending"])
        self.assertEqual(info["passed_jobs"], [{"type": "document", "jobid": "finished"}])
        self.assertEqual(info["knowledgeid"], "chapter")
        self.assertEqual(info["ktoken"], "offline")

    def test_passed_tasks_match_pending_identity_even_without_job_or_playback_fields(self):
        cards = [
            {"type": "video", "job": True, "jobid": "video-job", "objectId": "video-object", "mid": "m"},
            {"type": "document", "job": True, "jobid": "document-job", "property": {"objectid": "document-object"}},
            {"type": "workid", "job": True, "jobid": "work-job"},
            {"type": "video", "job": True, "jobid": "live-job", "property": {"liveId": "live-id"}},
            {"type": "read", "jobid": "read-job", "property": {"id": "read-id", "read": False}},
        ]
        for card in cards:
            with self.subTest(card=card):
                pending, before = decode_course_card(card_page([card]))
                self.assertEqual(len(pending), 1)
                finished = deepcopy(card)
                finished["isPassed"] = True
                finished.pop("job", None)
                finished.pop("mid", None)
                remaining, after = decode_course_card(card_page([finished]))
                self.assertEqual(remaining, [])
                self.assertEqual(before["passed_jobs"], [])
                self.assertEqual(after["passed_jobs"], [identity(pending[0])])

    def test_object_and_read_ids_are_preserved_when_jobid_is_absent(self):
        cards = [
            {"type": "video", "job": True, "objectId": "video-object", "mid": "m"},
            {"type": "document", "job": True, "property": {"objectid": "document-object"}},
            {"type": "read", "property": {"id": "read-id", "read": False}},
            {"type": "live", "job": True, "id": 42},
        ]
        for card in cards:
            with self.subTest(card=card):
                pending, _ = decode_course_card(card_page([card]))
                finished = dict(card, isPassed=True)
                finished.pop("job", None)
                _, info = decode_course_card(card_page([finished]))
                self.assertEqual(info["passed_jobs"], [identity(pending[0])])

    def test_read_true_is_explicit_completion_without_is_passed(self):
        for flag in (True, 1, 1.0, "1", "true", " TRUE ", "passed"):
            with self.subTest(flag=flag):
                card = {"type": "read", "jobid": "read-job", "property": {"id": "read-id", "read": flag}}
                pending, info = decode_course_card(card_page([card]))
                self.assertEqual(pending, [])
                self.assertEqual(info["passed_jobs"], [{"type": "read", "jobid": "read-job", "id": "read-id"}])

    def test_false_completion_markers_do_not_count_as_passed(self):
        for flag in (False, 0, 0.0, "0", "false", " FALSE ", "", None, "unknown"):
            with self.subTest(flag=flag):
                cards = [
                    {"type": "read", "jobid": "read-job", "isPassed": flag, "property": {"read": flag}},
                    {"type": "document", "job": True, "jobid": "document-job", "isPassed": flag},
                ]
                pending, info = decode_course_card(card_page(cards))
                self.assertEqual([job["jobid"] for job in pending], ["read-job", "document-job"])
                self.assertEqual(info["passed_jobs"], [])

    def test_passed_boolean_variants_do_not_require_job(self):
        for flag in (True, 1, 1.0, "1", "true", " TRUE ", "yes", "y", "passed"):
            with self.subTest(flag=flag):
                card = {"type": "document", "jobid": "document-job", "isPassed": flag}
                pending, info = decode_course_card(card_page([card]))
                self.assertEqual(pending, [])
                self.assertEqual(info["passed_jobs"], [{"type": "document", "jobid": "document-job"}])

    def test_live_hints_keep_the_same_identity_after_job_field_disappears(self):
        variants = [
            {"type": "live"},
            {"type": "livestream"},
            {"type": "video", "property": {"type": "LiveResource"}},
            {"type": "video", "property": {"resourceType": "LIVE"}},
            {"type": "video", "property": {"liveId": "live-id"}},
            {"type": "video", "property": {"streamName": "stream"}},
            {"type": "video", "property": {"vdoid": 0}},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                card = dict(variant, job=True, jobid="live-job", objectId="live-object")
                pending, _ = decode_course_card(card_page([card]))
                finished = dict(card, isPassed=True)
                finished.pop("job")
                remaining, info = decode_course_card(card_page([finished]))
                self.assertEqual(remaining, [])
                self.assertEqual(pending[0]["type"], "live")
                self.assertEqual(info["passed_jobs"], [identity(pending[0])])

    def test_non_read_markers_and_non_task_attachments_preserve_pending_parsing(self):
        cards = [
            {"type": "video", "job": True, "jobid": "video-job", "objectId": "video-object", "mid": "m",
             "otherInfo": "kept&courseId=removed", "property": {"read": True, "name": "clip"}},
            {"type": "document", "job": True, "jobid": "document-job", "isPassed": "false",
             "property": {"read": "true", "objectid": "document-object"}},
            {"type": "workid", "job": True, "jobid": "work-job", "property": {"read": 1}},
            {"type": "read", "jobid": "read-job", "property": {"id": "read-id", "read": "false"}},
            {"type": "document", "jobid": "non-task", "isPassed": "false"},
            {"type": "video", "job": True, "jobid": "missing-playback-fields"},
        ]
        pending, info = decode_course_card(card_page(cards))
        self.assertEqual([identity(job) for job in pending], [
            {"type": "video", "jobid": "video-job", "objectid": "video-object"},
            {"type": "document", "jobid": "document-job", "objectid": "document-object"},
            {"type": "workid", "jobid": "work-job"},
            {"type": "read", "jobid": "read-job", "id": "read-id"},
        ])
        self.assertEqual(pending[0]["otherinfo"], "kept")
        self.assertEqual(pending[0]["mid"], "m")
        self.assertEqual(pending[0]["name"], "clip")
        self.assertEqual(info["passed_jobs"], [])

    def test_blank_or_non_scalar_identities_are_not_completion_evidence(self):
        for value in (None, "", " ", True, False, [], {}):
            with self.subTest(value=value):
                cards = [
                    {"type": kind, "jobid": value, "isPassed": True}
                    for kind in ("video", "document", "workid", "read", "live")
                ]
                cards.append({"type": "live", "id": value, "isPassed": True})
                pending, info = decode_course_card(card_page(cards))
                self.assertEqual(pending, [])
                self.assertEqual(info["passed_jobs"], [])

    def test_unknown_and_unidentified_attachments_are_not_successful_tasks(self):
        cards = [
            {"type": "iframe", "jobid": "not-a-job", "isPassed": True},
            {"id": "decorative", "isPassed": True},
            {"type": "video", "isPassed": True},
            {"type": "document", "isPassed": True},
            {"type": "workid", "isPassed": True},
            {"type": "live", "isPassed": True},
            {"type": "read", "property": {"read": True}},
        ]
        pending, info = decode_course_card(card_page(cards))
        self.assertEqual(pending, [])
        self.assertEqual(info["passed_jobs"], [])

    def test_empty_and_unopened_cards_always_have_empty_passed_list(self):
        pages = ("<div></div>", "<script>mArg={};</script>", card_page([]), "章节未开放")
        for page in pages:
            with self.subTest(page=page):
                pending, info = decode_course_card(page)
                self.assertEqual(pending, [])
                self.assertEqual(info["passed_jobs"], [])
        self.assertTrue(decode_course_card("章节未开放")[1]["notOpen"])


class DecodeJobResultIntegrationTests(unittest.TestCase):
    def setUp(self):
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Offline parsing only"))
        network.start()
        self.addCleanup(network.stop)
        self.client = Chaoxing(account=Account("offline-decode", "secret"), tiku=None)
        self.client.rate_limiter = Mock()
        self.addCleanup(self.client.close)
        self.session = SimpleNamespace(get=Mock())
        session_patch = patch.object(self.client.session_manager, "get_session", return_value=self.session)
        session_patch.start()
        self.addCleanup(session_patch.stop)
        empty_patch = patch.object(self.client, "study_emptypage", return_value=StudyResult.SUCCESS)
        self.empty = empty_patch.start()
        self.addCleanup(empty_patch.stop)
        self.course = {"courseId": "course", "clazzId": "class", "cpi": "cpi", "title": "Course"}
        self.chapter = {"id": "chapter", "title": "Chapter", "jobCount": 1, "has_finished": False}

    def set_card_pages(self, pages):
        responses = []
        for page in pages:
            response = requests.Response()
            response.status_code = 200
            response.encoding = "utf-8"
            response._content = page.encode("utf-8")
            response.url = "https://example.invalid/cards"
            responses.append(response)
        self.session.get.side_effect = responses

    def test_all_cards_reach_chapter_counts_without_an_empty_page_request(self):
        document = {"type": "document", "jobid": "shared", "isPassed": "true"}
        self.set_card_pages([
            card_page([document, {"type": "read", "property": {"id": "read-id", "read": True}}]),
            card_page([
                {"type": "workid", "jobid": "shared", "isPassed": True},
                {"type": "video", "objectId": "video-object", "isPassed": True},
                {"type": "live", "id": 42, "isPassed": True},
            ]),
            card_page([document]),
        ] + ["<div></div>"] * 4)
        done = Mock()
        with patch("main.process_job") as run:
            result = main.process_chapter(self.client, self.course, self.chapter, 1,
                                          {"chapter_done_callback": done})
        self.assertEqual(result, main.ChapterResult.SUCCESS)
        self.assertEqual(self.chapter["_task_stats"], {"total": 5, "completed": 5, "failed": 0, "skipped": 0})
        self.assertEqual(set(self.chapter["_job_results"]), {
            ("document", "shared"), ("workid", "shared"), ("read", "read-id"),
            ("video", "video-object"), ("live", "42"),
        })
        self.assertEqual(self.session.get.call_count, 7)
        run.assert_not_called()
        self.empty.assert_not_called()
        done.assert_called_once_with(self.course, self.chapter)

    def test_explicit_evidence_recovers_retry_with_stable_fallback_identities(self):
        video = {"type": "video", "job": True, "mid": "m", "objectId": "video-object"}
        document = {"type": "document", "job": True, "property": {"objectid": "document-object"}}
        read = {"type": "read", "property": {"id": "read-id", "read": False}}
        snapshots = [
            [video, document, read],
            [
                {"type": "video", "objectId": "video-object", "isPassed": "true"},
                document,
                {"type": "read", "property": {"id": "read-id", "read": "true"}},
            ],
            [{"type": "document", "property": {"objectid": "document-object"}, "isPassed": True}],
        ]
        self.set_card_pages([page for cards in snapshots for page in [card_page(cards)] + ["<div></div>"] * 6])
        done = Mock()
        with patch("main.process_job", return_value=StudyResult.ERROR) as run:
            result = main.process_chapter(self.client, self.course, self.chapter, 1,
                                          {"chapter_done_callback": done})
            self.assertEqual(result, main.ChapterResult.ERROR)
            self.assertEqual(self.chapter["_task_stats"], {"total": 3, "completed": 0, "failed": 3, "skipped": 0})
            result = main.process_chapter(self.client, self.course, self.chapter, 1,
                                          {"chapter_done_callback": done})
            self.assertEqual(result, main.ChapterResult.ERROR)
            self.assertEqual(self.chapter["_task_stats"], {"total": 3, "completed": 2, "failed": 1, "skipped": 0})
            done.assert_not_called()
            result = main.process_chapter(self.client, self.course, self.chapter, 1,
                                          {"chapter_done_callback": done})
        self.assertEqual(result, main.ChapterResult.SUCCESS)
        self.assertEqual(self.chapter["_task_stats"], {"total": 3, "completed": 3, "failed": 0, "skipped": 0})
        self.assertEqual(run.call_count, 4)
        self.empty.assert_not_called()
        done.assert_called_once_with(self.course, self.chapter)

    def test_missing_failed_task_without_evidence_stays_failed_after_empty_page(self):
        self.set_card_pages([
            card_page([{"type": "document", "job": True, "jobid": "failed-job"}]),
        ] + ["<div></div>"] * 6 + [
            card_page([
                {"type": "iframe", "jobid": "failed-job", "isPassed": True},
                {"type": "read", "property": {"read": True}},
                {"type": "document", "jobid": "failed-job", "isPassed": "false"},
            ]),
        ] + ["<div></div>"] * 6)
        done = Mock()
        with patch("main.process_job", return_value=StudyResult.ERROR) as run:
            for _ in range(2):
                result = main.process_chapter(self.client, self.course, self.chapter, 1,
                                              {"chapter_done_callback": done})
                self.assertEqual(result, main.ChapterResult.ERROR)
                self.assertEqual(self.chapter["_task_stats"], {"total": 1, "completed": 0, "failed": 1, "skipped": 0})
        run.assert_called_once()
        self.empty.assert_called_once_with(self.course, self.chapter)
        done.assert_not_called()


if __name__ == "__main__":
    unittest.main()
