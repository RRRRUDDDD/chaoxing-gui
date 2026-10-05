import unittest
from unittest.mock import Mock, patch

from api.base import Account, Chaoxing, RateLimiter


class RateLimiterTests(unittest.TestCase):
    def test_delay_is_reserved_and_waited_outside_the_lock(self):
        limiter = RateLimiter(1)
        now = {"value": 1000.0}
        sleeps = []

        def fake_time():
            return now["value"]

        def fake_sleep(seconds):
            acquired = limiter.lock.acquire(blocking=False)
            self.assertTrue(acquired)
            limiter.lock.release()
            sleeps.append(seconds)
            now["value"] += seconds

        with patch("api.base.time.time", fake_time), patch("api.base.time.sleep", fake_sleep):
            limiter.last_call = 1000.0
            limiter.limit_rate()
            limiter.limit_rate(random_time=True, random_min=0.2, random_max=0.2)
        self.assertEqual(sleeps, [1, 1])

    def test_random_pause_counts_toward_the_interval(self):
        limiter = RateLimiter(0.5)
        now = {"value": 50.0}
        sleeps = []

        def fake_time():
            return now["value"]

        def fake_sleep(seconds):
            sleeps.append(seconds)
            now["value"] += seconds

        with patch("api.base.time.time", fake_time), patch("api.base.time.sleep", fake_sleep), \
                patch("api.base.random.uniform", return_value=0.2):
            limiter.last_call = 50.0
            limiter.limit_rate(random_time=True, random_min=0, random_max=1)
        self.assertEqual(sleeps, [0.5])


class CourseListReuseTests(unittest.TestCase):
    def test_cookie_validation_response_is_reused_for_the_root_folder(self):
        client = Chaoxing(account=Account("alice", ""), tiku=None)
        self.addCleanup(client.close)
        session = client.session_manager.get_session()
        session.cookies.set("_uid", "alice")
        response = Mock(status_code=200, url="https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
                        text="<div>courses</div>")
        with patch.object(client.session_manager, "update_cookies"), \
                patch.object(session, "post", return_value=response) as post, \
                patch.object(session, "get", return_value=Mock(status_code=200, text="")), \
                patch("api.base.decode_course_list", return_value=[{"courseId": "1"}]) as decode, \
                patch("api.base.decode_course_folder", return_value=[]):
            self.assertTrue(client.login(login_with_cookies=True)["status"])
            self.assertEqual(client.get_course_list(), [{"courseId": "1"}])
        self.assertEqual(post.call_count, 1)
        decode.assert_called_once_with("<div>courses</div>")
        self.assertIsNone(client._root_course_list_html)


if __name__ == "__main__":
    unittest.main()
