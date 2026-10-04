import unittest
from unittest.mock import Mock

import requests

from api.work_result import WorkResultReader, parse_records, parse_detail


def records(*rows):
    return '<ul>' + ''.join('<li><span class="viewNum">第' + str(n) + '次</span>' +
                           ('' if s is None else '<span class="viewScore">' + str(s) + '分</span>') +
                           '</li>' for n, s in rows) + '</ul>'


class WorkResultTests(unittest.TestCase):
    def reader(self, pages):
        session = Mock()
        replies = []
        for text in pages:
            reply = requests.Response()
            reply.status_code = 200
            reply._content = text.encode('utf8')
            reply.encoding = 'utf8'
            replies.append(reply)
        session.get.side_effect = replies
        reader = WorkResultReader(session, {'courseId': '1', 'clazzId': '2', 'cpi': '3'},
                                  {'workId': '4', 'workAnswerId': '5'},
                                  cancelled=Mock(return_value=False), wait=Mock(return_value=False),
                                  rate_limit=Mock())
        return reader

    def test_records_are_paired_per_row_and_missing_score_is_unknown(self):
        self.assertEqual(parse_records(records((1, None), (2, 80))), {1: None, 2: 80})
        with self.assertRaises(ValueError):
            parse_records('<html>登录</html>')
        with self.assertRaises(ValueError):
            parse_records(records((1, 80), (1, 90)))

    def test_new_record_is_checked_without_posting_or_assuming_full_marks(self):
        detail = '<div class="TiMu singleQuesId" data="1"><span>我的答案：</span><div>A</div></div>'
        reader = self.reader([records((1, 50)), records((1, 50), (2, 60)), detail])
        reader.capture_baseline()
        result = reader.check()
        self.assertEqual(result['score'], 60)
        self.assertEqual(result['unknown'], 1)
        self.assertEqual(result['result_status'], 'confirmed')
        reader.session.post.assert_not_called()
        self.assertTrue(all(call.kwargs['allow_redirects'] is False for call in reader.session.get.call_args_list))

    def test_stale_records_are_never_reported_as_current(self):
        reader = self.reader([records((1, 100))] * 4)
        reader.capture_baseline()
        self.assertEqual(reader.check()['result_status'], 'unknown')
        self.assertEqual(reader.session.get.call_count, 4)
        self.assertEqual(reader.wait.call_count, 2)

    def test_unknown_baseline_does_not_attribute_a_record(self):
        reader = self.reader(['<html>unrecognized</html>'])
        reader.capture_baseline()
        self.assertEqual(reader.check()['result_status'], 'unknown')
        self.assertEqual(reader.session.get.call_count, 1)

    def test_missing_score_and_concurrent_records_are_not_confirmed(self):
        for rows in [((1, 80), (2, None)), ((1, 80), (2, 90), (3, 100))]:
            reader = self.reader([records((1, 80))] + [records(*rows)] * 3)
            reader.capture_baseline()
            result = reader.check()
            self.assertEqual(result['result_status'], 'unknown')
            self.assertIsNone(result['score'])

    def test_timeout_and_cancellation_do_not_resubmit(self):
        reader = self.reader([records((1, 80))])
        reader.capture_baseline()
        reader.session.get.side_effect = requests.Timeout('private detail')
        result = reader.check()
        self.assertEqual(result['result_status'], 'unknown')
        self.assertNotIn('private detail', result['reason'])
        reader.cancelled.return_value = True
        self.assertEqual(reader.check()['result_status'], 'cancelled')
        reader.session.post.assert_not_called()

    def test_cancellation_during_detail_read_is_preserved(self):
        reader = self.reader([records((1, 50))])
        reader.capture_baseline()
        reader._read = Mock(side_effect=[records((1, 50), (2, 60)), InterruptedError()])
        self.assertEqual(reader.check()['result_status'], 'cancelled')
        reader.session.post.assert_not_called()

    def test_detail_missing_answer_is_unknown_not_equal_empty(self):
        rows = parse_detail('<div class="TiMu singleQuesId" data="1"></div>')
        self.assertFalse(rows[0]['parse_ok'])
        self.assertIsNone(rows[0]['same_text'])
