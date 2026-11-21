"""Tests for automatic shutdown after brushing tasks complete."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import app as web
from api import course_tool_tasks as tool_tasks
from api.shutdown import ShutdownManager, WINDOWS_NO_SHUTDOWN_IN_PROGRESS
from api.task_state import TaskNotFound, TaskStore


def _completed_proc(returncode=0, stdout=b"", stderr=b""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


class ShutdownManagerTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.store = TaskStore(ttl_seconds=60, clock=lambda: self.now, wall_time=lambda: self.now)
        self.addCleanup(self.store.close)
        self.runner = MagicMock(return_value=_completed_proc(0))
        self.manager = ShutdownManager(
            delay=60,
            wall_time=lambda: self.now,
            runner=self.runner,
            platform="win32",
        )
        self.addCleanup(self.manager.reset)

    def _create_task(self, *, account="alice", shutdown_on_complete=True):
        return self.store.create(
            account,
            {"progress": 0, "total": 1, "shutdown_on_complete": shutdown_on_complete},
            {"courses": [], "active_jobs": {}},
        )

    def test_schedules_windows_shutdown_on_completed_and_partial(self):
        for outcome in ("completed", "partial"):
            with self.subTest(outcome=outcome):
                self.runner.reset_mock()
                task_id = self._create_task(shutdown_on_complete=True)
                info = self.manager.finish_task(self.store, task_id, outcome)
                self.assertEqual(info["state"], "scheduled")
                self.assertEqual(info["delay_seconds"], 60)
                self.assertEqual(info["scheduled_at"], self.now)
                self.assertEqual(info["shutdown_at"], self.now + 60)

                args = self.runner.call_args.args[0]
                self.assertEqual(args[:4], ["shutdown", "/s", "/t", "60"])
                self.assertEqual(args[4], "/c")
                self.assertIn("60", args[5])

                status = self.store.get_status(task_id)
                self.assertEqual(status["status"], outcome)
                self.assertEqual(status["shutdown"], info)
                logs = self.store.read_logs(task_id, 0)["data"]
                self.assertTrue(any("自动关机" in entry["message"] for entry in logs))

    def test_skips_shutdown_when_disabled_or_on_error_and_cancelled(self):
        cases = [
            (False, "completed"),
            (False, "partial"),
            (True, "error"),
            (True, "cancelled"),
        ]
        for enabled, outcome in cases:
            with self.subTest(enabled=enabled, outcome=outcome):
                self.runner.reset_mock()
                task_id = self._create_task(shutdown_on_complete=enabled)
                info = self.manager.finish_task(self.store, task_id, outcome)
                self.assertIsNone(info)
                self.runner.assert_not_called()
                status = self.store.get_status(task_id)
                self.assertEqual(status["status"], outcome)
                self.assertNotIn("shutdown", status)

    def test_cancel_scheduled_shutdown_runs_shutdown_abort_and_is_idempotent(self):
        for abort_code in (0, WINDOWS_NO_SHUTDOWN_IN_PROGRESS):
            with self.subTest(abort_code=abort_code):
                self.runner.reset_mock()
                self.runner.side_effect = [_completed_proc(0), _completed_proc(abort_code)]
                task_id = self._create_task(shutdown_on_complete=True)
                self.manager.finish_task(self.store, task_id, "completed")

                result = self.manager.cancel_for_task(self.store, task_id, "alice")
                self.assertEqual(result, {"state": "cancelled"})
                self.assertEqual(self.runner.call_args_list[1].args[0], ["shutdown", "/a"])

                status = self.store.get_status(task_id)
                self.assertFalse(status["shutdown_on_complete"])
                self.assertEqual(status["shutdown"], {"state": "cancelled"})

                # Calling cancel again does not re-run OS command.
                self.assertEqual(self.manager.cancel_for_task(self.store, task_id, "alice"), {"state": "cancelled"})
                self.assertEqual(self.runner.call_count, 2)

    def test_disarming_running_task_prevents_shutdown_on_completion(self):
        task_id = self._create_task(shutdown_on_complete=True)
        self.assertEqual(self.manager.cancel_for_task(self.store, task_id, "alice"), {"state": "cancelled"})
        self.runner.assert_not_called()

        info = self.manager.finish_task(self.store, task_id, "completed")
        self.assertIsNone(info)
        self.runner.assert_not_called()
        status = self.store.get_status(task_id)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["shutdown"], {"state": "cancelled"})

    def test_cancel_rejects_other_account_and_unknown_task(self):
        task_id = self._create_task(account="alice", shutdown_on_complete=True)
        with self.assertRaises(TaskNotFound):
            self.manager.cancel_for_task(self.store, task_id, "bob")
        with self.assertRaises(TaskNotFound):
            self.manager.cancel_for_task(self.store, "missing-task", "alice")

    def test_schedule_failure_records_error_without_blocking_task_completion(self):
        self.runner.return_value = _completed_proc(5, stderr="拒绝访问".encode("utf-8"))
        task_id = self._create_task(shutdown_on_complete=True)
        info = self.manager.finish_task(self.store, task_id, "completed")
        self.assertEqual(info["state"], "failed")
        self.assertIn("拒绝访问", info["error"])

        status = self.store.get_status(task_id)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["shutdown"]["state"], "failed")

    def test_cancel_os_failure_surfaces_error_and_keeps_scheduled_state(self):
        self.runner.side_effect = [_completed_proc(0), _completed_proc(5, stderr=b"access denied")]
        task_id = self._create_task(shutdown_on_complete=True)
        self.manager.finish_task(self.store, task_id, "completed")
        with self.assertRaises(RuntimeError):
            self.manager.cancel_for_task(self.store, task_id, "alice")
        self.assertEqual(self.store.get_status(task_id)["shutdown"]["state"], "scheduled")

    def test_request_cancel_prevents_shutdown_from_scheduling(self):
        task_id = self._create_task(shutdown_on_complete=True)
        self.store.request_cancel(task_id, "alice")
        info = self.manager.finish_task(self.store, task_id, "completed")
        self.assertIsNone(info)
        self.runner.assert_not_called()
        self.assertEqual(self.store.get_status(task_id)["status"], "cancelled")

    def test_posix_timer_schedules_and_cancels_cleanly(self):
        for platform in ("darwin", "linux"):
            with self.subTest(platform=platform):
                runner = MagicMock(return_value=_completed_proc(0))
                manager = ShutdownManager(delay=30, wall_time=lambda: self.now, runner=runner, platform=platform)
                self.addCleanup(manager.reset)
                task_id = self._create_task(shutdown_on_complete=True)
                info = manager.finish_task(self.store, task_id, "completed")
                self.assertEqual(info["state"], "scheduled")
                self.assertIsNotNone(manager._timer)
                runner.assert_not_called()

                self.assertEqual(manager.cancel_for_task(self.store, task_id, "alice"), {"state": "cancelled"})
                self.assertIsNone(manager._timer)
                runner.assert_not_called()


class ShutdownApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.now = 2000.0
        self.store = TaskStore(
            state_file=self.directory / "study_tasks.json",
            ttl_seconds=60,
            clock=lambda: self.now,
            wall_time=lambda: self.now,
        )
        self.addCleanup(self.store.close)
        self.runner = MagicMock(return_value=_completed_proc(0))
        self.manager = ShutdownManager(
            delay=60,
            wall_time=lambda: self.now,
            runner=self.runner,
            platform="win32",
        )
        self.addCleanup(self.manager.reset)

        self.patch("app.task_store", self.store)
        self.patch("app.DATA_DIR", str(self.directory))
        self.patch("app.shutdown_manager", self.manager)
        self.patch("api.course_tool_tasks.shutdown_manager", self.manager)
        self.patch("requests.sessions.Session.request", side_effect=AssertionError("offline only"))
        self.client = web.app.test_client()

    def patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_start_rejects_non_boolean_shutdown_on_complete(self):
        for bad in ("true", 1, 0, None, []):
            with self.subTest(bad=bad):
                resp = self.client.post("/api/start", json={
                    "username": "alice", "password": "pw", "course_list": ["c1"],
                    "shutdown_on_complete": bad,
                })
                self.assertEqual(resp.status_code, 400)
                tool_resp = self.client.post("/api/start", json={
                    "task_type": "visits", "username": "alice", "password": "pw",
                    "course_list": ["c1"], "tool_options": {"count": 1, "interval": 1},
                    "shutdown_on_complete": bad,
                })
                self.assertEqual(tool_resp.status_code, 400)

    def test_study_task_triggers_shutdown_and_cancel_endpoint_aborts_it(self):
        chaoxing = MagicMock()
        chaoxing.login.return_value = {"status": True}
        chaoxing.get_course_list.return_value = [{"courseId": "c1", "clazzId": "z1", "cpi": "p1", "title": "课程一"}]
        chaoxing.get_course_point.return_value = {"points": []}
        course_result = MagicMock(tasks=[], failed=False, skipped=False, success=True,
                                  verification={"status": "confirmed", "reason": "ok"})
        self.patch("main.init_chaoxing", return_value=chaoxing)
        self.patch("main.process_course", return_value=course_result)
        self.patch("app._launch_study_task", side_effect=lambda *args: web._run_study_task(*args))

        resp = self.client.post("/api/start", json={
            "username": "alice", "password": "pw", "course_list": ["c1"],
            "notification_config": {"provider": ""}, "shutdown_on_complete": True,
        })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        task_id = resp.get_json()["data"]["task_id"]

        status = self.client.get(f"/api/task/{task_id}").get_json()["data"]
        self.assertEqual(status["status"], "completed")
        self.assertTrue(status["shutdown_on_complete"])
        self.assertEqual(status["shutdown"]["state"], "scheduled")
        self.assertEqual(self.runner.call_count, 1)

        # Wrong account and bad payload are rejected.
        self.assertEqual(
            self.client.post(f"/api/task/{task_id}/cancel-shutdown", json={"username": "bob"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(f"/api/task/{task_id}/cancel-shutdown", json={"username": "alice", "extra": 1}).status_code,
            400,
        )

        # Owner cancels cleanly.
        cancel = self.client.post(f"/api/task/{task_id}/cancel-shutdown", json={"username": "alice"})
        self.assertEqual(cancel.status_code, 200, cancel.get_json())
        self.assertEqual(cancel.get_json()["data"], {"task_id": task_id, "state": "cancelled"})
        self.assertEqual(self.runner.call_args_list[1].args[0], ["shutdown", "/a"])

        updated = self.client.get(f"/api/task/{task_id}").get_json()["data"]
        self.assertEqual(updated["shutdown"], {"state": "cancelled"})

    def test_visits_tool_triggers_shutdown_but_catalog_never_does(self):
        fake_client = MagicMock()
        fake_client.login.return_value = {"status": True}
        fake_client.get_course_list.return_value = [{"courseId": "c1", "clazzId": "z1", "cpi": "p1", "title": "课程一"}]
        self.patch("app._login_client").return_value.__enter__.return_value = fake_client

        fake_service = MagicMock()
        fake_service.scan_course.return_value = []
        fake_service.add_visits.return_value = {"submitted": 1, "before": 0, "after": 1}
        self.patch("api.course_tool_tasks.create_service", return_value=fake_service)
        self.patch(
            "app._launch_tool_task",
            side_effect=lambda task_id, store, config: tool_tasks.run_tool_task(
                task_id, store, config, str(self.directory), web._login_client,
            ),
        )

        # Catalog must NEVER arm or trigger shutdown even if shutdown_on_complete=True is sent.
        cat_resp = self.client.post("/api/start", json={
            "task_type": "catalog", "username": "alice", "password": "pw",
            "course_list": ["c1"], "tool_options": {"purpose": "download"},
            "shutdown_on_complete": True,
        })
        self.assertEqual(cat_resp.status_code, 200, cat_resp.get_json())
        cat_status = self.client.get(f"/api/task/{cat_resp.get_json()['data']['task_id']}").get_json()["data"]
        self.assertEqual(cat_status["status"], "completed")
        self.assertFalse(cat_status["shutdown_on_complete"])
        self.assertNotIn("shutdown", cat_status)
        self.runner.assert_not_called()

        # Visits brushing task DOES trigger shutdown.
        visits_resp = self.client.post("/api/start", json={
            "task_type": "visits", "username": "alice", "password": "pw",
            "course_list": ["c1"], "tool_options": {"count": 1, "interval": 1},
            "shutdown_on_complete": True,
        })
        self.assertEqual(visits_resp.status_code, 200, visits_resp.get_json())
        visits_status = self.client.get(f"/api/task/{visits_resp.get_json()['data']['task_id']}").get_json()["data"]
        self.assertEqual(visits_status["status"], "completed")
        self.assertEqual(visits_status["shutdown"]["state"], "scheduled")
        self.assertEqual(self.runner.call_count, 1)


if __name__ == "__main__":
    unittest.main()
