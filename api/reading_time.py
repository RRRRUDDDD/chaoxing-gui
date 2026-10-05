"""Background service for Chaoxing专题阅读时长任务.

The reading attachment is different from video/document resources: the card
only carries a job token, while the actual readable books and current minute
counter are returned by ``api/work``.  This module keeps that protocol behind
the same account-owned session used by :mod:`api.course_tools`.
"""

from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

from loguru import logger

from api.course_tools import CourseTools, _check_html, _scalar, _number
from api.reading_browser import NotReadingPage, TaskPointPage, READING_URL_MESSAGES, scroll_book
from api.url_policy import canonical_https_url


READ_WORK_URL = "https://mooc1.chaoxing.com/mooc-ans/api/work"
# The link emitted by the reading task starts at ``/course/<id>.html`` and
# the platform redirects it to the canonical ``/zt/<id>.html`` page.  Both
# forms carry the same book identity and must be accepted while validating
# every redirect hop.
READ_BOOK_PATH = re.compile(r"/mooc-ans/(?:course|zt)/(\d{1,20})\.html\Z", re.I)
MOOC_HOST = re.compile(r"mooc\d+(?:-\d+|-ans)?\.chaoxing\.com\Z", re.I)
READ_KIND = "read"

_TASK_POINT_HTML = re.compile(
    r"ans-insertvideo-online|ans-insertaudio|insertdoc-online|(?<![\w-])ans-book(?![\w-])|"
    r"module\s*=\s*['\"]insert(?:video|audio|doc|work|live|bbs)['\"]|"
    r"/ananas/modules/(?:video|audio|pdf|ppt|work|live)/|"
    r"/ananas/modules/read/index",
    re.I,
)


def _task_point_html(value):
    """Video, document, work and ordinary read-task pages are not duration pages."""
    return isinstance(value, str) and bool(_TASK_POINT_HTML.search(value))


def _platform_url(value, *, base=None, path=None):
    """Return a canonical HTTPS URL for the small set of reading endpoints."""
    return canonical_https_url(
        value, messages=READING_URL_MESSAGES, base=base, path=path,
        allowed=lambda host, parts: bool(MOOC_HOST.fullmatch(host)),
    )


def _text_number(value):
    match = re.search(r"-?[\d,]+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return _number(match[0].replace(",", ""))
    except (TypeError, ValueError):
        return None


class ReadingTools(CourseTools):
    """Scan and report one ``insertread`` attachment without opening a UI."""

    @staticmethod
    def _is_read_attachment(attachment):
        if not isinstance(attachment, dict):
            return False
        prop = attachment.get("property") or {}
        if not isinstance(prop, dict):
            return False
        kind = _scalar(attachment.get("type")).lower()
        module = _scalar(prop.get("module")).lower()
        # ``read=true`` also appears on ordinary document attachments.  It is
        # not enough to identify the duration task; require the explicit
        # type/module pair emitted by the reading iframe.
        return kind == READ_KIND and module == "insertread"

    def _resource(self, course, chapter, attachment, defaults, page, index):
        if not self._is_read_attachment(attachment):
            return None
        prop = attachment.get("property") or {}
        if not isinstance(prop, dict):
            raise RuntimeError("阅读附件属性格式无效")
        job_id = self._job_id(attachment)
        enc = _scalar(attachment.get("enc")) or _scalar(prop.get("enc"))
        if not job_id or not enc:
            # A text iframe can advertise insertread without being an actual
            # task.  It is not selectable until the platform supplies both
            # pieces of the signed work request.
            return None
        name = next((_scalar(value) for value in (
            prop.get("name"), prop.get("title"), attachment.get("name"),
        ) if _scalar(value)), "课程阅读")[:512]
        # Keep the catalog identity tied to the stable task job.  ``mid`` is a
        # presentation identifier and changes when a teacher re-embeds the
        # same reading task.
        identity = [str(course["courseId"]), str(course["clazzId"]), chapter["id"], job_id]
        return {
            "id": hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest(),
            "course_id": str(course["courseId"]),
            "course_title": _scalar(course.get("title")),
            "chapter_id": chapter["id"], "chapter_title": chapter["title"],
            "name": name, "kind": READ_KIND,
            "downloadable": False, "watchable": False, "readable": False,
            "required_minutes": None, "read_minutes": None, "book_count": 0,
            "_attachment": deepcopy(attachment), "_defaults": deepcopy(defaults),
        }

    @staticmethod
    def public_resource(resource):
        fields = (
            "id", "course_id", "course_title", "chapter_id", "chapter_title",
            "name", "kind", "downloadable", "watchable", "duration", "readable",
            "required_minutes", "read_minutes", "book_count",
        )
        return {key: deepcopy(resource[key]) for key in fields if key in resource}

    def _read_work_params(self, course, resource):
        if str(resource.get("course_id")) != str(course.get("courseId")):
            raise ValueError("阅读任务与所选课程不匹配")
        attachment = resource.get("_attachment")
        defaults = resource.get("_defaults", {})
        if not isinstance(attachment, dict) or not isinstance(defaults, dict):
            raise ValueError("阅读任务缺少新鲜的课程参数，请重新读取")
        prop = attachment.get("property") or {}
        if not isinstance(prop, dict):
            raise ValueError("阅读附件属性格式无效")
        for expected, aliases in (("courseId", ("courseId", "courseid")),
                                  ("clazzId", ("clazzId", "clazzid")),
                                  ("cpi", ("cpi",))):
            value = next((_scalar(defaults.get(key)) for key in aliases
                          if _scalar(defaults.get(key))), "")
            if value and value != str(course.get(expected)):
                raise ValueError("阅读参数与所选课程不匹配")
        job_id = self._job_id(attachment)
        enc = _scalar(attachment.get("enc")) or _scalar(prop.get("enc"))
        if not job_id or not enc:
            raise ValueError("阅读任务缺少有效的上报参数，请重新读取")
        knowledge = _scalar(defaults.get("knowledgeid"))
        if knowledge and knowledge != str(resource.get("chapter_id")):
            raise ValueError("阅读参数与所选章节不匹配")
        params = {
            "api": "1", "workId": "", "jobid": job_id, "needRedirect": "true",
            "type": "read", "knowledgeid": str(resource.get("chapter_id") or ""),
            "ut": "s", "isphone": "false", "clazzId": str(course["clazzId"]),
            "enc": enc, "courseid": str(course["courseId"]),
        }
        if not params["knowledgeid"]:
            raise ValueError("阅读任务缺少章节标识，请重新读取")
        return params

    def _read_page(self, course, resource):
        """Fetch the work page, following only same-host expected redirects."""
        url = READ_WORK_URL
        params = self._read_work_params(course, resource)
        for hop in range(6):
            response = self._request(
                "get", url, "阅读任务", statuses=(200, 301, 302, 303, 307, 308),
                params=params if hop == 0 else None,
                headers={"Referer": "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudy"},
            )
            try:
                if response.status_code == 200:
                    return response.text
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError("阅读任务重定向缺少目标地址")
                # Current deployments use ``preview-show`` for student
                # reading tasks; older ones used ``show``.  Keep the path
                # allow-list explicit so redirects cannot escape to arbitrary
                # same-host endpoints.
                target = _platform_url(
                    location,
                    base=url,
                    path=lambda p: p in {
                        "/mooc-ans/coursedata/job/preview-show",
                        "/mooc-ans/coursedata/job/show",
                    },
                )
                url, params = target, None
            finally:
                response.close()
        raise RuntimeError("阅读任务重定向次数过多")

    @staticmethod
    def _parse_read_page(text):
        if _task_point_html(text):
            raise TaskPointPage("当前页面是视频或其他任务点，不是阅读页")
        soup = _check_html(text, "专题阅读任务")
        tips = soup.select(".readTips span")
        values = [_text_number(node.get_text(" ", strip=True)) for node in tips]
        values = [value for value in values if value is not None]
        if len(values) < 2:
            all_text = soup.get_text(" ", strip=True)
            matches = re.findall(r"(?:阅读总时长|阅读总时长达到)[^\d]{0,40}([\d,]+(?:\.\d+)?)", all_text)
            values = [_text_number(item) for item in matches]
        if len(values) < 2:
            raise RuntimeError("专题阅读页面缺少时长统计")
        books = []
        for node in soup.select(".readList a[href]"):
            url = _platform_url(node.get("href"), base=READ_WORK_URL,
                                path=READ_BOOK_PATH.fullmatch)
            title = node.select_one(".readTitle")
            author = node.select_one(".readAuthor")
            books.append({
                "url": url,
                "title": title.get_text(" ", strip=True) if title else "",
                "author": author.get_text(" ", strip=True) if author else "",
            })
        return {"read_minutes": values[0], "required_minutes": values[1],
                "book_count": len(books), "books": books}

    def _read_metadata(self, course, resource):
        metadata = self._parse_read_page(self._read_page(course, resource))
        resource.update({key: metadata[key] for key in ("read_minutes", "required_minutes", "book_count")})
        resource["readable"] = bool(metadata["books"])
        resource["_books"] = metadata["books"]
        return metadata

    def scan_course(self, course, on_chapter=None):
        resources = super().scan_course(course, on_chapter=on_chapter)
        result = []
        for resource in resources:
            self._check_cancelled()
            try:
                self._read_metadata(course, resource)
            except TaskPointPage:
                logger.info("跳过非阅读页面：{}", resource.get("name") or resource.get("id"))
                continue
            result.append(resource)
        return result

    @staticmethod
    def _book_query(url):
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        result = {}
        for key in ("_from_", "_fromV2_", "rtag"):
            value = query.get(key, [""])[0]
            if not value:
                continue
            if len(value) > 4096 or re.search(r"[\x00-\x20\x7f\\]", value):
                raise ValueError("专题书籍归属参数无效")
            if key == "rtag":
                value = re.sub(r"</?[^>]+>", "", value).strip()
            if value:
                result[key] = value
        return result

    def _book_context(self, book, course=None):
        url = _platform_url(book.get("url"), path=READ_BOOK_PATH.fullmatch)
        initial_path = READ_BOOK_PATH.fullmatch(urlsplit(url).path)
        expected_course = initial_path[1] if initial_path else ""
        initial_query = self._book_query(url)
        # Book links may be redirected by the platform.  Follow only a short
        # chain of HTTPS mooc course pages, validating every destination.
        text = None
        for _ in range(6):
            response = self._request(
                "get", url, "专题书籍", statuses=(200, 301, 302, 303, 307, 308),
                headers={"Referer": READ_WORK_URL},
            )
            try:
                if response.status_code == 200:
                    text = response.text
                    break
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError("专题书籍重定向缺少目标地址")
                url = _platform_url(location, base=url, path=READ_BOOK_PATH.fullmatch)
                redirected_path = READ_BOOK_PATH.fullmatch(urlsplit(url).path)
                if not redirected_path or redirected_path[1] != expected_course:
                    raise ValueError("专题书籍重定向课程标识不匹配")
            finally:
                response.close()
        if text is None:
            raise RuntimeError("专题书籍重定向次数过多")
        _check_html(text, "专题书籍")
        course_match = re.search(r"(?:window\[['\"]courseid['\"]\]|window\.courseid|window\.courseId|var\s+courseid)\s*=\s*['\"]?(\d{1,20})", text)
        if not course_match:
            course_match = READ_BOOK_PATH.fullmatch(urlsplit(url).path)
        if not course_match:
            raise RuntimeError("专题书籍缺少课程标识")
        course_id = course_match[1]
        path_match = READ_BOOK_PATH.fullmatch(urlsplit(url).path)
        if not path_match or course_id != path_match[1]:
            raise ValueError("专题书籍课程标识不匹配")
        # Attribution belongs to the original link.  A redirect must not be
        # able to replace it with a different course/account marker.
        query = dict(initial_query)
        for key, value in self._book_query(url).items():
            query.setdefault(key, value)
        if course is not None:
            origin = query.get("_from_", "")
            attribution = origin.split("_") if origin else []
            if origin and (len(attribution) < 2 or
                    attribution[0] != str(course.get("courseId")) or
                    attribution[1] != str(course.get("clazzId"))):
                raise ValueError("专题书籍归属课程不匹配")
        return url

    def watch_reading(self, course, resource, seconds, on_progress=None):
        self._check_cancelled()
        requested = _number(seconds)
        if not 0 < requested <= 86400:
            raise ValueError("阅读时长必须大于零且不超过 24 小时")
        metadata = self._read_metadata(course, resource) if not resource.get("_books") else {
            "read_minutes": resource.get("read_minutes"),
            "required_minutes": resource.get("required_minutes"),
            "book_count": resource.get("book_count", 0),
            "books": resource.get("_books", []),
        }
        if not metadata["books"]:
            raise ValueError("专题阅读没有可读书籍")
        last_error = None
        for book in metadata["books"]:
            self._check_cancelled()
            url = self._book_context(book, course=course)
            try:
                completed = scroll_book(
                    url, self.chaoxing.session_manager.get_session().cookies,
                    requested, on_progress=on_progress, wait=self._wait,
                    check=self._check_cancelled,
                )
                break
            except NotReadingPage as exc:
                # Only unreadable content permits another book. Network,
                # cancellation and post-progress failures must not be replayed.
                last_error = exc
        else:
            if isinstance(last_error, TaskPointPage):
                raise last_error
            raise RuntimeError("没有可读取的专题书籍") from last_error

        # Refreshing the page is part of the authenticated protocol and also
        # gives the caller the platform's end counter.  Invalid, empty or
        # rejected responses are surfaced instead of being mistaken for a
        # successful report; the UI explains that the counter itself may lag
        # until the next day.
        refreshed = self._read_metadata(course, resource)
        return {
            "seconds": completed,
            "before": metadata.get("read_minutes"),
            "after": refreshed.get("read_minutes"),
        }
