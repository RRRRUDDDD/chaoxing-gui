from contextlib import nullcontext
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from uuid import UUID

import app as web
from api.base import StudyResult
from api.task_state import TaskStore


REAL_PROCESS_COURSE = web.main_module.process_course
REAL_LAUNCH_TASK = web._launch_study_task


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.store = TaskStore(ttl_seconds=10, max_logs=30, clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.patch("app.task_store", self.store)
        self.patch("requests.sessions.Session.request", side_effect=AssertionError("Network access is forbidden in offline tests"))
        self.patch("builtins.input", side_effect=AssertionError("Web API must never prompt on stdin"))
        self.client = web.app.test_client()
        self.courses = [{"courseId": "course-1", "title": "课程一", "clazzId": "class-1", "cpi": "cpi-1"}]
        self.snapshot = {"points": [{"id": "chapter-1", "title": "章节一", "jobCount": 2, "has_finished": False}]}
        self.chaoxing = self.make_client()
        self.init = self.patch("app.main_module.init_chaoxing", return_value=self.chaoxing)
        self.process = self.patch("app.main_module.process_course", side_effect=self.success_result)
        self.launch = self.patch("app._launch_study_task", side_effect=web._run_study_task)
        self.notification = MagicMock()
        factory = self.patch("app.Notification")
        factory.return_value.get_notification_from_config.return_value = self.notification
        self.constructor = self.patch("app.Chaoxing", return_value=self.chaoxing)
        self.tiku = self.patch("app.Tiku")

    def patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def make_client(self):
        client = MagicMock()
        client.login.return_value = {"status": True, "msg": "ok"}
        client.get_course_list.return_value = self.courses
        client.get_course_point.return_value = self.snapshot
        client.session_manager.context.side_effect = nullcontext
        return client

    def success_result(self, chaoxing, course, config, *, point_list):
        self.assertIs(point_list, chaoxing.get_course_point.return_value)
        tasks = []
        for index, point in enumerate(point_list["points"]):
            result = web.main_module.ChapterResult.SUCCESS
            count = int(point.get("jobCount", 0))
            point["_task_stats"] = {"total": count, "completed": count, "failed": 0, "skipped": 0}
            config["chapter_start_callback"](course, point)
            config["chapter_result_callback"](course, point, result)
            tasks.append(web.main_module.ChapterTask(index, point, result))
        return web.main_module.CourseResult(tuple(tasks))

    def start(self, **overrides):
        data = {
            "username": "alice", "password": "secret", "course_list": ["course-1"],
            "jobs": 1, "retry_interval": 0,
        }
        data.update(overrides)
        return self.client.post("/api/start", json=data)

    def state(self, response):
        self.assertEqual(response.status_code, 200, response.get_json())
        task_id = response.get_json()["data"]["task_id"]
        return task_id, self.client.get(f"/api/task/{task_id}").get_json()["data"]

    def test_success_reuses_chapter_snapshot_and_closes_client(self):
        task_id, status = self.state(self.start())
        self.assertEqual(UUID(task_id).version, 4)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["stats"]["completed_chapters"], 1)
        self.assertEqual(status["stats"]["completed_tasks"], 2)
        self.assertEqual(status["stats"]["completed_courses"], 1)
        self.chaoxing.get_course_point.assert_called_once_with("course-1", "class-1", "cpi-1")
        self.chaoxing.close.assert_called_once_with()
        self.assertIs(self.process.call_args.kwargs["point_list"], self.snapshot)
        self.assertFalse(self.init.call_args.args[0]["interactive"])
        self.assertIn("所有课程学习任务已完成", self.notification.send.call_args.args[0])

    def test_course_exception_is_not_reported_as_success(self):
        self.process.side_effect = RuntimeError("upstream 503")
        task_id, status = self.state(self.start())
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["stats"]["failed_courses"], 1)
        self.assertEqual(status["stats"]["failed_chapters"], 1)
        self.assertEqual(status["stats"]["failed_tasks"], 2)
        self.assertEqual(status["stats"]["completed_tasks"], 0)
        detail = self.store.get_details(task_id)["courses"][0]
        self.assertEqual(detail["status"], "error")
        self.assertFalse(detail["chapters"][0]["has_finished"])
        self.chaoxing.close.assert_called_once_with()
        self.assertNotIn("所有课程学习任务已完成", self.notification.send.call_args.args[0])

    def test_failed_chapter_list_counts_course_without_inventing_job_counts(self):
        self.chaoxing.get_course_point.side_effect = RuntimeError("chapter list unavailable")
        task_id, status = self.state(self.start())
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["stats"]["failed_courses"], 1)
        self.assertEqual(status["stats"]["failed_tasks"], 0)
        self.assertEqual(status["stats"]["total_chapters"], 0)
        self.assertEqual(self.store.get_details(task_id)["courses"][0]["chapters"], [])
        self.process.assert_not_called()
        self.chaoxing.close.assert_called_once_with()

    def test_partial_chapter_results_override_estimates_and_are_counted_once(self):
        self.snapshot["points"] = [
            {"id": "mixed", "title": "部分失败", "jobCount": 9},
            {"id": "empty", "title": "空章节", "jobCount": 1},
            {"id": "skipped", "title": "未提交", "jobCount": 2},
        ]

        def process(chaoxing, course, config, *, point_list):
            specs = [
                ("ERROR", {"total": 3, "completed": 2, "failed": 1, "skipped": 0}),
                ("EMPTY", {"total": 0, "completed": 0, "failed": 0, "skipped": 0}),
                ("SKIPPED", {"total": 2, "completed": 0, "failed": 0, "skipped": 2}),
            ]
            tasks = []
            for index, (point, (name, counts)) in enumerate(zip(point_list["points"], specs)):
                point["_task_stats"] = counts
                result = web.main_module.ChapterResult[name]
                # A duplicate callback plus final CourseResult reconciliation
                # must not multiply progress totals.
                config["chapter_result_callback"](course, point, result)
                config["chapter_result_callback"](course, point, result)
                tasks.append(web.main_module.ChapterTask(index, point, result))
            return web.main_module.CourseResult(tuple(tasks))

        self.process.side_effect = process
        task_id, status = self.state(self.start())
        self.assertEqual(status["status"], "partial")
        stats = status["stats"]
        self.assertEqual((stats["total_tasks"], stats["completed_tasks"], stats["failed_tasks"], stats["skipped_tasks"]), (5, 2, 1, 2))
        self.assertEqual((stats["completed_chapters"], stats["empty_chapters"], stats["failed_chapters"], stats["skipped_chapters"]), (1, 1, 1, 1))
        self.assertEqual((stats["failed_courses"], stats["skipped_courses"]), (1, 1))
        chapters = self.store.get_details(task_id)["courses"][0]["chapters"]
        self.assertEqual([chapter["status"] for chapter in chapters], ["error", "empty", "skipped"])
        self.assertEqual([chapter["has_finished"] for chapter in chapters], [False, True, False])
        self.assertNotIn("所有课程学习任务已完成", self.notification.send.call_args.args[0])

    def test_completed_course_and_failed_course_produce_partial_task(self):
        self.courses.append({"courseId": "course-2", "title": "课程二", "clazzId": "class-2", "cpi": "cpi-2"})

        def process(chaoxing, course, config, *, point_list):
            if course["courseId"] == "course-1":
                raise RuntimeError("first course failed")
            return self.success_result(chaoxing, course, config, point_list=point_list)

        self.process.side_effect = process
        _, status = self.state(self.start(course_list=["course-1", "course-2"]))
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["progress"], 2)
        self.assertEqual(status["stats"]["failed_courses"], 1)
        self.assertEqual(status["stats"]["completed_courses"], 1)

    def test_actual_scheduler_retry_exhaustion_propagates_to_api(self):
        self.process.side_effect = REAL_PROCESS_COURSE
        self.chaoxing.get_job_list.side_effect = RuntimeError("job response 503")
        _, status = self.state(self.start())
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["stats"]["failed_tasks"], 2)
        self.assertEqual(status["stats"]["failed_chapters"], 1)
        self.assertEqual(self.chaoxing.get_job_list.call_count, 5)
        self.chaoxing.get_course_point.assert_called_once()
        self.chaoxing.close.assert_called_once_with()

    def test_actual_scheduler_distinguishes_empty_and_unopened_skipped(self):
        self.process.side_effect = REAL_PROCESS_COURSE
        self.snapshot["points"].append({"id": "closed", "title": "关闭章节", "jobCount": 3})
        self.chaoxing.get_job_list.side_effect = lambda course, point: ([], {"notOpen": point["id"] == "closed"})
        task_id, status = self.state(self.start(notopen_action="continue"))
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["stats"]["empty_chapters"], 1)
        self.assertEqual(status["stats"]["skipped_tasks"], 3)
        self.assertEqual(status["stats"]["total_tasks"], 3)
        self.assertEqual([chapter["status"] for chapter in self.store.get_details(task_id)["courses"][0]["chapters"]], ["empty", "skipped"])

    def test_actual_scheduler_reports_mixed_job_outcomes_within_a_chapter(self):
        self.process.side_effect = REAL_PROCESS_COURSE
        self.chaoxing.get_job_list.return_value = (
            [{"type": "read", "jobid": result.name} for result in (StudyResult.SUCCESS, StudyResult.ERROR, StudyResult.SKIPPED)],
            {},
        )
        self.chaoxing.study_read.side_effect = lambda course, job, info: StudyResult[job["jobid"]]
        task_id, status = self.state(self.start())
        self.assertEqual(status["status"], "partial")
        stats = status["stats"]
        self.assertEqual((stats["total_tasks"], stats["completed_tasks"], stats["failed_tasks"], stats["skipped_tasks"]), (3, 1, 1, 1))
        self.assertEqual(stats["failed_chapters"], 1)
        self.assertFalse(self.store.get_details(task_id)["courses"][0]["chapters"][0]["has_finished"])

    def test_missing_course_result_fails_instead_of_implicit_success(self):
        self.process.side_effect = None
        self.process.return_value = None
        _, status = self.state(self.start())
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["stats"]["failed_courses"], 1)

    def test_unknown_course_ids_fail_without_selecting_all_courses(self):
        _, status = self.state(self.start(course_list=["unknown"]))
        self.assertEqual(status["status"], "error")
        self.assertIn("unknown", status["error"])
        self.process.assert_not_called()
        self.chaoxing.get_course_point.assert_not_called()
        self.chaoxing.close.assert_called_once_with()

    def test_login_failure_and_initialization_failure_release_account(self):
        self.chaoxing.login.return_value = {"status": False, "msg": "expired session"}
        first_id, first = self.state(self.start())
        self.assertEqual(first["status"], "error")
        self.assertIn("expired session", first["error"])
        self.chaoxing.close.assert_called_once_with()
        self.init.side_effect = RuntimeError("cannot initialize")
        second_id, second = self.state(self.start())
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(second["status"], "error")
        self.assertIn("cannot initialize", second["error"])

    def test_duplicate_start_returns_existing_id_until_terminal(self):
        self.launch.side_effect = None
        first_id, first = self.state(self.start())
        self.assertEqual(first["status"], "running")
        duplicate = self.start(username=" alice ")
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.get_json()["data"]["task_id"], first_id)
        self.assertEqual(self.launch.call_count, 1)
        self.store.finish(first_id, "completed")
        new_id, _ = self.state(self.start())
        self.assertNotEqual(new_id, first_id)

    def test_thread_start_failure_releases_account(self):
        self.launch.side_effect = RuntimeError("thread unavailable")
        response = self.start()
        self.assertEqual(response.status_code, 500)
        failed_id = response.get_json()["data"]["task_id"]
        self.assertEqual(self.store.get_status(failed_id)["status"], "error")
        self.launch.side_effect = None
        new_id, _ = self.state(self.start())
        self.assertNotEqual(new_id, failed_id)

    def test_invalid_start_parameters_are_rejected_before_creating_task(self):
        cases = [
            {"username": " "}, {"password": " "}, {"username": 123}, {"password": None},
            {"use_cookies": "false"}, {"course_list": []}, {"course_list": None},
            {"course_list": "course-1"}, {"course_list": [""]}, {"course_list": [False]},
            {"course_list": [{}]}, {"jobs": 0}, {"jobs": -1}, {"jobs": 17},
            {"jobs": True}, {"jobs": 1.5}, {"jobs": "1.5"}, {"jobs": None},
            {"speed": "nan"}, {"speed": "inf"}, {"speed": "-inf"}, {"speed": 0},
            {"speed": True}, {"speed": None}, {"retry_interval": -1}, {"retry_interval": 301},
            {"retry_interval": "nan"}, {"retry_interval": "inf"}, {"retry_interval": False},
            {"notopen_action": "ask"}, {"notopen_action": "unknown"}, {"tiku_config": []},
            {"notification_config": "bad"}, {"ocr_config": "bad"},
        ]
        for values in cases:
            with self.subTest(values=values):
                response = self.start(**values)
                self.assertEqual(response.status_code, 400, response.get_json())
        self.launch.assert_not_called()
        self.init.assert_not_called()
        for value in ([], None, "text"):
            self.assertEqual(self.client.post("/api/start", json=value).status_code, 400)

    def test_parameter_boundaries_preserve_speed_clamp(self):
        for jobs, speed, retry, expected_speed in ((1, 0.5, 0, 1.0), (16, 20, 300, 2.0)):
            with self.subTest(jobs=jobs):
                _, status = self.state(self.start(jobs=jobs, speed=speed, retry_interval=retry))
                self.assertEqual(status["status"], "completed")
                config = self.init.call_args.args[0]
                self.assertEqual(config["jobs"], jobs)
                self.assertEqual(config["speed"], expected_speed)
                self.assertEqual(config["retry_interval"], retry)

    def test_cookie_only_login_courses_and_start(self):
        credentials = {"username": "alice", "password": "", "use_cookies": True}
        for endpoint in ("/api/login", "/api/courses"):
            with self.subTest(endpoint=endpoint):
                response = self.client.post(endpoint, json=credentials)
                self.assertEqual(response.status_code, 200, response.get_json())
        _, status = self.state(self.start(**credentials))
        self.assertEqual(status["status"], "completed")
        self.assertTrue(self.init.call_args.args[0]["use_cookies"])
        self.assertEqual(self.chaoxing.login.call_count, 3)
        for call in self.chaoxing.login.call_args_list:
            self.assertTrue(call.kwargs["login_with_cookies"])
        self.assertEqual(self.chaoxing.close.call_count, 3)

    def test_login_and_course_requests_close_on_success_failure_and_exception(self):
        for endpoint in ("/api/login", "/api/courses"):
            for mode in ("success", "failed_login", "exception"):
                with self.subTest(endpoint=endpoint, mode=mode):
                    self.chaoxing.reset_mock()
                    self.chaoxing.login.side_effect = RuntimeError("offline error") if mode == "exception" else None
                    self.chaoxing.login.return_value = {"status": mode != "failed_login", "msg": "rejected"}
                    response = self.client.post(endpoint, json={"username": "alice", "password": "secret"})
                    self.assertEqual(response.status_code, {"success": 200, "failed_login": 401, "exception": 500}[mode])
                    self.chaoxing.close.assert_called_once_with()
        self.chaoxing.login.side_effect = None
        self.chaoxing.login.return_value = {"status": True}
        self.chaoxing.get_course_list.side_effect = RuntimeError("list failed")
        self.chaoxing.close.reset_mock()
        self.assertEqual(self.client.post("/api/courses", json={"username": "alice", "password": "secret"}).status_code, 500)
        self.chaoxing.close.assert_called_once_with()

    def test_upstream_value_errors_are_server_errors_and_close_client(self):
        self.chaoxing.login.side_effect = ValueError("invalid upstream JSON")
        for endpoint in ("/api/login", "/api/courses"):
            with self.subTest(endpoint=endpoint):
                self.chaoxing.close.reset_mock()
                response = self.client.post(endpoint, json={"username": "alice", "password": "secret"})
                self.assertEqual(response.status_code, 500)
                self.chaoxing.close.assert_called_once_with()

    def test_login_and_courses_reject_missing_credentials_without_constructing_client(self):
        for endpoint in ("/api/login", "/api/courses"):
            for data in ({}, {"username": "alice", "password": " "}, [], {"username": "", "use_cookies": True}):
                with self.subTest(endpoint=endpoint, data=data):
                    self.assertEqual(self.client.post(endpoint, json=data).status_code, 400)
        self.constructor.assert_not_called()

    def test_client_construction_failure_closes_created_tiku(self):
        self.constructor.side_effect = RuntimeError("constructor failed")
        response = self.client.post("/api/login", json={"username": "alice", "password": "secret"})
        self.assertEqual(response.status_code, 500)
        self.tiku.return_value.close.assert_called_once_with()

    def test_notifications_cannot_overwrite_real_learning_result(self):
        for failure in ("init", "send"):
            with self.subTest(failure=failure):
                self.notification.init_notification.side_effect = RuntimeError("notify init") if failure == "init" else None
                self.notification.send.side_effect = RuntimeError("notify send") if failure == "send" else None
                task_id, status = self.state(self.start())
                self.assertEqual(status["status"], "completed")
                self.assertIn("notification_error", status)
                self.assertNotIn("error", status)
                self.assertTrue(any("通知" in entry["message"] for entry in self.store.read_logs(task_id)["data"]))

    def test_final_logs_include_resource_cleanup_and_notification_tail(self):
        self.chaoxing.close.side_effect = lambda: web.logger.info("cleanup-tail-marker")
        self.notification.send.side_effect = lambda message: web.logger.info("notification-tail-marker")
        task_id, status = self.state(self.start())
        self.assertEqual(status["status"], "completed")
        response = self.client.get(f"/api/logs/{task_id}?after=0").get_json()
        self.assertIsInstance(response["data"], list)
        text = "\n".join(entry["message"] for entry in response["data"])
        self.assertIn("cleanup-tail-marker", text)
        self.assertIn("notification-tail-marker", text)
        tail = self.client.get(f"/api/logs/{task_id}?after={response['next_cursor']}").get_json()
        self.assertEqual(tail["data"], [])

    def test_cleanup_error_does_not_erase_completed_learning(self):
        self.chaoxing.close.side_effect = RuntimeError("cache flush failed")
        _, status = self.state(self.start())
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["cleanup_error"], "cache flush failed")
        self.chaoxing.close.assert_called_once_with()

    def test_concurrent_tasks_keep_worker_logs_in_their_own_context(self):
        clients = {}
        barrier = threading.Barrier(2)
        threads = []

        def initialize(config, tiku):
            account = config["username"]
            client = self.make_client()
            client.get_course_point.return_value = deepcopy(self.snapshot)

            def get_jobs(course, point):
                barrier.wait(timeout=5)
                web.logger.info(f"{account}-worker-marker")
                return [], {}

            client.get_job_list.side_effect = get_jobs
            clients[account] = client
            return client

        def launch(*args):
            thread = REAL_LAUNCH_TASK(*args)
            threads.append(thread)
            return thread

        self.init.side_effect = initialize
        self.launch.side_effect = launch
        self.process.side_effect = REAL_PROCESS_COURSE
        ids = {}
        try:
            for account in ("alice", "bob"):
                response = self.start(username=account)
                self.assertEqual(response.status_code, 200)
                ids[account] = response.get_json()["data"]["task_id"]
        finally:
            for thread in threads:
                thread.join(timeout=8)
                self.assertFalse(thread.is_alive())
        for account, task_id in ids.items():
            self.assertEqual(self.store.get_status(task_id)["status"], "completed")
            text = "\n".join(entry["message"] for entry in self.store.read_logs(task_id)["data"])
            self.assertIn(f"{account}-worker-marker", text)
            self.assertNotIn(f"{'bob' if account == 'alice' else 'alice'}-worker-marker", text)
            clients[account].close.assert_called_once_with()

    def test_ocr_config_is_explicit_and_never_changes_process_environment(self):
        from api import vision_ocr

        original = vision_ocr.ocr_context
        with patch.dict(os.environ, {"CHAOXING_VISION_OCR_PROVIDER": "previous-provider", "CHAOXING_VISION_OCR_KEY": "previous-key"}):
            before = {key: value for key, value in os.environ.items() if "OCR" in key}
            with patch.object(vision_ocr, "ocr_context", side_effect=original) as context:
                custom = {"provider": "openai", "key": "task-key", "endpoint": "https://example.invalid/v1"}
                self.state(self.start(ocr_config=custom))
                self.state(self.start(ocr_config={}))
                self.state(self.start(ocr_config=None))
                self.assertEqual([call.args[0] for call in context.call_args_list], [custom, {}, {}])
            self.assertEqual({key: value for key, value in os.environ.items() if "OCR" in key}, before)

    def test_ocr_import_failure_finishes_task_and_releases_account(self):
        original_import = __import__

        def importing(name, *args, **kwargs):
            if name == "api.vision_ocr":
                raise ImportError("OCR module unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=importing):
            task_id, status = self.state(self.start())
        self.assertEqual(status["status"], "error")
        self.assertIn("OCR module unavailable", status["error"])
        self.init.assert_not_called()
        new_id, next_status = self.state(self.start())
        self.assertNotEqual(task_id, new_id)
        self.assertEqual(next_status["status"], "completed")

    def test_log_cursor_validation_and_expiry_apply_to_all_endpoints(self):
        task_id, _ = self.state(self.start())
        for cursor in ("-1", "1.0", "true", "", "NaN", "1e3", "+1", "１２"):
            with self.subTest(cursor=cursor):
                self.assertEqual(self.client.get(f"/api/logs/{task_id}", query_string={"after": cursor}).status_code, 400)
        self.assertEqual(self.client.get(f"/api/logs/{task_id}?after=0").status_code, 200)
        self.now += 10
        for endpoint in (f"/api/task/{task_id}", f"/api/task/{task_id}/details", f"/api/logs/{task_id}"):
            self.assertEqual(self.client.get(endpoint).status_code, 404)

    def test_config_merges_selections_by_account_and_discards_legacy_selection(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(web, "CONFIG_FILE", str(Path(directory) / "config.json")):
            web.save_web_config({"settings": {"speed": 1}, "selectedCourses": ["old"], "selectedCoursesByAccount": {"alice": ["a"]}})
            self.assertNotIn("selectedCourses", self.client.get("/api/config").get_json()["data"])
            response = self.client.post("/api/config", json={"settings": {"speed": 2}, "selectedCoursesByAccount": {"bob": ["b"]}})
            self.assertEqual(response.status_code, 200)
            response = self.client.post("/api/config", json={"selectedCoursesByAccount": {"alice": []}})
            self.assertEqual(response.status_code, 200)
            saved = self.client.get("/api/config").get_json()["data"]
            self.assertEqual(saved["selectedCoursesByAccount"], {"alice": [], "bob": ["b"]})
            self.assertEqual(saved["settings"], {"speed": 2})
            self.assertNotIn("selectedCourses", saved)

    def test_concurrent_config_saves_preserve_both_account_keys(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(web, "CONFIG_FILE", str(Path(directory) / "config.json")):
            barrier = threading.Barrier(2)
            results = []

            def save(account):
                with web.app.test_client() as client:
                    barrier.wait(timeout=3)
                    response = client.post("/api/config", json={"selectedCoursesByAccount": {account: [account]}})
                    results.append(response.status_code)

            threads = [threading.Thread(target=save, args=(account,)) for account in ("alice", "bob")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
            self.assertEqual(results, [200, 200])
            self.assertEqual(web.load_web_config()["selectedCoursesByAccount"], {"alice": ["alice"], "bob": ["bob"]})

    def test_invalid_config_shapes_are_rejected(self):
        for data in ([], {"settings": []}, {"selectedCoursesByAccount": []}, {"selectedCoursesByAccount": {"": []}}, {"selectedCoursesByAccount": {"alice": "course-1"}}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post("/api/config", json=data).status_code, 400)


if __name__ == "__main__":
    unittest.main()
