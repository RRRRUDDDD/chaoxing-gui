import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

import app as web
from api.task_state import TaskAlreadyRunning, TaskStore


SETTINGS = {
    "course_list": ["course-1"], "jobs": 1, "speed": 1.4,
    "retry_interval": 0, "notopen_action": "continue",
    "tiku_config": {}, "notification_config": {"provider": ""}, "ocr_config": {},
}


class TaskRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state_file = Path(temporary.name) / "study_tasks.json"
        self.now = 1000.0
        self.store = self.new_store()

    def new_store(self):
        store = TaskStore(
            state_file=self.state_file, ttl_seconds=10, max_logs=3,
            clock=lambda: self.now, wall_time=lambda: self.now,
        )
        self.addCleanup(store.close)
        return store

    def create(self, account="alice"):
        return self.store.create(account, web._initial_status(), {"courses": [], "active_jobs": {}}, resume_config=SETTINGS)

    def test_restart_preserves_task_identity_and_reserves_account_without_starting(self):
        task_id = self.create()
        restarted = self.new_store()
        self.assertEqual(restarted.get_status(task_id)["status"], "interrupted")
        with self.assertRaises(TaskAlreadyRunning) as conflict:
            restarted.create("alice", {}, {}, resume_config=SETTINGS)
        self.assertEqual(conflict.exception.task_id, task_id)
        self.assertEqual(restarted.resume(task_id, "alice", web._initial_status(), {"courses": [], "active_jobs": {}}), SETTINGS)
        self.assertEqual(restarted.get_status(task_id)["status"], "running")
        self.assertIsNone(restarted.resume(task_id, "alice", {}, {}))

    def test_checkpoint_survives_process_exit_without_cleanup_handlers(self):
        script = """
import json, os, sys
from api.task_state import TaskStore
store = TaskStore(state_file=sys.argv[1])
store.create('alice', {'progress': 0}, {'courses': []}, resume_config=json.loads(sys.argv[2]))
os._exit(0)
"""
        subprocess.run(
            [sys.executable, "-c", script, str(self.state_file), json.dumps(SETTINGS)],
            cwd=Path(__file__).resolve().parents[1], check=True, timeout=15,
        )
        task_id = next(iter(json.loads(self.state_file.read_text(encoding="utf-8"))["tasks"]))
        self.assertEqual(self.new_store().get_status(task_id)["status"], "interrupted")

    def test_only_one_concurrent_resume_claim_succeeds(self):
        task_id = self.create()
        restarted = self.new_store()
        barrier = threading.Barrier(6)
        claims = []

        def resume():
            barrier.wait(timeout=5)
            claims.append(restarted.resume(task_id, "alice", web._initial_status(), {}))

        threads = [threading.Thread(target=resume) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(claim is not None for claim in claims), 1)

    def test_terminal_results_survive_restart_and_expire_without_resuming(self):
        for state in ("completed", "partial", "error"):
            with self.subTest(state=state):
                task_id = self.create()
                with self.store.edit(task_id) as task:
                    task.details["courses"] = [{"id": "course-1", "status": state}]
                for index in range(5):
                    self.store.append_log(task_id, f"log-{index}")
                self.store.finish(task_id, state)
                restarted = self.new_store()
                self.assertEqual(restarted.get_status(task_id)["status"], state)
                self.assertEqual(restarted.get_details(task_id)["courses"][0]["status"], state)
                self.assertEqual([entry["seq"] for entry in restarted.read_logs(task_id)["data"]], [3, 4, 5])
                self.assertIsNone(restarted.resume(task_id, "alice", {}, {}))
                self.now += 10
                with self.assertRaises(KeyError):
                    self.new_store().get_status(task_id)

    def test_account_mismatch_does_not_claim_task(self):
        task_id = self.create()
        restarted = self.new_store()
        with self.assertRaises(KeyError):
            restarted.resume(task_id, "bob", {}, {})
        self.assertEqual(restarted.get_status(task_id)["status"], "interrupted")

    def test_failed_initial_write_rolls_back_reservation_and_keeps_previous_file(self):
        previous = self.create("bob")
        original = self.state_file.read_bytes()
        with patch("api.task_state.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(self.state_file.read_bytes(), original)
        self.assertEqual(self.new_store().get_status(previous)["status"], "interrupted")
        self.create()

    def test_failed_resume_write_leaves_task_retryable(self):
        task_id = self.create()
        restarted = self.new_store()
        with patch("api.task_state.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                restarted.resume(task_id, "alice", {}, {})
        self.assertEqual(restarted.get_status(task_id)["status"], "interrupted")
        self.assertEqual(restarted.resume(task_id, "alice", {}, {}), SETTINGS)

    def test_corrupt_checkpoint_is_reported_and_never_overwritten(self):
        self.state_file.write_text('{"version":', encoding="utf-8")
        with self.assertRaises(OSError):
            self.create()
        self.assertEqual(self.state_file.read_text(encoding="utf-8"), '{"version":')

    def test_finish_write_failure_is_visible_and_terminal_in_memory(self):
        task_id = self.create()
        with patch("api.task_state.os.replace", side_effect=OSError("disk full")):
            self.store.finish(task_id, "completed")
        status = self.store.get_status(task_id)
        self.assertEqual(status["status"], "completed")
        self.assertIn("保存", status["recovery_error"])


class TaskRecoveryApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state_file = Path(temporary.name) / "study_tasks.json"
        self.store = TaskStore(state_file=self.state_file)
        self.addCleanup(self.store.close)
        self.patch("app.task_store", self.store)
        self.patch("requests.sessions.Session.request", side_effect=AssertionError("offline tests only"))
        self.launch = self.patch("app._launch_study_task")
        self.login = MagicMock()
        self.login.login.return_value = {"status": True}
        self.patch("app._login_client").return_value.__enter__.return_value = self.login
        self.client = web.app.test_client()

    def patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def start(self):
        response = self.client.post("/api/start", json={
            **SETTINGS, "username": "alice", "password": "never-persist-this-password",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()["data"]["task_id"]

    def restart(self):
        self.store.close()
        self.store = TaskStore(state_file=self.state_file)
        self.addCleanup(self.store.close)
        web.task_store = self.store
        self.launch.reset_mock()

    def resume(self, task_id, username="alice"):
        return self.client.post(f"/api/task/{task_id}/resume", json={
            "username": username, "password": "", "use_cookies": True,
        })

    def test_start_restart_resume_uses_saved_parameters_and_cookie_login(self):
        task_id = self.start()
        serialized = self.state_file.read_text(encoding="utf-8")
        self.assertNotIn("never-persist-this-password", serialized)
        self.assertNotIn('"password"', serialized)
        self.restart()
        response = self.resume(task_id)
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["data"]["task_id"], task_id)
        self.login.login.assert_called_once_with(login_with_cookies=True)
        args = self.launch.call_args.args
        self.assertEqual(args[0], task_id)
        self.assertEqual(args[2]["course_list"], SETTINGS["course_list"])
        self.assertEqual(args[2]["speed"], 1.4)
        self.assertEqual(args[2]["notopen_action"], "continue")
        self.assertEqual(args[2]["password"], "")
        self.assertTrue(args[2]["use_cookies"])
        self.assertEqual(self.resume(task_id).status_code, 200)
        self.launch.assert_called_once()

    def test_completed_task_does_not_restart(self):
        task_id = self.start()
        self.store.finish(task_id, "completed")
        self.restart()
        response = self.resume(task_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["data"]["status"], "completed")
        self.launch.assert_not_called()

    def test_resume_reloads_platform_progress_and_skips_completed_chapters(self):
        task_id = self.start()
        self.restart()
        course = {"courseId": "course-1", "title": "Fixture course", "clazzId": "class", "cpi": "cpi"}
        chaoxing = MagicMock()
        chaoxing.login.return_value = {"status": True}
        chaoxing.get_course_list.return_value = [course]
        chaoxing.get_course_point.return_value = {"points": [
            {"id": "finished", "title": "Already done", "has_finished": True, "jobCount": 1},
            {"id": "pending", "title": "Remaining chapter", "has_finished": False, "jobCount": 0},
        ]}
        chaoxing.get_job_list.return_value = ([], {})
        chaoxing.session_manager.context.side_effect = nullcontext
        self.patch("app.main_module.init_chaoxing", return_value=chaoxing)
        self.patch("app.Notification")
        self.launch.side_effect = web._run_study_task
        self.assertEqual(self.resume(task_id).status_code, 200)
        chaoxing.get_course_point.assert_called_once_with("course-1", "class", "cpi")
        chaoxing.get_job_list.assert_called_once()
        self.assertEqual(chaoxing.get_job_list.call_args.args[1]["id"], "pending")
        self.assertEqual(self.store.get_status(task_id)["status"], "completed")
        self.assertEqual(self.store.get_status(task_id)["stats"]["completed_chapters"], 2)
        chaoxing.close.assert_called_once()

    def test_failed_login_or_wrong_account_never_resumes(self):
        task_id = self.start()
        self.restart()
        self.login.login.return_value = {"status": False, "msg": "expired session"}
        self.assertEqual(self.resume(task_id).status_code, 401)
        self.login.login.return_value = {"status": True}
        self.assertEqual(self.resume(task_id, "bob").status_code, 404)
        self.assertEqual(self.resume("old-unpersisted-id").status_code, 404)
        self.launch.assert_not_called()
        self.assertEqual(self.store.get_status(task_id)["status"], "interrupted")

    def test_worker_launch_failure_can_be_retried(self):
        task_id = self.start()
        self.restart()
        self.launch.side_effect = RuntimeError("cannot start worker")
        self.assertEqual(self.resume(task_id).status_code, 500)
        self.assertEqual(self.store.get_status(task_id)["status"], "interrupted")
        self.launch.side_effect = None
        self.assertEqual(self.resume(task_id).status_code, 200)
        self.assertEqual(self.store.get_status(task_id)["status"], "running")

    def test_upstream_key_error_does_not_turn_a_recoverable_task_into_a_404(self):
        task_id = self.start()
        self.restart()
        self.login.login.side_effect = KeyError("unexpected upstream response")
        self.assertEqual(self.resume(task_id).status_code, 500)
        self.launch.assert_not_called()
        self.assertEqual(self.store.get_status(task_id)["status"], "interrupted")

    def test_persisted_settings_are_validated_before_launch(self):
        task_id = self.start()
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        data["tasks"][task_id]["resume_config"]["course_list"] = []
        self.state_file.write_text(json.dumps(data), encoding="utf-8")
        self.restart()
        self.assertEqual(self.resume(task_id).status_code, 400)
        self.launch.assert_not_called()
        self.assertEqual(self.store.get_status(task_id)["status"], "interrupted")


if __name__ == "__main__":
    unittest.main()
