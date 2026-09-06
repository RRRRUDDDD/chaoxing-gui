import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import main
from api.base import AI, Account, Chaoxing, StudyResult
from api.live_process import LiveProcessor
from tests.test_scheduler import COURSE, config, point


class StudyResultTests(unittest.TestCase):
    def setUp(self):
        self.client = Chaoxing(account=Account('offline', 'secret'), tiku=None)
        self.client.rate_limiter = Mock()
        self.addCleanup(self.client.close)

    def response(self, status):
        response = requests.Response()
        response.status_code = status
        response._content = b'{}'
        response.url = 'https://example.invalid/cards'
        return response

    def test_http_503_cannot_become_a_successful_empty_chapter(self):
        session = SimpleNamespace(get=Mock(return_value=self.response(503)))
        done = Mock()
        with patch.object(self.client.session_manager, 'get_session', return_value=session):
            with self.assertRaises(requests.HTTPError):
                main.process_chapter(self.client, COURSE, point(), 1, config(chapter_done_callback=done))
        done.assert_not_called()

    def test_empty_page_failure_is_propagated(self):
        session = SimpleNamespace(get=Mock(return_value=self.response(200)))
        with patch.object(self.client.session_manager, 'get_session', return_value=session), \
             patch('api.base.decode_course_card', return_value=([], {})), \
             patch.object(self.client, 'study_emptypage', return_value=StudyResult.ERROR):
            with self.assertRaises(requests.RequestException):
                self.client.get_job_list(COURSE, point())

    def test_confirmed_empty_page_has_an_explicit_empty_result(self):
        session = SimpleNamespace(get=Mock(return_value=self.response(200)))
        with patch.object(self.client.session_manager, 'get_session', return_value=session), \
             patch('api.base.decode_course_card', return_value=([], {})), \
             patch.object(self.client, 'study_emptypage', return_value=StudyResult.SUCCESS):
            result = main.process_chapter(self.client, COURSE, point(jobCount=0), 1, config())
        self.assertEqual(result, main.ChapterResult.EMPTY)

    def test_passed_jobs_across_cards_are_preserved_without_empty_page_request(self):
        session = SimpleNamespace(get=Mock(return_value=self.response(200)))
        passed = [{'type': 'read', 'jobid': 'j1'}, {'type': 'read', 'jobid': 'j2'}]
        responses = [( [], {'passed_jobs': [job]}) for job in passed] + [([], {})] * 5
        with patch.object(self.client.session_manager, 'get_session', return_value=session), \
             patch('api.base.decode_course_card', side_effect=responses), \
             patch.object(self.client, 'study_emptypage') as empty:
            result = main.process_chapter(self.client, COURSE, point(jobCount=2), 1, config())
        self.assertEqual(result, main.ChapterResult.SUCCESS)
        empty.assert_not_called()

    def test_web_outcome_remains_partial_after_pending_list_shrinks(self):
        import app
        from api.task_state import TaskStore
        store = TaskStore()
        self.addCleanup(store.close)
        task_id = store.create('offline', app._initial_status(), {'courses': [], 'active_jobs': {}})
        progress = app._StudyProgress(store, task_id)
        progress.set_courses([COURSE])
        chapter = point(jobCount=2)
        snapshot = {'points': [chapter]}
        progress.add_chapters(COURSE, snapshot)
        first, second = ({'type': 'read', 'jobid': key} for key in ('j1', 'j2'))
        client = SimpleNamespace(rate_limiter=Mock(), get_job_list=Mock(side_effect=[
            ([first, second], {})] + [([second], {})] * 4))
        with patch('main.process_job', side_effect=lambda _client, _course, job, *_a, **_k:
                   StudyResult.SUCCESS if job['jobid'] == 'j1' else StudyResult.ERROR):
            result = main.process_course(client, COURSE, config(chapter_result_callback=progress.chapter_result),
                                         point_list=snapshot)
        progress.finish_course(COURSE, result)
        self.assertEqual(progress.outcome(), 'partial')
        self.assertEqual(store.get_status(task_id)['stats']['completed_tasks'], 1)

    def test_disabled_question_provider_is_a_skip(self):
        self.assertEqual(self.client.study_work(COURSE, {}, {}), StudyResult.SKIPPED)

    def exercise_work(self, *, save_only=False, expired=False, query_error=False):
        question = {'id': 'q1', 'title': 'Offline question', 'type': 'single',
                    'options': 'A. first\nB. second', 'answerField': {}}
        questions = {'questions': [question]}
        provider = AI() if query_error else Mock()
        provider.DISABLE = False
        provider.COVER_RATE = 0
        provider.get_submit_params = Mock(return_value='1' if save_only else '')
        provider.query = Mock(side_effect=RuntimeError('query failed')) if query_error else Mock(return_value='A')
        self.client.tiku = provider
        session = Mock()
        session.get.return_value = Mock(text='<form>offline</form>', status_code=200)
        session.post.return_value = Mock(status_code=200)
        session.post.return_value.json.return_value = {
            'status': not expired, 'msg': '作业已过期' if expired else 'accepted'}
        job = {'jobid': 'work-1', 'enc': 'offline-token'}
        info = {'knowledgeid': 'chapter', 'ktoken': 'offline-token', 'cpi': 'cpi'}
        with patch.object(self.client.session_manager, 'get_session', return_value=session), \
             patch('api.base.decode_questions_info', return_value=questions):
            if query_error:
                with self.assertRaisesRegex(RuntimeError, 'query failed'):
                    self.client.study_work(COURSE, job, info)
                session.post.assert_not_called()
                return None
            result = self.client.study_work(COURSE, job, info)
        return result

    def test_saved_but_unsubmitted_work_is_skipped(self):
        self.assertEqual(self.exercise_work(save_only=True), StudyResult.SKIPPED)

    def test_expired_work_is_skipped(self):
        self.assertEqual(self.exercise_work(expired=True), StudyResult.SKIPPED)

    def test_submitted_work_is_completed(self):
        self.assertEqual(self.exercise_work(), StudyResult.SUCCESS)

    def test_ai_worker_failure_prevents_form_submission(self):
        self.exercise_work(query_error=True)

    def test_live_failure_is_reported_by_job(self):
        self.client.session_manager.set_cookies({'_uid': 'offline'})
        with patch('main.LiveProcessor.run_live', return_value=False):
            result = main.process_job(self.client, COURSE, {'type': 'live', 'jobid': 'j'}, {}, 1)
        self.assertEqual(result, StudyResult.ERROR)

    def test_failed_live_retry_does_not_report_completion(self):
        live = Mock(name='offline-live')
        live.get_status.return_value = {'temp': {'data': {'duration': 59}}}
        live.do_finish.return_value = False
        with patch('api.live_process.time.sleep'):
            self.assertFalse(LiveProcessor.run_live(live))
        self.assertEqual(live.do_finish.call_count, 2)

    def test_cli_failure_does_not_send_success_notification_and_closes_client(self):
        client = Mock()
        client.login.return_value = {'status': True}
        client.get_course_list.return_value = [COURSE]
        notification = Mock()
        notification.get_notification_from_config.return_value = notification
        common = config(course_list=['course-1'])
        outcome = main.CourseResult((main.ChapterTask(0, point(), main.ChapterResult.ERROR),))
        with patch('main.init_config', return_value=(common, {}, {})), \
             patch('main.init_chaoxing', return_value=client), \
             patch('main.Notification', return_value=notification), \
             patch('main.process_course', return_value=outcome):
            self.assertEqual(main.main(), 1)
        self.assertNotIn('所有课程学习任务已完成', notification.send.call_args.args[0])
        client.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
