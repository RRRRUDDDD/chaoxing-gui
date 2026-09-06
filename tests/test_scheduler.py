import contextvars
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main
from api.base import StudyResult
from api.exceptions import InputFormatError


COURSE = {'courseId': 'course-1', 'clazzId': 'class-1', 'cpi': 'cpi', 'title': 'Course'}


def point(index=1, **values):
    return {'id': str(index), 'title': f'Chapter {index}', 'has_finished': False,
            'jobCount': 1, **values}


def config(**values):
    return {'jobs': 2, 'speed': 1.0, 'retry_interval': 0,
            'notopen_action': 'retry', **values}


class SchedulerTests(unittest.TestCase):
    def test_exhausted_retry_propagates_failure_and_closes_threads(self):
        callback = Mock()
        task = main.ChapterTask(0, point())
        processor = main.JobProcessor(Mock(session_manager=None), COURSE, [task], config(chapter_result_callback=callback))
        with patch('main.process_chapter', return_value=main.ChapterResult.ERROR) as run:
            result = processor.run()
        self.assertFalse(result.success)
        self.assertEqual(result.failed, [task])
        self.assertEqual(run.call_count, processor.max_tries)
        callback.assert_called_once_with(COURSE, task.point, main.ChapterResult.ERROR)
        self.assertTrue(all(not thread.is_alive() for thread in processor.threads))

    def test_successive_courses_leave_no_scheduler_threads(self):
        for _ in range(3):
            processor = main.JobProcessor(Mock(session_manager=None), COURSE, [main.ChapterTask(0, point())], config())
            with patch('main.process_chapter', return_value=main.ChapterResult.SUCCESS):
                self.assertTrue(processor.run().success)
            self.assertTrue(all(not thread.is_alive() for thread in processor.threads))

    def test_unopened_chapter_is_skipped_or_failed(self):
        for action in ('continue', 'retry'):
            with self.subTest(action=action):
                processor = main.JobProcessor(Mock(session_manager=None), COURSE, [main.ChapterTask(0, point())],
                                              config(notopen_action=action))
                with patch('main.process_chapter', return_value=main.ChapterResult.NOT_OPEN):
                    result = processor.run()
                self.assertFalse(result.success)
                self.assertEqual(len(result.skipped if action == 'continue' else result.failed), 1)

    def test_worker_exceptions_are_reported_and_joined(self):
        processor = main.JobProcessor(Mock(session_manager=None), COURSE, [main.ChapterTask(0, point())], config())
        with patch('main.process_chapter', side_effect=RuntimeError('offline failure')):
            result = processor.run()
        self.assertEqual(len(result.failed), 1)
        self.assertTrue(all(not thread.is_alive() for thread in processor.threads))

    def test_partial_thread_start_failure_joins_already_started_retry_worker(self):
        original_start = threading.Thread.start
        def start(thread):
            if thread.name.startswith('chaoxing-worker-'):
                raise RuntimeError('cannot start worker')
            return original_start(thread)
        processor = main.JobProcessor(Mock(session_manager=None), COURSE,
                                      [main.ChapterTask(0, point())], config())
        with patch.object(threading.Thread, 'start', start):
            with self.assertRaisesRegex(RuntimeError, 'cannot start worker'):
                processor.run()
        self.assertTrue(all(not thread.is_alive() for thread in processor.threads))

    def test_task_context_reaches_workers_and_job_pool(self):
        marker = contextvars.ContextVar('scheduler_test_marker', default=None)
        token = marker.set('task-a')
        seen = []
        chaoxing = SimpleNamespace(rate_limiter=Mock(), get_job_list=Mock(return_value=(
            [{'type': 'read', 'jobid': 'j1'}, {'type': 'read', 'jobid': 'j2'}], {})))
        try:
            def run_job(*args, **kwargs):
                seen.append(marker.get())
                return StudyResult.SUCCESS
            with patch('main.process_job', side_effect=run_job):
                result = main.JobProcessor(chaoxing, COURSE, [main.ChapterTask(0, point())], config()).run()
            self.assertTrue(result.success)
            self.assertEqual(seen, ['task-a', 'task-a'])
        finally:
            marker.reset(token)

    def test_partial_job_failure_does_not_call_done(self):
        done = Mock()
        chapter = point(jobCount=2)
        chaoxing = SimpleNamespace(rate_limiter=Mock(), get_job_list=Mock(return_value=(
            [{'type': 'read', 'jobid': 'j1'}, {'type': 'read', 'jobid': 'j2'}], {})))
        with patch('main.process_job', side_effect=[StudyResult.SUCCESS, StudyResult.ERROR]):
            result = main.process_chapter(chaoxing, COURSE, chapter, 1, config(chapter_done_callback=done))
        self.assertEqual(result, main.ChapterResult.ERROR)
        self.assertEqual(chapter['_task_stats'], {'total': 2, 'completed': 1, 'failed': 1, 'skipped': 0})
        done.assert_not_called()

    def test_shrinking_retry_list_keeps_completed_job_counts(self):
        first = {'type': 'read', 'jobid': 'j1'}
        second = {'type': 'read', 'jobid': 'j2'}
        chapter = point(jobCount=2)
        client = SimpleNamespace(rate_limiter=Mock(), get_job_list=Mock(side_effect=[
            ([first, second], {}), ([second], {}), ([second], {})]))
        processor = main.JobProcessor(client, COURSE, [main.ChapterTask(0, chapter)], config())
        processor.max_tries = 3
        with patch('main.process_job', side_effect=lambda _client, _course, job, *_args, **_kw:
                   StudyResult.SUCCESS if job['jobid'] == 'j1' else StudyResult.ERROR) as run:
            result = processor.run()
        self.assertFalse(result.success)
        self.assertEqual(chapter['_task_stats'], {'total': 2, 'completed': 1, 'failed': 1, 'skipped': 0})
        self.assertEqual(run.call_count, 4)

    def test_passed_metadata_finishes_retry_without_losing_prior_jobs(self):
        first = {'type': 'read', 'jobid': 'j1'}
        second = {'type': 'read', 'jobid': 'j2'}
        chapter = point(jobCount=2)
        client = SimpleNamespace(rate_limiter=Mock(), get_job_list=Mock(side_effect=[
            ([first, second], {}), ([], {'passed_jobs': [first, second]})]))
        with patch('main.process_job', side_effect=[StudyResult.SUCCESS, StudyResult.ERROR]):
            result = main.JobProcessor(client, COURSE, [main.ChapterTask(0, chapter)], config()).run()
        self.assertTrue(result.success)
        self.assertEqual(result.tasks[0].result, main.ChapterResult.SUCCESS)
        self.assertEqual(chapter['_task_stats'], {'total': 2, 'completed': 2, 'failed': 0, 'skipped': 0})

    def test_missing_failed_job_without_passed_evidence_stays_failed(self):
        job = {'type': 'read', 'jobid': 'j1'}
        chapter = point()
        client = SimpleNamespace(rate_limiter=Mock(), get_job_list=Mock(side_effect=[
            ([job], {}), ([], {})]))
        processor = main.JobProcessor(client, COURSE, [main.ChapterTask(0, chapter)], config())
        processor.max_tries = 2
        with patch('main.process_job', return_value=StudyResult.ERROR):
            result = processor.run()
        self.assertFalse(result.success)
        self.assertEqual(chapter['_task_stats']['failed'], 1)

    def test_skipped_jobs_are_not_completed(self):
        done = Mock()
        chaoxing = SimpleNamespace(rate_limiter=Mock(), get_job_list=Mock(return_value=(
            [{'type': 'workid', 'jobid': 'j1'}], {})))
        with patch('main.process_job', return_value=StudyResult.SKIPPED):
            result = main.process_chapter(chaoxing, COURSE, point(), 1, config(chapter_done_callback=done))
        self.assertEqual(result, main.ChapterResult.SKIPPED)
        done.assert_not_called()

    def test_course_uses_provided_chapter_snapshot(self):
        chaoxing = Mock(session_manager=None)
        with patch('main.process_chapter', return_value=main.ChapterResult.SUCCESS):
            result = main.process_course(chaoxing, COURSE, config(), point_list={'points': [point()]})
        self.assertTrue(result.success)
        chaoxing.get_course_point.assert_not_called()

    def test_invalid_concurrency_never_starts_threads(self):
        for value in (0, -1, 17, 1.5, True, None, 'bad'):
            with self.subTest(value=value), self.assertRaises((ValueError, InputFormatError)):
                main.JobProcessor(Mock(session_manager=None), COURSE, [main.ChapterTask(0, point())], config(jobs=value))

    def test_api_selection_rejects_empty_or_any_unknown_id(self):
        with patch('builtins.input', side_effect=AssertionError('API must not prompt')):
            for ids in ([], None, ['unknown'], ['course-1', 'unknown']):
                with self.subTest(ids=ids), self.assertRaises(InputFormatError):
                    main.filter_courses([COURSE], ids, interactive=False)
        self.assertEqual(main.filter_courses([COURSE], ['course-1', 'course-1']), [COURSE])

    def test_cli_blank_selection_explicitly_selects_all(self):
        with patch('builtins.input', return_value=''), patch('builtins.print'):
            self.assertEqual(main.filter_courses([COURSE], None, interactive=True), [COURSE])


if __name__ == '__main__':
    unittest.main()
