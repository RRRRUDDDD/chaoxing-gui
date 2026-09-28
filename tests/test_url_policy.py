"""One table for every upstream URL check, so the shared policy cannot drift."""

import unittest

from api.course_tools import _trusted_url
from api.reading_browser import allow_reading_url
from api.reading_time import READ_BOOK_PATH, _platform_url
from api.url_policy import UrlMessages, canonical_https_url

BOOK = "https://mooc1.chaoxing.com/mooc-ans/course/1.html"
REJECTED_BY_ALL = [
    "https://user:pw@mooc1.chaoxing.com/mooc-ans/course/1.html",
    "https://user@mooc1.chaoxing.com/mooc-ans/course/1.html",
    "https://mooc1.chaoxing.com:8443/mooc-ans/course/1.html",
    "https://mooc1.chaoxing.com:abc/mooc-ans/course/1.html",
    BOOK + "#frag",
    "ftp://mooc1.chaoxing.com/mooc-ans/course/1.html",
    "javascript:alert(1)",
    BOOK + "\n",
    "https://mooc1.chaoxing.com/mooc-ans/course/1 .html",
    "https://mooc1.chaoxing.com\\@evil.example/",
    "https://evil.example/mooc-ans/course/1.html",
    "https://mooc1.chaoxing.com.evil.example/mooc-ans/course/1.html",
    "",
    "x" * 16385,
    None,
    123,
]
CHECKS = {
    "download": lambda value: _trusted_url(value),
    "visit": lambda value: _trusted_url(value, purpose="visit"),
    "video": lambda value: _trusted_url(value, purpose="video"),
    "reading-request": lambda value: _platform_url(value, path=READ_BOOK_PATH.fullmatch),
    "reading-browser": allow_reading_url,
}


class UrlPolicyTests(unittest.TestCase):
    def test_common_rejections_apply_to_every_caller(self):
        for name, check in CHECKS.items():
            for value in REJECTED_BY_ALL:
                with self.subTest(check=name, value=str(value)[:60]):
                    with self.assertRaises(ValueError):
                        check(value)

    def test_each_caller_keeps_its_own_host_and_path_rules(self):
        cases = [
            ("download", "http://s3.cldisk.com/file/1.pdf", "https://s3.cldisk.com/file/1.pdf"),
            ("download", "https://p.ananas.chaoxing.com/star3/1.png?x=1", "https://p.ananas.chaoxing.com/star3/1.png?x=1"),
            ("visit", "https://fystat-ans.chaoxing.com/log/setlog?x=1", "https://fystat-ans.chaoxing.com/log/setlog?x=1"),
            ("video", "https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/cpi/token",
             "https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/cpi/token"),
            ("reading-request", "http://mooc1-1.chaoxing.com/mooc-ans/zt/22.html?a=1&amp;b=2",
             "https://mooc1-1.chaoxing.com/mooc-ans/zt/22.html?a=1&b=2"),
            ("reading-browser", BOOK, BOOK),
        ]
        for name, value, expected in cases:
            with self.subTest(check=name, value=value):
                self.assertEqual(CHECKS[name](value), expected)
        for name, value in [
            ("visit", "https://fystat-ans.chaoxing.com/log/other"),
            ("video", "https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/cpi/token?x=1"),
            ("download", "https://evilchaoxing.com/a"),
            ("download", "https://moo_c1.chaoxing.com/a"),
            ("reading-request", "https://mooc1.chaoxing.com/mooc-ans/api/work"),
        ]:
            with self.subTest(check=name, value=value):
                with self.assertRaises(ValueError):
                    CHECKS[name](value)

    def test_browser_urls_are_not_unescaped_or_downgraded(self):
        with self.assertRaises(ValueError):
            allow_reading_url("http://mooc1.chaoxing.com/mooc-ans/course/1.html")
        escaped = BOOK + "?a=1&amp;b=2"
        self.assertEqual(allow_reading_url(escaped), escaped)
        self.assertEqual(_platform_url(escaped), BOOK + "?a=1&b=2")

    def test_messages_distinguish_invalid_untrusted_and_path(self):
        self.assertEqual(self.message(lambda: _trusted_url("a\nb")), "上游资源地址无效")
        self.assertEqual(self.message(lambda: _trusted_url("ftp://chaoxing.com/a")), "上游资源地址不受支持")
        self.assertEqual(self.message(lambda: _trusted_url("https://evil.example/a")), "拒绝非受信任的超星资源地址")
        self.assertEqual(self.message(lambda: allow_reading_url("https://evil.example/a")), "拒绝非受信任的专题阅读地址")
        self.assertEqual(self.message(lambda: allow_reading_url("https://mooc1.chaoxing.com/a")), "专题阅读地址路径不受支持")

    def test_relative_links_resolve_against_the_page(self):
        self.assertEqual(_platform_url("../zt/3.html", base=BOOK, path=READ_BOOK_PATH.fullmatch),
                         "https://mooc1.chaoxing.com/mooc-ans/zt/3.html")
        with self.assertRaises(ValueError):
            _platform_url("//evil.example/a", base=BOOK)

    def test_policy_without_a_path_check_accepts_any_trusted_path(self):
        messages = UrlMessages("bad", "untrusted")
        self.assertEqual(canonical_https_url("https://a.example/x?y", messages=messages,
                                             allowed=lambda host, parts: host == "a.example"),
                         "https://a.example/x?y")

    @staticmethod
    def message(call):
        try:
            call()
        except ValueError as exc:
            return str(exc)
        return None


if __name__ == "__main__":
    unittest.main()
