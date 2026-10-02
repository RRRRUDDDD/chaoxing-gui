from copy import deepcopy
from unittest.mock import Mock, patch

from api.course_tools import CARDS_URL, COURSE_URL, STUDY_URL
from api.decode import decode_job_progress
from api.verification import verify_course
from tests.test_course_tools import COURSE, VIDEO, OfflineToolsCase, Response, card_page, course_page


def overview(done=1, total=1):
    return f'已完成任务点: <span>{done}</span>/{total}' + course_page()


class VerificationTests(OfflineToolsCase):
    def setUp(self):
        super().setUp()
        self.client.rate_limiter = Mock()

    def verify(self, pages, cancelled=None):
        self.session.get.side_effect = [Response(page) for page in pages]
        result = verify_course(self.client, COURSE, cancelled)
        self.session.post.assert_not_called()
        self.client.get_job_list.assert_not_called()
        for call in self.session.get.call_args_list:
            self.assertIn(call.args[0], {COURSE_URL, STUDY_URL, CARDS_URL})
            self.assertFalse(call.kwargs['allow_redirects'])
        return result

    def test_confirms_all_raw_tasks_without_calling_study_methods(self):
        result = self.verify([overview(), '<input id="cardcount" value="1">', card_page([VIDEO]), overview()])
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['tasks_checked'], 1)

    def test_pending_and_unsupported_tasks_are_not_completed(self):
        pending = {**VIDEO, 'isPassed': False}
        result = self.verify([overview(0), '<input id="cardcount" value="1">', card_page([pending]), overview(0)])
        self.assertEqual(result['status'], 'pending')
        unsupported = {**VIDEO, 'type': 'unsupported', 'job': True}
        result = self.verify([overview(), '<input id="cardcount" value="1">', card_page([unsupported])])
        self.assertEqual(result['status'], 'unknown')

    def test_missing_aggregate_locked_and_bad_html_fail_closed(self):
        for page in [course_page(), '已完成任务点: 1/1' + course_page(((101, 'locked', True),)), '<html>unknown</html>']:
            with self.subTest(page=page):
                result = self.verify([page])
                self.assertEqual(result['status'], 'unknown')

    def test_missing_pass_flag_and_conflicting_duplicates_are_unknown(self):
        missing = deepcopy(VIDEO)
        missing.pop('isPassed')
        for cards in [[missing], [VIDEO, {**VIDEO, 'isPassed': False}]]:
            result = self.verify([overview(), '<input id="cardcount" value="1">', card_page(cards)])
            self.assertEqual(result['status'], 'unknown')

    def test_duplicate_pages_do_not_inflate_task_counts(self):
        result = self.verify([overview(), '<input id="cardcount" value="2">',
                              card_page([VIDEO]), card_page([VIDEO]), overview()])
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['tasks_checked'], 1)

    def test_probe_requires_an_explicit_end(self):
        with patch('api.course_tools.MAX_PROBED_CARD_PAGES', 2):
            result = self.verify([overview(), '<html>chapter</html>', card_page([VIDEO]), card_page([VIDEO])])
            self.assertEqual(result['status'], 'unknown')
            result = self.verify([overview(), '<html>chapter</html>', card_page([VIDEO]),
                                  '<script>mArg="";</script>', overview()])
            self.assertEqual(result['status'], 'confirmed')

    def test_aggregate_mismatch_or_changed_snapshot_is_unknown(self):
        for after in [overview(0), overview(2, 2)]:
            result = self.verify([overview(), '<input id="cardcount" value="1">', card_page([VIDEO]), after])
            self.assertEqual(result['status'], 'unknown')
        result = self.verify([overview(2, 2), '<input id="cardcount" value="1">', card_page([VIDEO]), overview(2, 2)])
        self.assertEqual(result['status'], 'unknown')

    def test_cancelled_and_captcha_do_not_invoke_side_effects(self):
        self.assertEqual(self.verify([], lambda: True)['status'], 'cancelled')
        self.client.solve_captcha = Mock()
        with patch('api.course_tools.is_captcha_response', return_value=True):
            self.assertEqual(self.verify([overview()])['status'], 'unknown')
        self.client.solve_captcha.assert_not_called()

    def test_progress_parser_rejects_inconsistent_or_impossible_counts(self):
        self.assertEqual(decode_job_progress(overview()), {'done': 1, 'total': 1})
        for page in ['已完成任务点: 2/1', '已完成任务点: 1/1 已完成任务点: 0/1', '<script>已完成任务点: 1/1</script>']:
            self.assertIsNone(decode_job_progress(page))
