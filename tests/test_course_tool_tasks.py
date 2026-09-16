"""Offline lifecycle tests for course utilities; never access account files."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from api.task_state import TaskAlreadyRunning, TaskStore
from api import course_tool_tasks as tasks


COURSE = {"courseId": "c1", "clazzId": "cl1", "cpi": "cp1", "title": "课程一"}
RESOURCE = {
    "id": "resource1", "course_id": "c1", "course_title": "课程一",
    "chapter_id": "ch1", "chapter_title": "第一章", "name": "演示视频",
    "kind": "video", "watchable": True, "downloadable": True,
}


class CourseToolTaskTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        self.state_file = self.data_dir / "tasks.json"
        self.store = TaskStore(state_file=self.state_file)
        self.addCleanup(self.store.close)
        self.client = MagicMock()
        self.client.login.return_value = {"status": True}
        self.client.get_course_list.return_value = [COURSE]
        self.service = MagicMock()
        self.service.scan_course.return_value = [dict(RESOURCE, _attachment={"dtoken": "private-token"})]
        self.service.public_resource.side_effect = lambda resource: {key: value for key, value in resource.items() if not key.startswith("_")}
        self.service.add_visits.return_value = {"before": 12, "after": 14, "submitted": 2}
        self.service.watch_video.return_value = {"seconds": 6}
        self.patch("api.course_tool_tasks.create_service", return_value=self.service)
        self.patch("requests.sessions.Session.request", side_effect=AssertionError("offline tests only"))

    def patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @contextmanager
    def factory(self, username, password):
        try:
            yield self.client
        finally:
            self.client.close()

    def create(self, kind="visits", options=None, resources=None):
        if options is None:
            options = {"count": 2, "interval": 1}
        config = {"username": "alice", "password": "secret", "use_cookies": False,
                  "course_list": ["c1"], "task_type": kind, "tool_options": options}
        task_id = self.store.create(
            "alice", tasks.initial_status(config), tasks.initial_details(config, resources or []),
            resume_config=tasks.resume_config(config),
        )
        return task_id, config

    def run_task(self, kind="visits", options=None, resources=None):
        task_id, config = self.create(kind, options, resources)
        tasks.run_tool_task(task_id, self.store, config, self.data_dir, self.factory)
        return task_id

    def test_visits_counts_submissions_separately_and_closes_before_terminal(self):
        observed = []
        self.client.close.side_effect = lambda: observed.append(self.store.get_status(task_id)["status"])
        task_id, config = self.create()
        tasks.run_tool_task(task_id, self.store, config, self.data_dir, self.factory)
        self.assertEqual(observed, ["running"])
        self.assertEqual(self.store.get_status(task_id)["status"], "completed")
        result = self.store.get_details(task_id)["tool"]["results"][0]
        self.assertEqual((result["before"], result["after"], result["submitted"]), (12, 14, 2))
        self.assertNotIn("secret", self.state_file.read_text(encoding="utf-8"))

    def test_catalog_does_not_publish_private_attachment_parameters(self):
        task_id = self.run_task("catalog", {"purpose": "download"})
        self.assertEqual(self.store.get_status(task_id)["status"], "completed")
        self.assertEqual(self.store.get_details(task_id)["tool"]["resources"], [RESOURCE])
        self.assertNotIn("private-token", self.state_file.read_text(encoding="utf-8"))

    def test_cancel_during_visits_keeps_submitted_count_and_releases_account_after_close(self):
        task_id, config = self.create()

        def add_visits(course, count, interval, on_progress=None):
            on_progress(1, count)
            self.store.request_cancel(task_id, "alice")
            raise RuntimeError("cancelled")

        self.service.add_visits.side_effect = add_visits
        tasks.run_tool_task(task_id, self.store, config, self.data_dir, self.factory)
        self.assertEqual(self.store.get_status(task_id)["status"], "cancelled")
        self.assertEqual(self.store.get_details(task_id)["tool"]["completed_units"], 1)
        self.client.close.assert_called_once()
        self.create()

    def test_upstream_failures_are_not_success_and_final_logs_are_visible(self):
        self.service.add_visits.side_effect = RuntimeError("statistics endpoint unavailable")
        task_id = self.run_task()
        self.assertEqual(self.store.get_status(task_id)["status"], "error")
        self.assertEqual(self.store.get_details(task_id)["tool"]["results"][0]["status"], "error")
        self.assertTrue(any("unavailable" in item["message"] for item in self.store.read_logs(task_id)["data"]))

    def test_video_refreshes_resource_metadata_instead_of_reusing_catalog_tokens(self):
        task_id = self.run_task("video_time", {"source_task_id": "source", "resource_ids": ["resource1"], "minutes": 0.1}, [RESOURCE])
        self.assertEqual(self.store.get_status(task_id)["status"], "completed")
        args = self.service.watch_video.call_args.args
        self.assertIn("_attachment", args[1])
        self.assertEqual(args[2], 6)

    def test_missing_selected_resource_is_reported(self):
        self.service.scan_course.return_value = []
        task_id = self.run_task("download", {"source_task_id": "source", "resource_ids": ["resource1"]}, [RESOURCE])
        self.assertEqual(self.store.get_status(task_id)["status"], "error")
        self.service.download_resource.assert_not_called()

    def test_failed_download_does_not_claim_discarded_bytes_were_saved(self):
        def download(course, resource, directory, on_progress=None):
            on_progress(100, 200)
            raise RuntimeError("truncated transfer")

        self.service.download_resource.side_effect = download
        task_id = self.run_task("download", {"source_task_id": "source", "resource_ids": ["resource1"]}, [RESOURCE])
        self.assertEqual(self.store.get_status(task_id)["status"], "error")
        tool = self.store.get_details(task_id)["tool"]
        self.assertEqual(tool["completed_units"], 0)
        self.assertEqual(tool["results"][0]["bytes"], 0)
        self.assertNotIn("path", tool["results"][0])

    def test_cancelled_download_does_not_claim_discarded_bytes_were_saved(self):
        task_id, config = self.create("download", {"source_task_id": "source", "resource_ids": ["resource1"]}, [RESOURCE])

        def download(course, resource, directory, on_progress=None):
            on_progress(100, None)
            self.store.request_cancel(task_id, "alice")
            raise RuntimeError("stopped")

        self.service.download_resource.side_effect = download
        tasks.run_tool_task(task_id, self.store, config, self.data_dir, self.factory)
        self.assertEqual(self.store.get_status(task_id)["status"], "cancelled")
        self.assertEqual(self.store.get_details(task_id)["tool"]["completed_units"], 0)

    def test_real_download_checkpoint_failure_removes_part_and_resets_unsaved_bytes(self):
        from api.course_tools import CourseTools

        task_id, config = self.create("download", {"source_task_id": "source", "resource_ids": ["resource1"]}, [RESOURCE])
        service = CourseTools(self.client, cancel_check=lambda: self.store.is_cancelled(task_id))
        resource = dict(RESOURCE, _attachment={"objectId": "object1", "property": {}}, _defaults={})
        response = MagicMock()
        response.headers = {"Content-Length": "20"}
        response.iter_content.return_value = iter([b"0123456789"])
        original_checkpoint = self.store.checkpoint
        calls = 0

        def checkpoint(identifier):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full")
            original_checkpoint(identifier)

        with patch("api.course_tool_tasks.create_service", return_value=service), \
                patch.object(service, "scan_course", return_value=[resource]), \
                patch.object(service, "_media_status", return_value={"filename": "video.mp4", "http": "https://cdn.chaoxing.com/video.mp4"}), \
                patch.object(service, "_open_download", return_value=response), \
                patch("api.course_tool_tasks.time.monotonic", side_effect=[0, 6]), \
                patch.object(self.store, "checkpoint", side_effect=checkpoint):
            tasks.run_tool_task(task_id, self.store, config, self.data_dir, self.factory)
        tool = self.store.get_details(task_id)["tool"]
        self.assertEqual(self.store.get_status(task_id)["status"], "error")
        self.assertEqual(tool["completed_units"], 0)
        self.assertIsNone(tool["current"])
        self.assertEqual(tool["results"], [])
        self.assertEqual(list(tasks.download_directory(self.data_dir, task_id).iterdir()), [])
        response.close.assert_called_once()

    @unittest.skipUnless(os.name == "nt", "Windows short-path regression")
    def test_real_download_with_windows_short_data_path_publishes_a_completed_result(self):
        import ctypes
        from api.course_tools import CourseTools

        get_short_path = ctypes.windll.kernel32.GetShortPathNameW
        get_short_path.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32)
        get_short_path.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        if not get_short_path(str(self.data_dir), buffer, len(buffer)) or "~" not in buffer.value:
            self.skipTest("fixture filesystem does not provide an 8.3 alias")
        task_id, config = self.create("download", {"source_task_id": "source", "resource_ids": ["resource1"]}, [RESOURCE])
        service = CourseTools(self.client, cancel_check=lambda: self.store.is_cancelled(task_id))
        resource = dict(RESOURCE, _attachment={"objectId": "object1", "property": {}}, _defaults={})
        response = MagicMock()
        response.headers = {"Content-Length": "10"}
        response.iter_content.return_value = iter([b"0123456789"])
        with patch("api.course_tool_tasks.create_service", return_value=service), \
                patch.object(service, "scan_course", return_value=[resource]), \
                patch.object(service, "_media_status", return_value={"filename": "video.mp4", "http": "https://cdn.chaoxing.com/video.mp4"}), \
                patch.object(service, "_open_download", return_value=response):
            tasks.run_tool_task(task_id, self.store, config, buffer.value, self.factory)
        self.assertEqual(self.store.get_status(task_id)["status"], "completed")
        tool = self.store.get_details(task_id)["tool"]
        self.assertEqual(tool["completed_units"], 10)
        self.assertEqual(tool["results"][0]["path"], "演示视频.mp4")
        self.assertEqual((Path(tool["output_dir"]) / tool["results"][0]["path"]).read_bytes(), b"0123456789")
        self.assertTrue(tasks._valid_download(tool["results"][0], Path(tool["output_dir"])))

    def test_completed_download_rows_remain_counted_when_resumed_worker_skips_them(self):
        task_id, config = self.create("download", {"source_task_id": "source", "resource_ids": ["resource1"]}, [RESOURCE])
        directory = tasks.download_directory(self.data_dir, task_id, create=True)
        target = directory / "video.mp4"
        target.write_bytes(b"video")
        with self.store.edit(task_id) as task:
            task.details["tool"]["results"] = [{"id": "resource1", "status": "completed", "bytes": 5, "path": target.name}]
        self.store.checkpoint(task_id)
        restarted = TaskStore(state_file=self.state_file)
        self.addCleanup(restarted.close)
        details = tasks.restored_details(config, restarted.get_details(task_id), self.data_dir, task_id)
        restarted.resume(task_id, "alice", tasks.initial_status(config, details), details)
        tasks.run_tool_task(task_id, restarted, config, self.data_dir, self.factory)
        self.service.scan_course.assert_not_called()
        self.service.download_resource.assert_not_called()
        self.assertEqual(restarted.get_status(task_id)["status"], "completed")
        self.assertEqual(restarted.get_status(task_id)["progress"], 1)
        self.assertEqual(restarted.get_status(task_id)["stats"]["completed_tasks"], 1)
        self.assertEqual(restarted.get_details(task_id)["tool"]["total_units"], 5)

    def test_catalog_budget_reserves_complete_ascii_encoded_download_results(self):
        _, config = self.create("catalog", {"purpose": "download"})
        details = tasks.initial_details(config, [])
        oversized = [dict(RESOURCE, id=f"r{i}", name="中" * 110, course_title="课" * 110, chapter_title="章" * 110) for i in range(1000)]
        self.assertFalse(tasks._catalog_fits(details, oversized))
        resources = []
        for item in oversized:
            if not tasks._catalog_fits(details, resources + [item]):
                break
            resources.append(item)
        self.assertGreater(len(resources), 0)
        details["tool"]["resources"] = resources
        details["tool"]["results"] = [{"id": item["id"], "name": "中" * 120,
            "course_title": "课" * 120, "course_id": "c1", "status": "error",
            "message": "错" * 240, "path": "文" * 53 + ".pdf", "bytes": 99999999999}
            for item in resources]
        self.assertLess(tasks._wire_size(details), 2 * 1024 * 1024)

    def test_authenticated_course_membership_is_rechecked_in_worker(self):
        self.client.get_course_list.return_value = []
        task_id = self.run_task()
        self.assertEqual(self.store.get_status(task_id)["status"], "error")
        self.service.add_visits.assert_not_called()

    def test_checkpoint_records_progress_before_a_later_process_restart(self):
        task_id, _ = self.create()
        with self.store.edit(task_id) as task:
            task.details["tool"]["completed_units"] = 1
        self.store.checkpoint(task_id)
        restarted = TaskStore(state_file=self.state_file)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.get_details(task_id)["tool"]["completed_units"], 1)
        with self.assertRaises(TaskAlreadyRunning):
            restarted.create("alice", {}, {})

    def test_failed_progress_checkpoint_aborts_later_requests_without_duplicate_rows(self):
        course2 = {**COURSE, "courseId": "c2", "title": "课程二"}
        self.client.get_course_list.return_value = [COURSE, course2]
        task_id, config = self.create()
        config["course_list"].append("c2")

        def add_visits(course, count, interval, on_progress=None):
            on_progress(1, count)
            self.fail("must not continue after acknowledgement persistence fails")

        self.service.add_visits.side_effect = add_visits
        with patch.object(self.store, "checkpoint", side_effect=OSError("disk full")):
            tasks.run_tool_task(task_id, self.store, config, self.data_dir, self.factory)
        self.service.add_visits.assert_called_once()
        self.assertEqual(self.store.get_status(task_id)["status"], "partial")
        self.assertEqual(self.store.get_details(task_id)["tool"]["completed_units"], 1)
        self.assertEqual(self.store.get_details(task_id)["tool"]["results"], [])
        self.assertIn("保存执行记录失败", self.store.get_status(task_id)["error"])

    def test_failed_result_checkpoint_does_not_append_duplicate_failure(self):
        task_id, config = self.create()
        with patch.object(self.store, "checkpoint", side_effect=OSError("disk full")):
            tasks.run_tool_task(task_id, self.store, config, self.data_dir, self.factory)
        results = self.store.get_details(task_id)["tool"]["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "completed")
        self.assertEqual(self.store.get_status(task_id)["status"], "partial")


class ToolOptionTests(unittest.TestCase):
    def test_invalid_numbers_shapes_and_untrusted_fields_are_rejected(self):
        for kind, options in [
            ("visits", {"count": True}), ("visits", {"count": 0}),
            ("visits", {"count": 1.5}), ("visits", {"interval": 0}),
            ("visits", {"interval": "NaN"}), ("catalog", {"purpose": "unknown"}),
            ("download", {"source_task_id": "../bad", "resource_ids": ["r"]}),
            ("download", {"source_task_id": "t", "resource_ids": []}),
            ("download", {"source_task_id": "t", "resource_ids": ["r"], "path": "C:/"}),
            ("video_time", {"source_task_id": "t", "resource_ids": ["r"], "minutes": float("inf")}),
            ("video_time", {"source_task_id": "t", "resource_ids": ["r"], "minutes": 0}),
        ]:
            with self.subTest(kind=kind, options=options), self.assertRaises(ValueError):
                tasks.parse_options(kind, options)

    def test_download_directory_rejects_bad_task_id_and_linked_parents(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                tasks.download_directory(directory, "../escape", create=True)
            path = tasks.download_directory(directory, "task-1", create=True)
            self.assertTrue(path.is_dir())
            self.assertEqual(path.parent, Path(directory).resolve() / "downloads")

    def test_real_linked_download_root_is_rejected_without_writing_outside(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            data = root / "data"
            data.mkdir()
            try:
                os.symlink(outside, data / "downloads", target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            with self.assertRaises(ValueError):
                tasks.download_directory(data, "task-1", create=True)
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
