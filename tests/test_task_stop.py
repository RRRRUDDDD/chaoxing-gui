import threading
import unittest
from unittest.mock import Mock, patch

import requests

import app as web
import main
from api.base import AI, Account, Chaoxing, StudyResult
from api.live_process import LiveProcessor
from api.task_state import TaskAlreadyRunning, TaskStore
from tests.test_scheduler import COURSE, config, point


class OfflineStopTests(unittest.TestCase):
    def setUp(self):
        self.patch("requests.sessions.Session.request", side_effect=AssertionError("Offline test attempted network access"))
        self.patch("builtins.input", side_effect=AssertionError("A stopped task must not prompt"))

    def patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result


class TaskStopApiTests(OfflineStopTests):
    def setUp(self):
        super().setUp()
        self.store = TaskStore()
        self.addCleanup(self.store.close)
        self.patch("app.task_store", self.store)
        self.client = web.app.test_client()
        self.task_id = self.store.create("offline", web._initial_status(), {"courses": [], "active_jobs": {}})

    def stop(self, username="offline", task_id=None):
        return self.client.post(f"/api/task/{task_id or self.task_id}/stop", json={"username": username})

    def test_stop_validates_json_and_account_before_setting_signal(self):
        for body in ([], None, {}, {"username": " "}, {"username": 3}):
            with self.subTest(body=body):
                response = self.client.post(f"/api/task/{self.task_id}/stop", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(self.store.is_cancelled(self.task_id))
        self.assertEqual(self.stop("another-account").status_code, 404)
        self.assertEqual(self.stop(task_id="missing-task").status_code, 404)
        self.assertFalse(self.store.is_cancelled(self.task_id))

    def test_stop_is_idempotent_and_account_stays_reserved_until_worker_finishes(self):
        for username in (" offline ", "offline"):
            response = self.stop(username)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["data"]["state"], "stopping")
        self.assertTrue(self.store.is_cancelled(self.task_id))
        self.assertEqual(self.store.get_status(self.task_id)["status"], "running")
        with self.assertRaises(TaskAlreadyRunning):
            self.store.create("offline", {}, {})
        self.store.finish(self.task_id, "cancelled")
        self.assertEqual(self.stop().get_json()["data"]["state"], "already_finished")
        self.assertNotEqual(self.store.create("offline", {}, {}), self.task_id)

    def test_interrupted_task_stops_without_needing_a_worker(self):
        self.store.interrupt(self.task_id, "worker was not started")
        self.assertEqual(self.stop().get_json()["data"]["state"], "cancelled")
        self.assertEqual(self.store.get_status(self.task_id)["status"], "cancelled")
        self.assertNotEqual(self.store.create("offline", {}, {}), self.task_id)

    def test_unrelated_key_error_is_a_server_error_and_does_not_hide_task(self):
        with patch.object(self.store, "request_cancel", side_effect=KeyError("invalid internal record")):
            response = self.stop()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.store.get_status(self.task_id)["status"], "running")


class StudyTaskStopTests(OfflineStopTests):
    def setUp(self):
        super().setUp()
        self.store = TaskStore()
        self.addCleanup(self.store.close)
        self.patch("app.task_store", self.store)
        self.task_id = self.store.create("offline", web._initial_status(), {"courses": [], "active_jobs": {}})
        self.chaoxing = Mock(session_manager=None)
        self.chaoxing.login.return_value = {"status": True}
        self.chaoxing.get_course_list.return_value = [COURSE]
        self.chaoxing.get_course_point.return_value = {"points": []}
        self.init = self.patch("app.main_module.init_chaoxing", return_value=self.chaoxing)
        self.notification = Mock()
        self.patch("app.Notification").return_value.get_notification_from_config.return_value = self.notification

    def cancel(self):
        return self.store.request_cancel(self.task_id, "offline")

    def run_task(self):
        common = config(username="offline", password="unused", use_cookies=False, course_list=[COURSE["courseId"]])
        web._run_study_task(self.task_id, self.store, common, {}, {}, {})

    def assert_cancelled(self):
        status = self.store.get_status(self.task_id)
        self.assertEqual(status["status"], "cancelled")
        self.assertNotIn("error", status)
        details = self.store.get_details(self.task_id)
        self.assertEqual(details["active_jobs"], {})
        for course in details["courses"]:
            self.assertNotIn(course["status"], {"pending", "running"})
            for chapter in course["chapters"]:
                self.assertNotIn(chapter["status"], {"pending", "running"})
        self.assertNotEqual(self.store.create("offline", {}, {}), self.task_id)

    def test_stop_before_worker_runs_does_not_initialize_or_login(self):
        self.cancel()
        self.run_task()
        self.init.assert_not_called()
        self.chaoxing.login.assert_not_called()
        self.assert_cancelled()

    def test_stop_during_login_does_not_begin_course_discovery(self):
        def login(**kwargs):
            self.cancel()
            return {"status": True}

        self.chaoxing.login.side_effect = login
        self.run_task()
        self.chaoxing.get_course_list.assert_not_called()
        self.chaoxing.close.assert_called_once_with()
        self.assert_cancelled()

    def test_stop_during_chapter_discovery_does_not_begin_study(self):
        def get_points(*args):
            self.cancel()
            return {"points": [point()]}

        self.chaoxing.get_course_point.side_effect = get_points
        with patch("app.main_module.process_course", return_value=main.CourseResult(())) as process:
            self.run_task()
        process.assert_not_called()
        self.assertEqual(self.store.get_status(self.task_id)["stats"]["completed_courses"], 0)
        self.assert_cancelled()

    def test_stop_during_cleanup_waits_for_resources_and_preserves_final_logs(self):
        closing = threading.Event()
        release = threading.Event()

        def close():
            closing.set()
            if not release.wait(3):
                raise RuntimeError("test did not release resource cleanup")
            web.logger.info("stop-cleanup-tail")

        self.chaoxing.close.side_effect = close
        thread = threading.Thread(target=self.run_task, name="offline-stop-task", daemon=True)
        thread.start()
        try:
            self.assertTrue(closing.wait(3))
            self.assertEqual(self.cancel(), "stopping")
            self.assertEqual(self.store.get_status(self.task_id)["status"], "running")
            with self.assertRaises(TaskAlreadyRunning):
                self.store.create("offline", {}, {})
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.chaoxing.close.assert_called_once_with()
        self.assert_cancelled()
        self.assertIn("已手动停止", self.notification.send.call_args.args[0])
        logs = self.store.read_logs(self.task_id)
        self.assertIn("stop-cleanup-tail", "\n".join(entry["message"] for entry in logs["data"]))
        self.assertEqual(self.store.read_logs(self.task_id, logs["next_cursor"])["data"], [])

    def test_stop_accepted_during_final_log_flush_wins_terminal_publication(self):
        complete = web.logger.complete

        def flush():
            self.cancel()
            return complete()

        with patch.object(web.logger, "complete", side_effect=flush):
            self.run_task()
        self.assert_cancelled()


class StudyOperationStopTests(OfflineStopTests):
    def setUp(self):
        super().setUp()
        self.cancel = threading.Event()
        self.chaoxing = Chaoxing(account=Account("offline-stop", "unused"), tiku=None)
        self.addCleanup(self.chaoxing.close)
        self.session = Mock()
        self.patch("api.base.tqdm", return_value=Mock())
        self.patch("api.base.time.sleep", side_effect=lambda delay: self.cancel.set())
        self.patch("api.base.decode_questions_info", side_effect=lambda text: {
            "questions": [
                {"id": str(index), "title": f"Question {index}", "type": "single",
                 "options": "A. first\nB. second", "answerField": {}}
                for index in range(8)
            ],
        })
        self.chaoxing.get_fid = Mock(return_value="offline")
        self.patch("api.session.SessionManager.get_session", return_value=self.session)

    def video(self, **overrides):
        job = {"type": "video", "jobid": "video-1", "objectid": "object-1", "name": "Offline video", "playTime": 0}
        job.update(overrides)
        self.session.get.return_value.json.return_value = {
            "status": "success", "dtoken": "offline", "duration": 1800, "crc": "crc", "key": "key",
        }
        return job

    def test_stopped_video_does_not_fetch_or_report_progress(self):
        job = self.video()
        self.cancel.set()
        with patch.object(self.chaoxing, "video_progress_log") as report:
            result = self.chaoxing.study_video(COURSE, job, {}, cancel_check=self.cancel.is_set)
        self.assertEqual(result, StudyResult.SKIPPED)
        self.session.get.assert_not_called()
        report.assert_not_called()

    def test_stop_between_initial_video_reports_prevents_completion_report(self):
        def report(*args, **kwargs):
            self.cancel.set()
            return False, 200

        with patch.object(self.chaoxing, "video_progress_log", side_effect=report) as reports, \
             patch("api.base.tqdm") as bars:
            result = self.chaoxing.study_video(COURSE, self.video(), {}, cancel_check=self.cancel.is_set)
        self.assertEqual(result, StudyResult.SKIPPED)
        self.assertEqual(reports.call_count, 1)
        bars.return_value.close.assert_called_once_with()

    def test_running_video_stops_after_one_sleep_slice(self):
        with patch.object(self.chaoxing, "video_progress_log", return_value=(False, 200)) as reports, \
             patch("api.base.tqdm") as bars, \
             patch("api.base.time.sleep", side_effect=lambda delay: self.cancel.set()) as sleep:
            result = self.chaoxing.study_video(COURSE, self.video(), {}, cancel_check=self.cancel.is_set)
        self.assertEqual(result, StudyResult.SKIPPED)
        self.assertEqual(reports.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertLessEqual(sleep.call_args.args[0], 1)
        bars.return_value.close.assert_called_once_with()

    def test_video_failure_closes_progress_resource(self):
        with patch.object(self.chaoxing, "video_progress_log", side_effect=requests.Timeout("offline timeout")), \
             patch("api.base.tqdm") as bars:
            with self.assertRaises(requests.Timeout):
                self.chaoxing.study_video(COURSE, self.video(), {}, cancel_check=self.cancel.is_set)
        bars.return_value.close.assert_called_once_with()

    def test_cancelled_video_does_not_start_audio_fallback(self):
        def failed_video(*args, **kwargs):
            self.cancel.set()
            return StudyResult.ERROR

        with patch.object(self.chaoxing, "study_video", side_effect=failed_video) as study:
            result = main.process_job(self.chaoxing, COURSE, self.video(), {}, 1, cancel_check=self.cancel.is_set)
        self.assertEqual(result, StudyResult.SKIPPED)
        self.assertEqual(study.call_count, 1)

    def test_video_forbidden_backoff_stops_before_refreshing(self):
        with patch.object(self.chaoxing, "video_progress_log", side_effect=[(False, 200), (False, 200), (False, 403)]), \
             patch.object(self.chaoxing, "_recover_after_forbidden", return_value=None) as recover:
            result = self.chaoxing.study_video(COURSE, self.video(playTime=1800000), {}, cancel_check=self.cancel.is_set)
        self.assertEqual(result, StudyResult.SKIPPED)
        recover.assert_not_called()

    def work(self, *, ai=False):
        provider = Mock(spec=AI) if ai else Mock()
        provider.DISABLE = False
        provider.COVER_RATE = 0
        provider.get_submit_params.return_value = ""
        provider.query.side_effect = lambda question: (self.cancel.set(), "A")[1]
        self.chaoxing.tiku = provider
        self.chaoxing.kwargs["ai_concurrency"] = 1
        self.session.get.return_value = Mock(text="<form>offline</form>", status_code=200)
        self.session.post.return_value = Mock(status_code=200)
        self.session.post.return_value.json.return_value = {"status": True, "msg": "accepted"}
        job = {"type": "workid", "jobid": "work-1", "enc": "offline"}
        info = {"knowledgeid": "chapter", "ktoken": "offline", "cpi": "cpi"}
        return provider, job, info

    def test_stopped_work_does_not_fetch_questions(self):
        provider, job, info = self.work()
        self.cancel.set()
        result = main.process_job(self.chaoxing, COURSE, job, info, 1, cancel_check=self.cancel.is_set)
        self.assertEqual(result, StudyResult.SKIPPED)
        self.session.get.assert_not_called()
        provider.query.assert_not_called()
        self.session.post.assert_not_called()

    def test_work_stop_skips_remaining_questions_and_never_submits(self):
        for ai in (False, True):
            with self.subTest(ai=ai):
                self.cancel.clear()
                self.session.reset_mock()
                provider, job, info = self.work(ai=ai)
                result = main.process_job(self.chaoxing, COURSE, job, info, 1, cancel_check=self.cancel.is_set)
                self.assertEqual(result, StudyResult.SKIPPED)
                self.assertEqual(provider.query.call_count, 1)
                self.session.post.assert_not_called()

    def test_work_fetch_backoff_stops_before_retrying(self):
        provider, job, info = self.work()
        self.session.get.side_effect = requests.RequestException("offline failure")
        result = main.process_job(self.chaoxing, COURSE, job, info, 1, cancel_check=self.cancel.is_set)
        self.assertEqual(result, StudyResult.SKIPPED)
        self.assertEqual(self.session.get.call_count, 1)
        provider.query.assert_not_called()
        self.session.post.assert_not_called()

    def test_work_query_delay_is_interruptible(self):
        provider, job, info = self.work()
        self.chaoxing.kwargs["query_delay"] = 300
        with patch("api.base.time.sleep", side_effect=lambda delay: self.cancel.set()) as sleep:
            result = main.process_job(self.chaoxing, COURSE, job, info, 1, cancel_check=self.cancel.is_set)
        self.assertEqual(result, StudyResult.SKIPPED)
        provider.query.assert_not_called()
        self.session.post.assert_not_called()
        self.assertLessEqual(sleep.call_args.args[0], 1)


class ChapterDiscoveryStopTests(OfflineStopTests):
    def setUp(self):
        super().setUp()
        self.cancel = threading.Event()
        self.chaoxing = Chaoxing(account=Account("offline-cards", "unused"), tiku=None)
        self.addCleanup(self.chaoxing.close)
        self.chaoxing.rate_limiter = Mock()
        self.session = Mock()
        self.response = Mock(status_code=200, text="<html>offline</html>")
        self.session.get.return_value = self.response
        self.patch("api.session.SessionManager.get_session", return_value=self.session)
        self.empty = Mock(return_value=StudyResult.SUCCESS)
        self.chaoxing.study_emptypage = self.empty

    def test_stop_during_card_request_does_not_fetch_remaining_cards(self):
        def get(*args, **kwargs):
            self.cancel.set()
            return self.response

        self.session.get.side_effect = get
        with patch("api.base.decode_course_card", return_value=([], {})) as decode:
            result = main.process_chapter(self.chaoxing, COURSE, point(), 1,
                                          config(cancel_check=self.cancel.is_set))
        self.assertEqual(result, main.ChapterResult.SKIPPED)
        self.session.get.assert_called_once()
        decode.assert_not_called()
        self.empty.assert_not_called()

    def test_stop_after_last_card_does_not_submit_empty_page_completion(self):
        parsed = []

        def decode(text):
            parsed.append(text)
            if len(parsed) == 7:
                self.cancel.set()
            return [], {}

        done = Mock()
        with patch("api.base.decode_course_card", side_effect=decode):
            result = main.process_chapter(self.chaoxing, COURSE, point(), 1,
                                          config(cancel_check=self.cancel.is_set, chapter_done_callback=done))
        self.assertEqual(result, main.ChapterResult.SKIPPED)
        self.assertEqual(self.session.get.call_count, 7)
        self.empty.assert_not_called()
        done.assert_not_called()


class LiveStopTests(OfflineStopTests):
    def setUp(self):
        super().setUp()
        self.cancel = threading.Event()
        self.live = Mock()
        self.live.get_status.return_value = {"temp": {"data": {"duration": 180}}}
        self.live.do_finish.return_value = True

    def test_stopped_live_does_not_fetch_status(self):
        self.cancel.set()
        self.assertFalse(LiveProcessor.run_live(self.live, cancel_check=self.cancel.is_set))
        self.live.get_status.assert_not_called()
        self.live.do_finish.assert_not_called()

    def test_stop_interrupts_live_sleep_and_job_is_skipped(self):
        client = Mock()
        with patch("main.Live", return_value=self.live), \
             patch("api.live_process.time.sleep", side_effect=lambda delay: self.cancel.set()) as sleep:
            result = main.process_job(client, COURSE, {"type": "live", "jobid": "live-1"}, {}, 1,
                                      cancel_check=self.cancel.is_set)
        self.assertEqual(result, StudyResult.SKIPPED)
        self.live.do_finish.assert_called_once_with()
        self.assertLessEqual(sleep.call_args.args[0], 1)

    def test_stop_interrupts_live_retry_without_another_submission(self):
        self.live.do_finish.return_value = False
        with patch("api.live_process.time.sleep", side_effect=lambda delay: self.cancel.set()):
            self.assertFalse(LiveProcessor.run_live(self.live, cancel_check=self.cancel.is_set))
        self.live.do_finish.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
