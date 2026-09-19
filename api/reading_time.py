"""Background service for Chaoxing专题阅读时长任务.

The reading attachment is different from video/document resources: the card
only carries a job token, while the actual readable books and current minute
counter are returned by ``api/work``.  This module keeps that protocol behind
the same account-owned session used by :mod:`api.course_tools`.
"""

from copy import deepcopy
import hashlib
import html
import json
import re
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from api.course_tools import CourseTools, _check_html, _scalar, _number


READ_WORK_URL = "https://mooc1.chaoxing.com/mooc-ans/api/work"
READ_LOG_URL = "https://mooc1.chaoxing.com/multimedia/readlog"
READ_WORK_PATH = "/mooc-ans/api/work"
READ_LOG_PATH = "/multimedia/readlog"
# Captured from the real book page: courseMainBox is 470px, and logs.js only
# reports after scrollTop changes. The probe wheel sequence was +380,+380,-280.
READ_HEIGHT = "470"
READ_SCROLL_STEPS = (380, 380, -280)
READ_CARDS_PATH = "/mooc-ans/zt/getcards"
# Compatibility aliases make the protocol names easy to discover for callers
# and keep tests independent from the implementation's naming style.
READLOG_URL = READ_LOG_URL
READ_CARDS_URL = "https://mooc1.chaoxing.com" + READ_CARDS_PATH
# The link emitted by the reading task starts at ``/course/<id>.html`` and
# the platform redirects it to the canonical ``/zt/<id>.html`` page.  Both
# forms carry the same book identity and must be accepted while validating
# every redirect hop.
READ_BOOK_PATH = re.compile(r"/mooc-ans/(?:course|zt)/(\d{1,20})\.html\Z", re.I)
MOOC_HOST = re.compile(r"mooc\d+(?:-\d+|-ans)?\.chaoxing\.com\Z", re.I)
READ_KIND = "read"


def _platform_url(value, *, base=None, path=None):
    """Return a canonical HTTPS URL for the small set of reading endpoints."""
    if not isinstance(value, str) or not value or len(value) > 16384:
        raise ValueError("阅读资源地址无效")
    value = html.unescape(value)
    if re.search(r"[\x00-\x20\x7f\\]", value):
        raise ValueError("阅读资源地址无效")
    value = urljoin(base, value) if base else value
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("阅读资源地址无效") from None
    host = (parts.hostname or "").lower()
    if (parts.scheme not in {"http", "https"} or not MOOC_HOST.fullmatch(host)
            or parts.username is not None or parts.password is not None
            or port not in {None, 443} or parts.fragment):
        raise ValueError("拒绝非受信任的专题阅读地址")
    if path is not None and not path(parts.path):
        raise ValueError("专题阅读地址路径不受支持")
    return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))


def _scroll_after(position, index):
    """Return the next scroll offset used by the real reading page."""
    delta = READ_SCROLL_STEPS[index % len(READ_SCROLL_STEPS)]
    nxt = position + delta
    if nxt < 0:
        nxt = position + READ_SCROLL_STEPS[0]
    return nxt


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
            self._read_metadata(course, resource)
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

    def _book_context(self, book, course=None, chapter=None):
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
        soup = _check_html(text, "专题书籍")
        course_match = re.search(r"(?:window\[['\"]courseid['\"]\]|window\.courseid|window\.courseId|var\s+courseid)\s*=\s*['\"]?(\d{1,20})", text)
        if not course_match:
            course_match = re.search(r"/mooc-ans/course/(\d{1,20})\.html", url)
        chapter_match = re.search(r"\b(?:ctid|courseChapterId|initKid)\s*=\s*['\"]?(\d{1,20})", text)
        if not chapter_match:
            node = soup.select_one("#nodeIdInput[value], #chapterId[value], [id^='zt_']")
            if node and node.get("value"):
                chapter_match = re.fullmatch(r"(\d{1,20})", node["value"])
            elif node:
                chapter_match = re.search(r"zt_(\d{1,20})", node.get("id", ""))
        if not course_match or not chapter_match:
            raise RuntimeError("专题书籍缺少课程或章节内容")
        course_id, chapter_id = course_match[1], chapter_match[1]
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
        cards_url = _platform_url(urlunsplit((urlsplit(url).scheme, urlsplit(url).netloc, READ_CARDS_PATH, "", "")),
                                  path=lambda p: p == READ_CARDS_PATH)
        cards = None
        for _ in range(6):
            response = self._request(
                "get", cards_url, "专题书籍章节", statuses=(200, 301, 302, 303, 307, 308),
                params={"knowledgeid": chapter_id, "courseid": course_id, **query},
                headers={"Referer": url},
            )
            try:
                if response.status_code == 200:
                    cards = response.text
                    break
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError("专题书籍章节重定向缺少目标地址")
                cards_url = _platform_url(location, base=cards_url,
                                          path=lambda p: p == READ_CARDS_PATH)
            finally:
                response.close()
        if cards is None:
            raise RuntimeError("专题书籍章节重定向次数过多")
        cards_soup = _check_html(cards, "专题书籍章节")
        # ``zt/getcards`` is normally a full page.  Keep the marker check for
        # the real response, while accepting small HTML fixtures and rejecting
        # the JSON ``data: []`` response used for an unavailable chapter.
        stripped = cards.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                payload = json.loads(stripped)
            except (TypeError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get("data") in (None, [], ""):
                raise RuntimeError("专题书籍没有可读内容")
        if (not cards_soup.select_one("#pageDiv") and not cards_soup.select_one(".ans-cc")
                and not cards_soup.get_text(" ", strip=True)):
            raise RuntimeError("专题书籍没有可读内容")
        return {"url": url, "courseid": course_id, "chapterid": chapter_id,
                "query": query, "height": READ_HEIGHT}

    def _readlog_url(self, context):
        return _platform_url(READ_LOG_PATH, base=context["url"],
                             path=lambda p: p == READ_LOG_PATH)

    def _readlog(self, context, h):
        try:
            scroll = int(h)
        except (TypeError, ValueError):
            raise ValueError("阅读滚动位置无效") from None
        if scroll < 0 or scroll > 10_000_000:
            raise ValueError("阅读滚动位置无效")
        params = {"courseid": context["courseid"], "chapterid": context["chapterid"],
                  "height": str(context["height"]), **context["query"], "h": str(scroll)}
        response_text = self._text("get", self._readlog_url(context), "阅读时长上报", no_retry=True,
                                   params=params, headers={"Referer": context["url"]})
        try:
            payload = json.loads(response_text)
        except (TypeError, ValueError):
            raise RuntimeError("阅读时长上报返回了无效响应") from None
        if payload != {}:
            raise RuntimeError("阅读时长上报未确认成功")

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
        context = None
        last_error = None
        for book in metadata["books"]:
            try:
                context = self._book_context(book, course=course)
                break
            except RuntimeError as exc:
                last_error = exc
        if context is None:
            raise RuntimeError("没有可读取的专题书籍") from last_error

        target = requested
        completed = 0.0
        if callable(on_progress):
            on_progress(0, target)
        # logs.js sends h=0 once to open the session, then only reports again
        # when scrollTop changes. Repeating h=0 is acknowledged as {} but is
        # not counted as reading time by the platform.
        position = 0
        step = 0
        self._readlog(context, position)
        while completed < target:
            self._check_cancelled()
            interval = min(5.0, target - completed)
            self._wait(interval)
            position = _scroll_after(position, step)
            step += 1
            self._readlog(context, position)
            completed += interval
            if callable(on_progress):
                on_progress(completed, target)
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
