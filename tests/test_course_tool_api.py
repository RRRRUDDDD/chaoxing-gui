"""Course tool HTTP contracts and restart behavior with isolated fixtures."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import app as web
from api import course_tool_tasks as tasks
from api.task_state import TaskStore


RESOURCE = {"id": "video1", "name": "示例视频", "course_id": "course1", "course_title": "课程一",
            "chapter_id": "chapter1", "chapter_title": "第一章", "kind": "video",
            "downloadable": True, "watchable": True}


class CourseToolApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state_file = self.directory / "study_tasks.json"
        self.now = 1000.0
        self.store = self.new_store()
        self.addCleanup(self.store.close)
        self.patch("app.task_store", self.store)
        self.patch("app.DATA_DIR", str(self.directory))
        self.patch("requests.sessions.Session.request", side_effect=AssertionError("offline only"))
        self.launch = self.patch("app._launch_tool_task")
        self.study_launch = self.patch("app._launch_study_task")
        self.login = MagicMock()
        self.login.login.return_value = {"status": True}
        self.patch("app._login_client").return_value.__enter__.return_value = self.login
        self.client = web.app.test_client()

    def new_store(self):
        return TaskStore(state_file=self.state_file, ttl_seconds=10,
                         clock=lambda: self.now, wall_time=lambda: self.now)

    def patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def start(self, kind="visits", options=None, username="alice", **extra):
        return self.client.post("/api/start", json={"task_type": kind, "username": username,
            "password": "never-store-password", "course_list": ["course1"],
            "tool_options": options if options is not None else {"count": 3, "interval": 30}, **extra})

    def catalog(self, purpose="download", username="alice"):
        response = self.start("catalog", {"purpose": purpose}, username=username)
        self.assertEqual(response.status_code, 200, response.get_json())
        task_id = response.get_json()["data"]["task_id"]
        with self.store.edit(task_id) as task:
            task.details["tool"]["resources"] = [RESOURCE]
        self.store.finish(task_id, "completed")
        return task_id

    def restart(self):
        self.store.close()
        self.store = self.new_store()
        self.addCleanup(self.store.close)
        web.task_store = self.store
        self.launch.reset_mock()

    def resume(self, task_id, username="alice"):
        return self.client.post(f"/api/task/{task_id}/resume", json={"username": username, "use_cookies": True})

    def test_tool_start_persists_only_options_and_uses_same_account_lock(self):
        response = self.start()
        self.assertEqual(response.status_code, 200, response.get_json())
        task_id = response.get_json()["data"]["task_id"]
        self.assertEqual(self.store.get_status(task_id)["task_type"], "visits")
        self.assertEqual(self.start().status_code, 409)
        conflict = self.client.post("/api/start", json={"username": "alice", "password": "pw", "course_list": ["course1"]})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["data"]["task_id"], task_id)
        self.assertNotIn("never-store-password", self.state_file.read_text(encoding="utf-8"))
        self.study_launch.assert_not_called()

    def test_unsupported_types_and_invalid_options_do_not_launch(self):
        for kind, options in [("shell", {}), ([], {}), (None, {}), ("visits", []),
                              ("visits", {"count": 1001}), ("catalog", {"purpose": "study"}),
                              ("catalog", {"purpose": []}), ("catalog", {"purpose": {}})]:
            with self.subTest(kind=kind, options=options):
                self.assertEqual(self.start(kind, options).status_code, 400)
        self.launch.assert_not_called()

    def test_download_resolves_selection_from_own_completed_catalogue(self):
        source = self.catalog()
        self.launch.reset_mock()
        response = self.start("download", {"source_task_id": source, "resource_ids": ["video1"]})
        self.assertEqual(response.status_code, 200, response.get_json())
        task_id = response.get_json()["data"]["task_id"]
        self.assertEqual(self.store.get_details(task_id)["tool"]["resources"], [RESOURCE])
        self.launch.assert_called_once()

    def test_other_account_expired_source_unknown_ids_and_wrong_function_are_rejected(self):
        source = self.catalog()
        for username, source_id, ids, kind, expected in [
            ("bob", source, ["video1"], "download", 404),
            ("alice", "missing", ["video1"], "download", 404),
            ("alice", source, ["unknown"], "download", 400),
            ("alice", source, ["video1"], "video_time", 400),
        ]:
            with self.subTest(username=username, ids=ids, kind=kind):
                response = self.start(kind, {"source_task_id": source_id, "resource_ids": ids}, username=username)
                self.assertEqual(response.status_code, expected, response.get_json())

    def test_launcher_failure_finishes_task_and_releases_account(self):
        self.launch.side_effect = RuntimeError("cannot start worker")
        response = self.start()
        self.assertEqual(response.status_code, 500)
        task_id = response.get_json()["data"]["task_id"]
        self.assertEqual(self.store.get_status(task_id)["status"], "error")
        self.launch.side_effect = None
        self.assertEqual(self.start().status_code, 200)

    def test_expired_catalogue_is_rejected_before_creating_a_new_task(self):
        source = self.catalog()
        self.now += 11
        self.launch.reset_mock()
        response = self.start("download", {"source_task_id": source, "resource_ids": ["video1"]})
        self.assertEqual(response.status_code, 404)
        self.launch.assert_not_called()

    def test_download_can_resume_from_its_own_selection_after_source_expires(self):
        source = self.catalog()
        response = self.start("download", {"source_task_id": source, "resource_ids": ["video1"]})
        task_id = response.get_json()["data"]["task_id"]
        self.now += 11
        self.restart()
        self.assertEqual(self.client.get(f"/api/task/{source}").status_code, 404)
        response = self.resume(task_id)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.store.get_details(task_id)["tool"]["resources"], [RESOURCE])
        self.launch.assert_called_once()

    def test_oversized_catalogue_stays_readable_through_the_real_details_endpoint(self):
        service = MagicMock()
        service.scan_course.return_value = [dict(RESOURCE, id=f"r{i}", name="中" * 110,
            course_title="课" * 110, chapter_title="章" * 110) for i in range(1000)]
        service.public_resource.side_effect = lambda resource: resource
        self.login.get_course_list.return_value = [{"courseId": "course1", "clazzId": "class1", "cpi": "cpi", "title": "课程一"}]
        self.launch.side_effect = lambda task_id, store, config: tasks.run_tool_task(
            task_id, store, config, self.directory, web._login_client)
        with patch("api.course_tool_tasks.create_service", return_value=service):
            response = self.start("catalog", {"purpose": "download"})
        self.assertEqual(response.status_code, 200, response.get_json())
        task_id = response.get_json()["data"]["task_id"]
        self.assertEqual(self.store.get_status(task_id)["status"], "error")
        details = self.client.get(f"/api/task/{task_id}/details")
        self.assertEqual(details.status_code, 200)
        self.assertLess(len(details.data), 2 * 1024 * 1024)
        self.assertIn("分批", details.get_json()["data"]["tool"]["results"][0]["message"])

    def test_non_idempotent_restart_keeps_acknowledged_progress_without_replaying(self):
        response = self.start()
        task_id = response.get_json()["data"]["task_id"]
        with self.store.edit(task_id) as task:
            task.details["tool"]["completed_units"] = 2
        self.store.checkpoint(task_id)
        self.restart()
        response = self.resume(task_id)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["data"]["status"], "partial")
        self.assertEqual(self.store.get_details(task_id)["tool"]["completed_units"], 2)
        self.assertIn("重复", self.store.get_status(task_id)["error"])
        self.launch.assert_not_called()
        self.assertEqual(self.start().status_code, 200)

    def test_catalogue_restart_claims_original_id_once(self):
        response = self.start("catalog", {"purpose": "download"})
        task_id = response.get_json()["data"]["task_id"]
        self.restart()
        self.assertEqual(self.resume(task_id).status_code, 200)
        self.assertEqual(self.resume(task_id).status_code, 200)
        self.launch.assert_called_once()
        self.assertEqual(self.launch.call_args.args[0], task_id)
        self.assertEqual(self.launch.call_args.args[2]["password"], "")

    def test_download_restart_keeps_completed_files_without_requiring_live_source(self):
        source = self.catalog()
        response = self.start("download", {"source_task_id": source, "resource_ids": ["video1"]})
        task_id = response.get_json()["data"]["task_id"]
        directory = tasks.download_directory(self.directory, task_id, create=True)
        target = directory / "video.mp4"
        target.write_bytes(b"fixture")
        with self.store.edit(task_id) as task:
            task.details["tool"]["results"] = [{"id": "video1", "status": "completed", "bytes": 7, "path": str(target)}]
        self.store.checkpoint(task_id)
        self.restart()
        self.assertEqual(self.resume(task_id).status_code, 200)
        self.assertEqual(len(self.store.get_details(task_id)["tool"]["results"]), 1)
        self.assertEqual(self.store.get_details(task_id)["tool"]["output_dir"], str(directory))
        self.assertEqual(self.store.get_status(task_id)["progress"], 1)
        self.assertEqual(self.store.get_status(task_id)["stats"]["completed_tasks"], 1)
        self.launch.assert_called_once()

    def test_open_downloads_is_task_owned_and_does_not_accept_a_client_path(self):
        source = self.catalog()
        response = self.start("download", {"source_task_id": source, "resource_ids": ["video1"]})
        task_id = response.get_json()["data"]["task_id"]
        directory = tasks.download_directory(self.directory, task_id, create=True)
        with patch("api.course_tool_tasks.os.startfile", create=True) as opening, patch("api.course_tool_tasks.sys.platform", "win32"):
            self.assertEqual(self.client.post(f"/api/task/{task_id}/open-downloads", json={"username": "bob"}).status_code, 404)
            self.assertEqual(self.client.post(f"/api/task/{task_id}/open-downloads", json={"username": "alice", "path": "C:/"}).status_code, 400)
            self.assertEqual(self.client.post(f"/api/task/{source}/open-downloads", json={"username": "alice"}).status_code, 400)
            opening.assert_not_called()
            response = self.client.post(f"/api/task/{task_id}/open-downloads", json={"username": "alice"})
            self.assertEqual(response.status_code, 200, response.get_json())
            opening.assert_called_once_with(str(directory))


if __name__ == "__main__":
    unittest.main()
