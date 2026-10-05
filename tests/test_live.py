"""Tests for live attachment request URLs (encoding of attachment params)."""

import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

from api.live import Live


def make_live(stream_name="直播流-1", vdoid="vd-1"):
    attachment = {"property": {"streamName": stream_name, "vdoid": vdoid,
                               "liveId": "L1", "_jobid": "j-1"}}
    defaults = {"userid": "u-1", "clazzId": "c-1", "knowledgeid": "k-1"}
    return Live(attachment, defaults, "course-1", Mock())


class LiveRequestUrlTests(unittest.TestCase):
    def test_do_finish_encodes_attachment_params(self):
        live = make_live(stream_name="a&b c")
        live.session.get.return_value = Mock(text="@success")
        self.assertTrue(live.do_finish())
        parsed = urlsplit(live.session.get.call_args.args[0])
        query = parse_qs(parsed.query)
        self.assertEqual((parsed.scheme, parsed.netloc, parsed.path),
                         ("https", "zhibo.chaoxing.com", "/saveTimePc"))
        self.assertEqual(query["streamName"], ["a&b c"])
        self.assertEqual(query["vdoid"], ["vd-1"])
        self.assertEqual(query["userId"], ["u-1"])
        self.assertEqual(query["courseId"], ["course-1"])
        self.assertEqual(query["isStart"], ["0"])

    def test_get_status_encodes_attachment_params(self):
        live = make_live()
        live.session.get.return_value = Mock(text='{"temp": {}}')
        self.assertEqual(live.get_status(), {"temp": {}})
        parsed = urlsplit(live.session.get.call_args.args[0])
        query = parse_qs(parsed.query)
        self.assertEqual((parsed.netloc, parsed.path),
                         ("mooc1.chaoxing.com", "/ananas/live/liveinfo"))
        self.assertEqual(query["liveid"], ["L1"])
        self.assertEqual(query["jobid"], ["j-1"])
        self.assertEqual(query["ut"], ["s"])

    def test_missing_params_are_reported_not_requested(self):
        live = make_live(stream_name="")
        self.assertFalse(live.do_finish())
        live.session.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
