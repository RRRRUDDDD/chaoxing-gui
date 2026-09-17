"""Offline reading task accounting, selection and persistence contracts."""

from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from api import course_tool_tasks as tasks
from api.course_tools import ToolCancelled
from api.task_state import TaskNotFound, TaskStore


COURSE = {"courseId": "course1", "clazzId": "class1", "cpi": "cpi1", "title": "课程一"}
READING = {
    "id": "reading1", "course_id": "course1", "course_title": "课程一",
    "chapter_id": "chapter1", "chapter_title": "阅读章节", "name": "课程阅读",
    "kind": "read", "readable": True, "watchable": False, "downloadable": False,
    "required_minutes": 60, "read_minutes": 4.3, "book_count": 3,
}
OPTIONS = {"source_task_id": "source", "resource_ids": ["reading1"], "minutes": 0.1}


class ReadingTaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = TaskStore(state_file=self.directory / "tasks.json")
        self.addCleanup(self.store.close)
        self.client = MagicMock()
        self.client.login.return_value = {"status": True}
        self.client.get_course_list.return_value = [COURSE]
        self.service = MagicMock()
        self.fresh = dict(READING, _attachment={"enc": "private-fresh-token"},
                          _books=[{"url": "https://mooc1.chaoxing.com/private"}])
        self.service.scan_course.return_value = [self.fresh]
        self.service.public_resource.side_effect = lambda item: item
        self.service.watch_reading.return_value = {"seconds": 6, "before": 4.3, "after": 4.3}
        factory = patch("api.course_tool_tasks.create_service", return_value=self.service)
        self.addCleanup(factory.stop)
        self.create_service = factory.start()
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("offline only"))
        self.addCleanup(network.stop)
        network.start()

    @contextmanager
    def factory(self, username, password):
        try:
            yield self.client
        finally:
            self.client.close()

    def create(self, kind="reading_time"):
        config = {"username": "alice", "password": "never-store-password", "use_cookies": True,
                  "course_list": ["course1"], "task_type": kind,
                  "tool_options": {"purpose": "reading_time"} if kind == "catalog" else dict(OPTIONS)}
        resources = [] if kind == "catalog" else [READING]
        task_id = self.store.create("alice", tasks.initial_status(config),
                                    tasks.initial_details(config, resources),
                                    resume_config=tasks.resume_config(config))
        return task_id, config

    def run_task(self, task_id, config):
        tasks.run_tool_task(task_id, self.store, config, self.directory, self.factory)

    def test_catalog_retains_reading_requirements_but_never_private_urls_or_tokens(self):
        task_id, config = self.create("catalog")
        self.run_task(task_id, config)
        self.assertEqual(self.store.get_status(task_id)["status"], "completed")
        self.assertEqual(self.store.get_status(task_id)["task_label"], "读取阅读任务")
        self.assertEqual(self.store.get_details(task_id)["tool"]["resources"], [READING])
        stored = (self.directory / "tasks.json").read_text(encoding="utf-8")
        for secret in ("private-fresh-token", "https://", "never-store-password"):
            self.assertNotIn(secret, stored)
        self.service.watch_reading.assert_not_called()

    def test_reading_refreshes_metadata_and_separates_reports_from_platform_minutes(self):
        task_id, config = self.create()
        tool = self.store.get_details(task_id)["tool"]
        self.assertEqual((tool["unit"], tool["total_units"]), ("秒", 6))
        observed = []
        self.client.close.side_effect = lambda: observed.append(self.store.get_status(task_id)["status"])
        self.run_task(task_id, config)
        self.service.watch_reading.assert_called_once()
        args = self.service.watch_reading.call_args.args
        self.assertEqual(args, (COURSE, self.fresh, 6))
        self.service.watch_video.assert_not_called()
        self.service.download_resource.assert_not_called()
        self.assertEqual(observed, ["running"])
        tool = self.store.get_details(task_id)["tool"]
        result = tool["results"][0]
        self.assertEqual((result["seconds"], result["before"], result["after"]), (6, 4.3, 4.3))
        self.assertEqual(tool["completed_units"], 6)
        self.assertIn("次日", result["message"])
        self.assertNotIn("达标", result["message"])

    def test_cancellation_keeps_acknowledged_seconds_and_never_counts_remaining_time(self):
        task_id, config = self.create()

        def read(course, resource, seconds, on_progress):
            on_progress(5, seconds)
            self.store.request_cancel(task_id, "alice")
            raise ToolCancelled("已停止")

        self.service.watch_reading.side_effect = read
        self.run_task(task_id, config)
        tool = self.store.get_details(task_id)["tool"]
        self.assertEqual(self.store.get_status(task_id)["status"], "cancelled")
        self.assertEqual(tool["completed_units"], 5)
        self.assertEqual(tool["results"][0]["seconds"], 5)
        self.assertIsNone(tool["current"])

    def test_report_failure_preserves_confirmed_progress_as_partial(self):
        task_id, config = self.create()

        def read(course, resource, seconds, on_progress):
            on_progress(5, seconds)
            raise RuntimeError("阅读记录被平台拒绝")

        self.service.watch_reading.side_effect = read
        self.run_task(task_id, config)
        self.assertEqual(self.store.get_status(task_id)["status"], "partial")
        self.assertEqual(self.store.get_details(task_id)["tool"]["completed_units"], 5)

    def test_checkpoint_failure_stops_reports_without_discarding_acknowledgement(self):
        task_id, config = self.create()

        def read(course, resource, seconds, on_progress):
            on_progress(5, seconds)
            self.fail("No report may follow a failed checkpoint")

        self.service.watch_reading.side_effect = read
        with patch.object(self.store, "checkpoint", side_effect=OSError("disk full")):
            self.run_task(task_id, config)
        self.assertEqual(self.store.get_status(task_id)["status"], "partial")
        self.assertEqual(self.store.get_details(task_id)["tool"]["completed_units"], 5)
        self.service.watch_reading.assert_called_once()

    def test_selection_requires_own_reading_catalog_and_explicit_readable_flag(self):
        source, config = self.create("catalog")
        self.run_task(source, config)
        options = dict(OPTIONS, source_task_id=source)
        self.assertEqual(tasks.selected_resources(self.store, "alice", ["course1"], "reading_time", options), [READING])
        with self.assertRaises(TaskNotFound):
            tasks.selected_resources(self.store, "bob", ["course1"], "reading_time", options)
        with self.assertRaises(ValueError):
            tasks.selected_resources(self.store, "alice", ["course1"], "video_time", options)
        for value in (False, "true", 1, None):
            with self.subTest(value=value):
                with self.store.edit(source) as task:
                    task.details["tool"]["resources"][0]["readable"] = value
                with self.assertRaises(ValueError):
                    tasks.selected_resources(self.store, "alice", ["course1"], "reading_time", options)


class ReadingOptionTests(unittest.TestCase):
    def test_minutes_normalize_and_catalog_accepts_reading_purpose(self):
        self.assertEqual(tasks.parse_options("catalog", {"purpose": "reading_time"}), {"purpose": "reading_time"})
        self.assertEqual(tasks.parse_options("reading_time", dict(OPTIONS, minutes="0.1")), OPTIONS)
        for value in (True, False, float("nan"), float("inf"), 0, 0.09, 1441, [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                tasks.parse_options("reading_time", dict(OPTIONS, minutes=value))
        for field in ("url", "cookie", "interval", "speed", "window"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                tasks.parse_options("reading_time", dict(OPTIONS, **{field: "untrusted"}))


if __name__ == "__main__":
    unittest.main()
