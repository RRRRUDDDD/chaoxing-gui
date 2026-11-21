# SPDX-License-Identifier: GPL-3.0-only
"""Account-scoped course utilities, independent of automatic task completion.

The endpoint, card and video-signing conventions are adapted from liuyunfz's
chaoxing_tool (https://github.com/liuyunfz/chaoxing_tool), distributed under the
GNU GPL version 3. See its LICENSE and this project's LICENSE. No upstream CLI,
global account state, or runtime dependency on that checkout is used here.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import errno
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import unicodedata
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from loguru import logger
import requests
from urllib3.util.retry import Retry

from api.captcha import is_captcha_response
from api.decode import card_page_has_payload, is_live_card
from api.fs_policy import reject_links
from api.url_policy import UrlMessages, canonical_https_url
from api.session import HTTP_TIMEOUT


COURSE_URL = "https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse"
STUDY_URL = "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax"
CARDS_URL = "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"
STATUS_URL = "https://mooc1.chaoxing.com/ananas/status/"
VISITS_URL = "https://stat2-ans.chaoxing.com/stat2/study-pv/chart"
STAT_INDEX_URL = "https://stat2-ans.chaoxing.com/study-data/index"
STAT_TIME_URL = "https://stat2-ans.chaoxing.com/stat2/task/s/index"
VIDEO_REFERER = "https://mooc1.chaoxing.com/ananas/modules/video/index.html"
READ_POINT_URL = "https://data-xxt.aichaoxing.com/analysis/ac_mark"
READ_POINT_SALT = "NrRzLDpWB2JkeodIVAn4"
READ_HEARTBEAT_SECONDS = 30
MAX_CHAPTER_DOCS = 10
PUBLIC_FIELDS = (
    "id", "course_id", "course_title", "chapter_id", "chapter_title",
    "name", "kind", "downloadable", "watchable", "readable", "duration",
)
_LOCKED = re.compile(r"章节未开放|章节已锁定|该章节尚未开放|请先完成.{0,80}解锁")
# An empty knowledge card page only initializes ``mArg = ""``. Without a card
# count from the study page, that placeholder marks the end of the chapter.
_CARD_PLACEHOLDER = re.compile(r"""\bmArg\s*=\s*(['"])\1""")
MAX_PROBED_CARD_PAGES = 30
# 同一章节的卡片页逐页拉取时的轻量间隔，降低触发风控/验证码的频率。
CARD_PAGE_THROTTLE_SECONDS = 0.25
_REDIRECTS = {301, 302, 303, 307, 308}
_SUCCESS = {"true", "1", "200", "success", "ok"}


class ToolCancelled(Exception):
    """The owning task requested cooperative cancellation."""


class _NetworkRequestError(RuntimeError):
    """传输层请求失败（连接中断/读写超时），请求可能未到达平台。

    与"平台明确拒绝"（HTTP 状态码、业务校验失败）区分：前者对幂等调用
    （如绑定绝对位置的心跳）可以安全重试，后者重试没有意义。
    """


def _scalar(value):
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value).strip()
    return ""


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("数值格式无效")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("数值范围无效")
    return number


def _seconds(milliseconds):
    return milliseconds // 1000 if milliseconds % 1000 == 0 else milliseconds / 1000


_TRUSTED_MESSAGES = UrlMessages(invalid="上游资源地址无效", untrusted="上游资源地址不受支持")
_MOOC_HOST = re.compile(r"mooc\d+(?:-\d+|-ans)?\.chaoxing\.com")
_VIDEO_LOG_PATH = re.compile(r"/(?:mooc-ans/)?multimedia/log/[a-z]/[^?#]+")
_READ_LOG_PATH = re.compile(r"/(?:mooc-ans/)?multimedia/readlog")
_STUDENT_STUDY_PATH = re.compile(r"/(?:mooc-ans/)?mycourse/studentstudy")
_NUMERIC_ID = re.compile(r"\d{1,20}")
_TOKEN_VALUE = re.compile(r"[a-zA-Z0-9_-]{8,128}")


def _trusted_url(value, *, purpose="download", base=None):
    """Validate before issuing a request, including each redirect destination."""
    def allowed(host, parts):
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            return False
        if purpose == "visit":
            trusted = host == "fystat-ans.chaoxing.com" and parts.path == "/log/setlog"
        elif purpose == "video":
            trusted = bool(_MOOC_HOST.fullmatch(host) and _VIDEO_LOG_PATH.fullmatch(parts.path)
                           and not parts.query)
        elif purpose == "read_point":
            trusted = host == "data-xxt.aichaoxing.com" and parts.path == "/analysis/ac_mark"
        elif purpose == "read_log":
            trusted = bool(_MOOC_HOST.fullmatch(host) and _READ_LOG_PATH.fullmatch(parts.path))
        elif purpose == "studentstudy":
            trusted = bool(_MOOC_HOST.fullmatch(host) and _STUDENT_STUDY_PATH.fullmatch(parts.path))
        elif purpose == "read_page":
            trusted = host == "chaoxing.com" or host.endswith(".chaoxing.com")
        else:
            # Authenticated media metadata also uses the cldisk CDN for documents.
            trusted = host in {"chaoxing.com", "cldisk.com"} or host.endswith((".chaoxing.com", ".cldisk.com"))
        if not trusted:
            raise ValueError("拒绝非受信任的超星资源地址")
        return True

    # Some status endpoints still label CDN links http; the result is HTTPS.
    return canonical_https_url(value, messages=_TRUSTED_MESSAGES, allowed=allowed, base=base)


def parse_studentstudy_url(value):
    """Validate and parse a Chaoxing studentstudy chapter URL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("章节链接不能为空")
    try:
        canonical = _trusted_url(value.strip(), purpose="studentstudy")
    except ValueError:
        raise ValueError("请输入有效的超星学习章节链接（studentstudy）") from None
    parts = urlsplit(canonical)
    params = {}
    for key, item in parse_qsl(parts.query, keep_blank_values=False):
        lower = key.lower()
        if lower not in params:
            params[lower] = item.strip()
    course_id = params.get("courseid", "")
    clazz_id = params.get("clazzid", "")
    chapter_id = params.get("chapterid", "")
    if not (_NUMERIC_ID.fullmatch(course_id) and _NUMERIC_ID.fullmatch(clazz_id) and _NUMERIC_ID.fullmatch(chapter_id)):
        raise ValueError("章节链接缺少有效的 courseId、clazzid 或 chapterId 参数")
    cpi = params.get("cpi", "")
    if cpi and not _NUMERIC_ID.fullmatch(cpi):
        raise ValueError("章节链接中的 cpi 参数格式无效")
    enc = params.get("enc", "")
    if enc and not _TOKEN_VALUE.fullmatch(enc):
        raise ValueError("章节链接中的 enc 参数格式无效")
    safe_query = [("chapterId", chapter_id), ("courseId", course_id), ("clazzid", clazz_id)]
    if cpi:
        safe_query.append(("cpi", cpi))
    sanitized = urlunsplit(("https", parts.netloc, parts.path, urlencode(safe_query), ""))
    return {
        "url": canonical,
        "sanitized_url": sanitized,
        "host": (parts.hostname or "mooc1.chaoxing.com").lower(),
        "courseId": course_id,
        "clazzId": clazz_id,
        "chapterId": chapter_id,
        "cpi": cpi,
        "enc": enc,
    }


def _read_point_timestamp(now=None):
    current = datetime.now() if now is None else now
    return current.strftime("%Y%m%d%H%M%S") + f"{current.microsecond // 1000:03d}"


def sign_read_point(payload, uid, timestamp, *, function="readPoint", pid=None, session_id=None):
    """Build and sign the query string for data-xxt.aichaoxing.com/analysis/ac_mark."""
    d_json = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    # Match JavaScript encodeURIComponent() exactly so the MD5 digest agrees with addPoint.js.
    params = {"f": str(function), "u": str(uid)}
    if pid is not None:
        params["pid"] = str(pid)
    if session_id is not None:
        params["s"] = str(session_id)
    params["d"] = quote(d_json, safe="-_.!~*'()")
    params["t"] = str(timestamp)
    raw = "".join(params[key] for key in sorted(params)) + READ_POINT_SALT
    enc = hashlib.md5(raw.encode("utf-8")).hexdigest()
    query = "".join(f"&{key}={value}" for key, value in params.items()) + f"&enc={enc}"
    return query, enc


def _check_business(data, label):
    if not isinstance(data, dict):
        raise RuntimeError(f"{label}返回的数据格式无效")
    for key in ("status", "success"):
        if key in data and str(data[key]).lower() not in _SUCCESS:
            raise RuntimeError(f"{label}被平台拒绝")
    if data.get("error") or data.get("errors"):
        raise RuntimeError(f"{label}被平台拒绝")
    if "code" in data and data["code"] not in (0, 200, "0", "200", None):
        raise RuntimeError(f"{label}被平台拒绝")


def _check_html(text, label):
    if not text.strip():
        raise RuntimeError(f"{label}返回了空页面")
    soup = BeautifulSoup(text, "html.parser")
    if (soup.select_one('input[type="password"]')
            or soup.select_one('form[action*="login"], form[action*="fanyalogin"]')
            or re.search(
                r"(?:location(?:\.href)?\s*=|http-equiv\s*=\s*['\"]?refresh)"
                r".{0,300}passport2\.chaoxing\.com", text, re.I | re.S)):
        raise RuntimeError(f"{label}需要重新登录")
    return soup


class CourseTools:
    def __init__(self, chaoxing, cancel_check=None, *, read_only=False):
        self.chaoxing = chaoxing
        self.cancel_check = cancel_check
        self.read_only = read_only

    def _check_cancelled(self):
        if callable(self.cancel_check) and self.cancel_check():
            raise ToolCancelled("任务已停止")

    def _wait(self, seconds):
        deadline = time.monotonic() + seconds
        while True:
            self._check_cancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))

    @contextmanager
    def _without_retries(self, session, url, enabled):
        if not enabled:
            yield
            return
        # This Session and its adapters belong to the current worker thread.
        # Although the platform uses GET, reports must never be auto-replayed.
        adapter = session.get_adapter(url)
        previous = adapter.max_retries
        adapter.max_retries = Retry(total=0, connect=0, read=0, status=0, redirect=0)
        try:
            yield
        finally:
            adapter.max_retries = previous

    def _request(self, method, url, label, *, no_retry=False, statuses=(200,), **kwargs):
        self._check_cancelled()
        if self.read_only and (method != "get" or url not in {COURSE_URL, STUDY_URL, CARDS_URL}):
            raise RuntimeError("复核只允许读取课程和任务卡片")
        if self.read_only:
            self.chaoxing.rate_limiter.limit_rate()
            self._check_cancelled()
        session = self.chaoxing.session_manager.get_session()
        response = None
        try:
            with self._without_retries(session, url, no_retry):
                response = getattr(session, method)(
                    url, timeout=HTTP_TIMEOUT, allow_redirects=False, **kwargs,
                )
            if is_captcha_response(response, inspect_body=not kwargs.get("stream", False)):
                if self.read_only:
                    raise RuntimeError(f"{label}需要验证码，请手动验证后复核")
                response.close()
                response = None
                # A verification page means the request was refused, so
                # sending it once more after the pass cannot double-count.
                if not self.chaoxing.solve_captcha(self.cancel_check):
                    self._check_cancelled()
                    raise RuntimeError(f"{label}需要验证码，自动识别未通过，请在浏览器中手动完成验证")
                self._check_cancelled()
                with self._without_retries(session, url, no_retry):
                    response = getattr(session, method)(
                        url, timeout=HTTP_TIMEOUT, allow_redirects=False, **kwargs,
                    )
                if is_captcha_response(response, inspect_body=not kwargs.get("stream", False)):
                    raise RuntimeError(f"{label}需要验证码，自动识别未通过，请在浏览器中手动完成验证")
            if response.status_code not in statuses:
                if response.status_code in {401, 403}:
                    raise RuntimeError(f"{label}访问受限或登录已失效（HTTP {response.status_code}）")
                raise RuntimeError(f"{label}请求失败（HTTP {response.status_code}）")
            return response
        except requests.RequestException as exc:
            if response is not None:
                response.close()
            raise _NetworkRequestError(f"{label}网络请求失败（{type(exc).__name__}）") from None
        except BaseException:
            if response is not None:
                response.close()
            raise

    def _text(self, method, url, label, **kwargs):
        response = self._request(method, url, label, **kwargs)
        try:
            return response.text
        finally:
            response.close()

    def _json(self, method, url, label, **kwargs):
        text = self._text(method, url, label, **kwargs)
        try:
            result = json.loads(text)
        except (ValueError, TypeError):
            if "<" in text:
                _check_html(text, label)
            raise RuntimeError(f"{label}返回的数据格式无效，请检查登录状态") from None
        _check_business(result, label)
        return result

    @staticmethod
    def _course_params(course):
        values = {key: _scalar(course.get(key)) for key in ("courseId", "clazzId", "cpi")}
        if not all(values.values()):
            raise ValueError("缺少课程、班级或账号选课参数")
        return {
            "courseid": values["courseId"], "clazzid": values["clazzId"],
            "cpi": values["cpi"], "ut": "s",
        }

    def _course_page(self, course):
        params = self._course_params(course)
        params["t"] = int(time.time() * 1000)
        return self._text("get", COURSE_URL, "课程章节", params=params)

    @staticmethod
    def _chapters(text):
        soup = _check_html(text, "课程章节")
        root = soup.select_one(".fanyaChapterWhite") or soup
        units = root.select(".chapter_unit")
        if not units:
            if soup.select_one(".fanyaChapterWhite") or re.search(r"暂无章节|没有章节", soup.get_text()):
                return []
            raise RuntimeError("无法识别课程章节页面，请检查登录或课程权限")
        chapters, seen = [], set()
        for unit in units:
            for entry in unit.find_all("li"):
                point = entry.find("div", recursive=False)
                if point is None:
                    raise RuntimeError("无法识别章节条目")
                title_node = point.select_one("a.clicktitle")
                if title_node is None:
                    if point.get("id", "").startswith("cur") or point.get("onclick"):
                        raise RuntimeError("章节标题缺失")
                    continue
                title = title_node.get_text(" ", strip=True)
                tips = " ".join(node.get_text(" ", strip=True) for node in point.select(".bntHoverTips"))
                locked = bool(_LOCKED.search(tips) or ("解锁" in tips and "已解锁" not in tips))
                match = re.fullmatch(r"cur(\d{1,20})", point.get("id", ""))
                identifier = match[1] if match else ""
                if not identifier:
                    args = re.findall(r"""['"]([^'"]*)['"]""", point.get("onclick", ""))
                    identifier = args[1] if len(args) > 1 else ""
                if not identifier:
                    if not locked:
                        raise RuntimeError(f"章节“{title or '未命名章节'}”的标识无法解析")
                    identifier = f"locked-{len(chapters)}"
                if identifier in seen:
                    continue
                seen.add(identifier)
                chapters.append({"id": identifier, "title": title or identifier, "locked": locked})
        if not chapters and any(unit.find("li") for unit in units):
            raise RuntimeError("无法解析课程章节列表")
        return chapters

    @staticmethod
    def _card_data(soup):
        text = " ".join(script.get_text() for script in soup.find_all("script"))
        # The platform initializes mArg to "" before assigning the resource
        # object inside try/catch. Match that object, not the placeholder.
        match = re.search(r"\bmArg\s*=\s*(?=\{)", text)
        if not match:
            raise RuntimeError("章节卡片缺少资源数据")
        try:
            data, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
        except ValueError:
            raise RuntimeError("章节卡片资源数据无法解析") from None
        if (not isinstance(data, dict) or not isinstance(data.get("defaults", {}), dict)
                or not isinstance(data.get("attachments", []), list)):
            raise RuntimeError("章节卡片资源结构无效")
        if data and not ({"defaults", "attachments"} & data.keys()):
            raise RuntimeError("无法识别章节卡片资源结构")
        _check_business(data, "章节卡片")
        if any(not isinstance(item, dict) for item in data.get("attachments", [])):
            raise RuntimeError("章节卡片附件结构无效")
        return data

    @staticmethod
    def _object_id(attachment):
        prop = attachment.get("property") or {}
        return next((
            value for value in (
                _scalar(attachment.get("objectId")), _scalar(attachment.get("objectid")),
                _scalar(prop.get("objectid")), _scalar(prop.get("objectId")),
            ) if value
        ), "")

    @staticmethod
    def _job_id(attachment):
        prop = attachment.get("property") or {}
        return _scalar(attachment.get("jobid")) or _scalar(prop.get("_jobid")) or _scalar(prop.get("jobid"))

    @staticmethod
    def _course_enc(text):
        soup = BeautifulSoup(text, "html.parser")
        for selector in ("input#enc", "input[name='enc']", "input#chapterEnc", "input[name='chapterEnc']"):
            node = soup.select_one(selector)
            if node is not None:
                value = _scalar(node.get("value"))
                if _TOKEN_VALUE.fullmatch(value):
                    return value
        for pattern in (
            r"""[?&]enc=([a-zA-Z0-9_-]{16,128})\b""",
            r"""\b(?:chapterEnc|enc)\s*[:=]\s*['"]([a-zA-Z0-9_-]{16,128})['"]""",
        ):
            match = re.search(pattern, text)
            if match:
                return match[1]
        return ""

    def _resource(self, course, chapter, attachment, defaults, page, index, *, course_enc=""):
        prop = attachment.get("property", {})
        if not isinstance(prop, dict):
            raise RuntimeError("章节附件属性格式无效")
        kind = _scalar(attachment.get("type")).lower()
        module = _scalar(prop.get("module")).lower()
        if is_live_card(attachment):
            return None
        object_id = self._object_id(attachment)
        job_id = self._job_id(attachment)
        if kind == "audio" or module == "insertaudio":
            kind = "audio"
        elif kind == "video" or module == "insertvideo":
            kind = "video"
        elif kind == "read" or module == "insertread" or job_id.startswith("read-"):
            kind = "read"
        elif kind in {"document", "doc", "pdf"} or module == "insertdoc":
            kind = "document"
        elif object_id or kind in {"file", "attachment", "book"}:
            kind = "file"
        else:
            return None
        name = next((
            _scalar(value) for value in (
                prop.get("name"), prop.get("title"), prop.get("bookname"), attachment.get("name"),
            ) if _scalar(value)
        ), object_id or job_id or "未命名资源")[:512]
        identity = object_id or job_id or _scalar(attachment.get("id")) or f"{page}:{index}"
        key = [str(course["courseId"]), str(course["clazzId"]), chapter["id"], kind, identity]
        resource = {
            "id": hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest(),
            "course_id": str(course["courseId"]), "course_title": _scalar(course.get("title")),
            "chapter_id": chapter["id"], "chapter_title": chapter["title"],
            "name": name, "kind": kind, "downloadable": bool(object_id),
            "watchable": bool(kind == "video" and object_id and job_id),
            "readable": bool((kind == "read" and job_id) or (kind == "document" and object_id)),
            "_attachment": deepcopy(attachment),
            # 本资源接管该对象：scan_course 传入的是整页共享的一份副本，
            # 之后任何代码都不得原地修改 _defaults。
            "_defaults": defaults,
        }
        if course_enc:
            resource["_course_enc"] = course_enc
        try:
            duration = _number(attachment.get("duration", prop.get("duration")))
            if duration > 0:
                resource["duration"] = duration
        except (TypeError, ValueError):
            pass
        return resource

    def iter_card_pages(self, course, chapter, *, strict=False, host=None):
        """Yield raw card data; strict readers require proof of the page boundary."""
        self._check_cancelled()
        if chapter["locked"]:
            if strict:
                raise RuntimeError("章节已锁定，无法确认任务状态")
            logger.warning("章节“{}”已锁定，已跳过", chapter["title"])
            return
        study_url = STUDY_URL if host is None else _trusted_url(
            f"https://{host}/mooc-ans/mycourse/studentstudyAjax", purpose="read_page",
        )
        cards_url = CARDS_URL if host is None else _trusted_url(
            f"https://{host}/mooc-ans/knowledge/cards", purpose="read_page",
        )
        params = {
            "courseId": str(course["courseId"]), "clazzid": str(course["clazzId"]),
            "chapterId": chapter["id"], "cpi": str(course["cpi"]),
            "verificationcode": "", "mooc2": 1,
        }
        text = self._text("get", study_url, "章节页数", params=params)
        soup = _check_html(text, "章节页数")
        count_node = soup.select_one("#cardcount")
        if count_node is None and _LOCKED.search(soup.get_text()):
            if strict:
                raise RuntimeError("章节未开放，无法确认任务状态")
            logger.warning("章节“{}”未开放，已跳过", chapter["title"])
            return
        probing = count_node is None
        raw_count = "" if probing else count_node.get("value", "")
        if not probing and (not re.fullmatch(r"\d{1,5}", raw_count) or int(raw_count) > 10000):
            raise RuntimeError(f"章节“{chapter['title']}”的实际页数无法读取")
        for page in range(MAX_PROBED_CARD_PAGES if probing else int(raw_count)):
            if page:
                self._wait(CARD_PAGE_THROTTLE_SECONDS)
            self._check_cancelled()
            params = {**self._course_params(course), "knowledgeid": chapter["id"],
                      "num": page, "v": "20160407-1", "mooc2": 1}
            text = self._text("get", cards_url, "章节卡片", params=params)
            soup = _check_html(text, "章节卡片")
            if _LOCKED.search(soup.get_text()):
                if strict:
                    raise RuntimeError("章节未开放，无法确认任务状态")
                logger.warning("章节“{}”未开放，已跳过剩余卡片", chapter["title"])
                return
            if probing and _CARD_PLACEHOLDER.search(text) and not card_page_has_payload(text):
                return
            try:
                data = self._card_data(soup)
            except RuntimeError as exc:
                raise RuntimeError(f"章节“{chapter['title']}”第 {page + 1} 页：{exc}") from None
            yield page, data
        if strict and probing:
            raise RuntimeError("已达到卡片探测上限，无法确认最后一页")

    def scan_course(self, course, on_chapter=None):
        self._check_cancelled()
        course_html = self._course_page(course)
        chapters = self._chapters(course_html)
        course_enc = self._course_enc(course_html)
        resources = {}
        for completed, chapter in enumerate(chapters, 1):
            for page, data in self.iter_card_pages(course, chapter):
                # 每页只做一份 defaults 私有副本，页内全部附件共享（_resource 接管该对象）。
                defaults = deepcopy(data.get("defaults", {}))
                for index, attachment in enumerate(data.get("attachments", [])):
                    self._check_cancelled()
                    resource = self._resource(
                        course, chapter, attachment, defaults, page, index, course_enc=course_enc,
                    )
                    if resource is not None:
                        previous = resources.get(resource["id"])
                        if (previous is None
                                or (resource["watchable"] and not previous["watchable"])
                                or (resource["readable"] and not previous["readable"])):
                            resources[resource["id"]] = resource
            if callable(on_chapter):
                on_chapter(chapter["title"], completed, len(chapters))
        self._check_cancelled()
        return list(resources.values())

    @staticmethod
    def public_resource(resource):
        return {key: deepcopy(resource[key]) for key in PUBLIC_FIELDS if key in resource}

    def _visit_total(self, course):
        params = self._course_params(course)
        now = datetime.now()
        params.update(year=now.year, month=f"{now.month:02d}")
        data = self._json("post", VISITS_URL, "学习次数统计", data=params)
        value = data.get("total")
        if value is None and isinstance(data.get("data"), dict):
            value = data["data"].get("total")
        total = _number(value)
        if not total.is_integer():
            raise ValueError("学习次数统计格式无效")
        return int(total)

    def get_statistics(self, course):
        """Visits and watched minutes for one course.

        Not wired to any task or route yet; kept for a planned statistics view
        and covered by tests.
        """
        self._check_cancelled()
        params = self._course_params(course)
        result = {"visits": None, "watched_minutes": None, "total_minutes": None, "warnings": []}
        try:
            result["visits"] = self._visit_total(course)
        except (RuntimeError, ValueError):
            result["warnings"].append("学习次数统计暂不可用")
        self._check_cancelled()
        try:
            text = self._text(
                "get", STAT_INDEX_URL, "学习统计签名",
                params={**params, "t": int(time.time() * 1000)},
            )
            soup = _check_html(text, "学习统计")
            match = re.search(r"""\bjobEnc\s*=\s*(['"])([^'"]+)\1""", text)
            node = soup.select_one("input#jobEnc")
            enc = match[2] if match else node.get("value") if node else None
            if not enc:
                raise RuntimeError("学习统计签名缺失")
            text = self._text(
                "get", STAT_TIME_URL, "视频时长统计", params={**params, "pEnc": enc},
            )
            soup = _check_html(text, "视频时长统计")
            watched = soup.select_one(".fl.min span")
            total = re.search(r"总时长\s*[:：]?\s*([\d,]+(?:\.\d+)?)", soup.get_text(" ", strip=True))
            if watched is not None:
                number = re.search(r"-?[\d,]+(?:\.\d+)?", watched.get_text())
                if number:
                    result["watched_minutes"] = _number(number[0].replace(",", ""))
            if total:
                result["total_minutes"] = _number(total[1].replace(",", ""))
            if result["watched_minutes"] is None or result["total_minutes"] is None:
                result["warnings"].append("部分视频时长统计暂不可用")
        except (RuntimeError, ValueError):
            result["warnings"].append("视频时长统计暂不可用")
        self._check_cancelled()
        return result

    @staticmethod
    def _visit_url(text):
        soup = _check_html(text, "学习次数上报地址")
        for script in soup.select("script[src]"):
            source = html.unescape(script["src"])
            if "/log/setlog" in source:
                return _trusted_url(source, purpose="visit", base=COURSE_URL)
        raise RuntimeError("课程页面缺少学习次数上报地址")

    @staticmethod
    def _check_report(text, *, video=False):
        label = "视频时长上报" if video else "学习次数上报"
        text = text.strip()
        # setlog is loaded as a script and returns the JavaScript literal
        # 'success', which is not JSON. Accept only explicit acknowledgements.
        if not video and (not text or text.lower() in {"ok", "success", "true", "1"}
                          or re.fullmatch(r"(['\"])(?:ok|success|true|1)\1", text, re.I)):
            return
        try:
            data = json.loads(text)
        except ValueError:
            raise RuntimeError(f"{label}返回了无效响应，请检查登录状态") from None
        _check_business(data, label)
        if video and "isPassed" in data:
            # Passed is completion evidence, not an instruction to stop adding
            # requested watch time to an already completed video.
            if str(data["isPassed"]).lower() in {"true", "false", "0", "1"}:
                return
            raise RuntimeError(f"{label}返回了无效的完成状态")
        if ("status" in data or "success" in data
                or (type(data.get("code")) in (str, int) and data["code"] in (0, 200, "0", "200"))):
            return
        raise RuntimeError(f"{label}未确认成功")

    def add_visits(self, course, count, interval, on_progress=None):
        self._check_cancelled()
        if type(count) is not int or not 1 <= count <= 1000:
            raise ValueError("学习次数必须是 1 到 1000 的整数")
        interval = _number(interval)
        if not 1 <= interval <= 3600:
            raise ValueError("学习次数间隔必须是 1 到 3600 秒")
        warnings = []

        def read_total():
            try:
                return self._visit_total(course)
            except (RuntimeError, ValueError):
                warnings.append("学习次数统计暂不可用，成功请求数不代表平台实际增加次数")
                return None

        before = read_total()
        url = self._visit_url(self._course_page(course))
        submitted = 0
        for index in range(count):
            self._check_cancelled()
            text = self._text(
                "get", url, "学习次数上报", no_retry=True, statuses=(200, 204),
                headers={"Referer": COURSE_URL},
            )
            self._check_report(text)
            submitted += 1
            if callable(on_progress):
                on_progress(submitted, count)
            self._check_cancelled()
            if index + 1 < count:
                self._wait(interval)
        after = read_total()
        self._check_cancelled()
        return {"before": before, "after": after, "submitted": submitted, "warnings": list(dict.fromkeys(warnings))}

    def _resource_parts(self, course, resource, *, require_object_id=True):
        self._course_params(course)
        if str(resource.get("course_id")) != str(course["courseId"]):
            raise ValueError("资源与所选课程不匹配")
        attachment, defaults = resource.get("_attachment"), resource.get("_defaults", {})
        if not isinstance(attachment, dict) or not isinstance(defaults, dict):
            raise ValueError("资源缺少新鲜的课程参数，请重新读取")
        if not isinstance(attachment.get("property", {}), dict):
            raise ValueError("资源属性格式无效")
        for key in ("courseId", "clazzId", "cpi"):
            if _scalar(defaults.get(key)) and str(defaults[key]) != str(course[key]):
                raise ValueError("资源参数与所选课程不匹配")
        object_id = self._object_id(attachment)
        if require_object_id and not object_id:
            raise ValueError("资源没有可用的文件标识")
        return attachment, defaults, object_id

    def _media_status(self, object_id):
        result = self._json(
            "get", STATUS_URL + quote(object_id, safe=""), "媒体状态",
            params={"k": self.chaoxing.get_fid() or "", "flag": "normal", "_dc": int(time.time() * 1000)},
            headers={"Referer": VIDEO_REFERER},
        )
        if str(result.get("status", "")).lower() not in _SUCCESS:
            raise RuntimeError("媒体尚未就绪或没有访问权限")
        return result

    def watch_video(self, course, resource, seconds, on_progress=None):
        self._check_cancelled()
        requested = _number(seconds)
        if not 0 < requested <= 86400:
            raise ValueError("视频时长必须大于零且不超过 24 小时")
        target = round(requested * 1000)
        if target < 1:
            raise ValueError("视频时长不能小于一毫秒")
        attachment, defaults, object_id = self._resource_parts(course, resource)
        job_id = self._job_id(attachment)
        if resource.get("kind") != "video" or not job_id:
            raise ValueError("该资源没有可用的视频上报参数")
        user_id = _scalar(self.chaoxing.get_uid())
        if not user_id or (_scalar(defaults.get("userid")) and str(defaults["userid"]) != user_id):
            raise ValueError("视频参数不属于当前登录账号，请重新读取资源")
        status = self._media_status(object_id)
        try:
            duration = round(_number(status.get("duration")) * 1000)
        except (TypeError, ValueError):
            raise RuntimeError("媒体时长无效") from None
        token = _scalar(status.get("dtoken"))
        if duration < 1 or not token:
            raise RuntimeError("媒体缺少有效时长或上报凭据")
        report = defaults.get("reportUrl") or (
            "https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/" + quote(str(course["cpi"]), safe="")
        )
        url = _trusted_url(report, purpose="video").rstrip("/") + "/" + quote(token, safe="")
        prop = attachment.get("property", {})
        other_info = _scalar(attachment.get("otherInfo")).split("&", 1)[0]
        rt = prop.get("rt")
        if not rt:
            match = re.search(r"-rt_([1d])", other_info)
            rt = "1" if match and match[1] == "1" else "0.9"
        try:
            heartbeat = min(60, max(1, _number(defaults.get("reportTimeInterval", 60))))
        except ValueError:
            heartbeat = 60
        heartbeat_ms = round(heartbeat * 1000)

        def report_progress(position, play_type):
            duration_seconds = _seconds(duration)
            enc = hashlib.md5(
                f"[{course['clazzId']}][{user_id}][{job_id}][{object_id}]"
                f"[{position}][d_yHJ!$pdA~5][{duration}][0_{duration_seconds}]".encode("utf-8")
            ).hexdigest()
            params = {
                "clazzId": str(course["clazzId"]), "courseId": str(course["courseId"]),
                "userid": user_id, "jobid": job_id, "objectId": object_id,
                "playingTime": _seconds(position), "duration": duration_seconds,
                "clipTime": f"0_{duration_seconds}", "otherInfo": other_info,
                "isdrag": play_type, "view": "pc", "dtype": "Video", "enc": enc,
                "rt": rt, "_t": int(time.time() * 1000),
            }
            for key in ("videoFaceCaptureEnc", "attDuration", "attDurationEnc"):
                if _scalar(attachment.get(key)):
                    params[key] = attachment[key]
            # Heartbeats wait up to a minute. A reused keep-alive socket that
            # the platform already closed becomes ConnectionError, and reports
            # must not inherit automatic retries (no_retry=True keeps replay
            # protection). A heartbeat is pinned to an absolute position, so
            # one manual retry after a transport-level failure cannot
            # double-report; platform rejections raise inside _check_report
            # and are never retried.
            for attempt in range(2):
                try:
                    text = self._text(
                        "get", url, "视频时长上报", no_retry=True,
                        params=params, headers={"Referer": VIDEO_REFERER, "Connection": "close"},
                    )
                    break
                except _NetworkRequestError as exc:
                    if attempt:
                        raise
                    logger.warning("视频时长上报网络中断，重试一次（位置 {} 毫秒）: {}", position, exc)
            self._check_report(text, video=True)

        completed = position = 0
        report_progress(0, 3)
        if callable(on_progress):
            on_progress(0, _seconds(target))
        while completed < target:
            self._check_cancelled()
            step = min(heartbeat_ms, duration - position, target - completed)
            self._wait(step / 1000)
            next_position = position + step
            report_progress(next_position, 4 if next_position == duration else 0)
            completed += step
            position = next_position
            if callable(on_progress):
                on_progress(_seconds(completed), _seconds(target))
            self._check_cancelled()
            if position == duration and completed < target:
                position = 0
                report_progress(0, 3)
        return {"seconds": _seconds(completed)}

    def _text_follow(self, url, label, *, purpose="read_page", params=None, headers=None):
        """Fetch an HTML page while validating every redirect hop against Chaoxing hosts."""
        current_params = params
        for _ in range(6):
            url = _trusted_url(url, purpose=purpose)
            response = self._request(
                "get", url, label, statuses=(200, *_REDIRECTS),
                params=current_params, headers=headers,
            )
            current_params = None
            try:
                if response.status_code == 200:
                    return url, response.text
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError(f"{label}重定向缺少目标地址")
                url = _trusted_url(location, purpose=purpose, base=url)
            finally:
                response.close()
        raise RuntimeError(f"{label}重定向次数过多")

    @staticmethod
    def _check_read_report(text):
        stripped = text.strip()
        # Verified live: data-xxt.aichaoxing.com/analysis/ac_mark returns HTTP 200 with "".
        if not stripped:
            return
        try:
            data = json.loads(stripped)
        except ValueError:
            if "<" in stripped:
                _check_html(stripped, "阅读时长上报")
            raise RuntimeError("阅读时长上报返回了无效响应，请检查登录状态") from None
        _check_business(data, "阅读时长上报")

    def _send_read_point(self, payload, uid, *, referer, timestamp=None):
        base_url = _trusted_url(READ_POINT_URL, purpose="read_point")
        query, _ = sign_read_point(payload, uid, timestamp or _read_point_timestamp())
        url = f"{base_url}?{query}"
        headers = {"Referer": referer, "Connection": "close"}
        for attempt in range(2):
            try:
                text = self._text("get", url, "阅读时长上报", no_retry=True, headers=headers)
                break
            except _NetworkRequestError as exc:
                if attempt:
                    raise
                logger.warning("阅读时长上报网络中断，重试一次: {}", exc)
        self._check_read_report(text)

    def _send_read_log(self, target, scroll_h):
        try:
            url = _trusted_url(f"https://{target['mooc_host']}/multimedia/readlog", purpose="read_log")
            params = {
                "courseid": target["book_id"],
                "chapterid": target["node_id"],
                "height": target["height"],
                "_from_": target["from_token"],
                "h": scroll_h,
            }
            self._text(
                "get", url, "阅读滚动日志", no_retry=True, params=params,
                headers={"Referer": target["referer"], "Connection": "close"},
            )
        except ToolCancelled:
            raise
        except Exception as exc:
            logger.debug("阅读滚动日志跳过: {}", exc)

    @staticmethod
    def _parse_read_tips(soup):
        text = soup.get_text(" ", strip=True)
        before_minutes = target_minutes = None
        match = re.search(r"您的阅读总时长\s*[:：]?\s*([\d,]+(?:\.\d+)?)\s*分钟", text)
        if match:
            try:
                before_minutes = _number(match[1].replace(",", ""))
            except ValueError:
                pass
        match = re.search(r"阅读总时长达到\s*([\d,]+(?:\.\d+)?)\s*分钟", text)
        if match:
            try:
                target_minutes = _number(match[1].replace(",", ""))
            except ValueError:
                pass
        return before_minutes, target_minutes

    @staticmethod
    def _query_map(url):
        result = {}
        for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=False):
            lower = key.lower()
            if lower not in result:
                result[lower] = value.strip()
        return result

    def _resolve_special_target(self, course, attachment, defaults, chapter_id, uid, *,
                                mooc_host="mooc1.chaoxing.com", fallback_enc=""):
        job_id = self._job_id(attachment)
        if not job_id:
            raise ValueError("该阅读任务缺少任务标识")
        work_url = _trusted_url(f"https://{mooc_host}/mooc-ans/api/work", purpose="read_page")
        work_params = {
            "api": "1", "workId": "", "jobid": job_id, "needRedirect": "true",
            "type": "read", "knowledgeid": str(chapter_id), "ut": "s", "isphone": "false",
            "clazzId": str(course["clazzId"]), "enc": _scalar(attachment.get("enc")),
            "utenc": _scalar(defaults.get("utenc")), "courseid": str(course["courseId"]),
        }
        final_work_url, work_html = self._text_follow(
            work_url, "阅读任务列表", params=work_params,
            headers={"Referer": f"https://{mooc_host}/ananas/modules/read/index.html"},
        )
        soup = _check_html(work_html, "阅读任务列表")
        before_minutes, target_minutes = self._parse_read_tips(soup)
        book_links = []
        seen_books = set()
        for anchor in soup.select(".readList a[href], a[href]"):
            href = html.unescape(_scalar(anchor.get("href")))
            if not href or not re.search(r"/(?:course|zt)/\d+\.html", href):
                continue
            try:
                book_url = _trusted_url(href, purpose="read_page", base=final_work_url)
            except ValueError:
                continue
            if book_url in seen_books:
                continue
            seen_books.add(book_url)
            title_node = anchor.select_one(".readTitle")
            book_title = (title_node.get_text(" ", strip=True) if title_node else anchor.get_text(" ", strip=True))
            book_links.append((book_url, book_title[:120]))
        if not book_links:
            raise RuntimeError("阅读任务页面未找到可阅读的拓展书籍链接")

        last_error = None
        for book_url, book_title in book_links:
            self._check_cancelled()
            try:
                final_book_url, book_html = self._text_follow(
                    book_url, "阅读书籍目录", headers={"Referer": final_work_url},
                )
                book_soup = _check_html(book_html, "阅读书籍目录")
                book_host = (urlsplit(final_book_url).hostname or mooc_host).lower()
                book_match = re.search(r"/(?:course|zt)/(\d+)\.html", urlsplit(final_book_url).path)
                if not book_match:
                    raise RuntimeError("无法识别拓展书籍编号")
                book_id = book_match[1]
                book_query = self._query_map(final_book_url)
                from_token = book_query.get("_from_") or (
                    f"{course['courseId']}_{course['clazzId']}_{uid}_{fallback_enc}" if fallback_enc else ""
                )
                node_url = None
                for anchor in book_soup.select("a[href]"):
                    href = html.unescape(_scalar(anchor.get("href")))
                    if "visitnodedetail" in href and "knowledgeId=" in href:
                        node_url = _trusted_url(href, purpose="read_page", base=final_book_url)
                        break
                if not node_url:
                    node_match = re.search(r"[?&]knowledge[Ii]d=(\d{1,20})", book_html)
                    if node_match:
                        node_url = _trusted_url(
                            f"https://{book_host}/mooc-ans/ztnodedetailcontroller/visitnodedetail?"
                            + urlencode({
                                "courseId": book_id, "knowledgeId": node_match[1],
                                "_from_": from_token, "_fromV2_": "", "rtag": "",
                            }),
                            purpose="read_page",
                        )
                if not node_url:
                    raise RuntimeError("无法在拓展书籍中找到可读章节")
                final_node_url, node_html = self._text_follow(
                    node_url, "阅读章节内容", headers={"Referer": final_book_url},
                )
                node_soup = _check_html(node_html, "阅读章节内容")
                node_query = self._query_map(final_node_url)
                node_id = node_query.get("knowledgeid", "")
                if not _NUMERIC_ID.fullmatch(node_id):
                    raise RuntimeError("阅读章节标识无法解析")
                from_token = node_query.get("_from_") or from_token
                rtag = node_query.get("rtag", "")
                for tag in node_soup(["script", "style"]):
                    tag.decompose()
                word_count = max(100, len(node_soup.get_text("", strip=True)))
                img_count = len(node_soup.find_all("img"))
                height = max(800, min(6000, word_count // 2 + img_count * 300))
                return {
                    "mode": "special",
                    "book_id": book_id,
                    "node_id": node_id,
                    "book_title": book_title,
                    "from_token": from_token,
                    "rtag": rtag,
                    "wc": word_count,
                    "ic": img_count,
                    "height": height,
                    "mooc_host": (urlsplit(final_node_url).hostname or book_host).lower(),
                    "referer": final_node_url,
                    "before_minutes": before_minutes,
                    "target_minutes": target_minutes,
                }
            except ToolCancelled:
                raise
            except Exception as exc:
                last_error = exc
                continue
        raise RuntimeError(f"无法打开拓展阅读章节：{last_error}")

    def _resolve_doc_target(self, course, attachment, defaults, uid, *, course_enc=""):
        object_id = self._object_id(attachment)
        if not object_id:
            raise ValueError("该文档没有可用的文件标识")
        prop = attachment.get("property") or {}
        enc = (course_enc or _scalar(defaults.get("chapterEnc"))
               or _scalar(defaults.get("enc")) or _scalar(defaults.get("mtEnc")))
        from_token = f"{course['courseId']}_{course['clazzId']}_{uid}_{enc}" if enc else ""
        try:
            page_count = max(1, int(float(_scalar(prop.get("pagenum")) or "1")))
        except ValueError:
            page_count = 1
        return {
            "mode": "doc",
            "object_id": object_id,
            "from_token": from_token,
            "wc": 22,
            "ic": page_count,
            "height": max(600, page_count * 400),
            "referer": f"https://pan-yz.chaoxing.com/screen/v2/file_{quote(object_id, safe='')}",
            "before_minutes": None,
            "target_minutes": None,
        }

    @staticmethod
    def _build_read_payload(target, state, scroll_h):
        if target["mode"] == "special":
            ext = json.dumps(
                {"_from_": target["from_token"], "rtag": target.get("rtag", "")},
                separators=(",", ":"), ensure_ascii=False,
            )
            return {
                "a": None,
                "r": f"{target['book_id']},{target['node_id']}",
                "t": "special",
                "l": 1,
                "f": 0,
                "wc": target["wc"],
                "ic": target["ic"],
                "v": 2,
                "s": state,
                "h": scroll_h,
                "e": "",
                "ext": ext,
            }
        payload = {
            "r": target["object_id"],
            "t": "doc",
            "l": 1,
            "f": 4,
            "p": 1,
            "tp": 1,
            "wc": target["wc"],
            "ic": target["ic"],
            "v": 2,
            "s": state,
            "h": scroll_h,
        }
        if target.get("from_token"):
            payload["ext"] = json.dumps(
                {"_from_": target["from_token"]}, separators=(",", ":"), ensure_ascii=False,
            )
        return payload

    def _run_read_targets(self, targets, uid, seconds, on_progress=None):
        self._check_cancelled()
        requested = _number(seconds)
        if not 0 < requested <= 86400:
            raise ValueError("阅读时长必须大于零且不超过 24 小时")
        target_ms = round(requested * 1000)
        if target_ms < 1:
            raise ValueError("阅读时长不能小于一毫秒")
        heartbeat_ms = READ_HEARTBEAT_SECONDS * 1000

        def send_all(state, scroll_step):
            for entry in targets:
                self._check_cancelled()
                max_h = max(400, entry.get("height", 1200))
                scroll_h = 0 if state == 1 else (scroll_step * 400) % max_h
                payload = self._build_read_payload(entry, state, scroll_h)
                self._send_read_point(payload, uid, referer=entry["referer"])
                if entry["mode"] == "special" and state in (1, 2):
                    self._send_read_log(entry, scroll_h)

        completed = 0
        tick = 0
        send_all(1, 0)
        if callable(on_progress):
            on_progress(0, _seconds(target_ms))
        while completed < target_ms:
            self._check_cancelled()
            step = min(heartbeat_ms, target_ms - completed)
            self._wait(step / 1000)
            tick += 1
            send_all(2, tick)
            completed += step
            if callable(on_progress):
                on_progress(_seconds(completed), _seconds(target_ms))
        self._check_cancelled()
        send_all(0, tick)
        result = {"seconds": _seconds(completed)}
        for entry in targets:
            if entry.get("before_minutes") is not None:
                result["before_minutes"] = entry["before_minutes"]
            if entry.get("target_minutes") is not None:
                result["target_minutes"] = entry["target_minutes"]
            if "before_minutes" in result or "target_minutes" in result:
                break
        return result

    def read_resource(self, course, resource, seconds, on_progress=None):
        self._check_cancelled()
        kind = resource.get("kind")
        if kind not in {"read", "document"}:
            raise ValueError("该资源不支持累计阅读时长")
        attachment, defaults, _ = self._resource_parts(course, resource, require_object_id=kind == "document")
        user_id = _scalar(self.chaoxing.get_uid())
        if not user_id or (_scalar(defaults.get("userid")) and str(defaults["userid"]) != user_id):
            raise ValueError("阅读参数不属于当前登录账号，请重新读取资源")
        course_enc = _scalar(resource.get("_course_enc"))
        if kind == "read":
            target = self._resolve_special_target(
                course, attachment, defaults, resource.get("chapter_id", ""), user_id,
                fallback_enc=course_enc,
            )
        else:
            target = self._resolve_doc_target(course, attachment, defaults, user_id, course_enc=course_enc)
        return self._run_read_targets([target], user_id, seconds, on_progress=on_progress)

    def read_study_url(self, raw_url, seconds, *, enrolled=None, on_progress=None):
        self._check_cancelled()
        parsed = parse_studentstudy_url(raw_url)
        user_id = _scalar(self.chaoxing.get_uid())
        if not user_id:
            raise ValueError("当前登录账号缺少用户标识，请重新登录")
        enrolled_course = enrolled.get(parsed["courseId"]) if isinstance(enrolled, dict) else None
        cpi = parsed["cpi"] or (_scalar(enrolled_course.get("cpi")) if isinstance(enrolled_course, dict) else "")
        if not cpi:
            raise ValueError("该链接缺少 cpi 参数且未在当前账号课程列表中找到，请确认已选修该课程或使用完整链接")
        course_title = (
            _scalar(enrolled_course.get("title")) if isinstance(enrolled_course, dict) else ""
        ) or f"课程 {parsed['courseId']}"
        course = {
            "courseId": parsed["courseId"],
            "clazzId": parsed["clazzId"],
            "cpi": cpi,
            "title": course_title,
        }
        chapter = {"id": parsed["chapterId"], "title": f"章节 {parsed['chapterId']}", "locked": False}
        special_targets = []
        doc_targets = []
        seen_docs = set()
        for _, data in self.iter_card_pages(course, chapter, host=parsed["host"]):
            defaults = data.get("defaults", {})
            if _scalar(defaults.get("userid")) and str(defaults["userid"]) != user_id:
                raise ValueError("章节卡片不属于当前登录账号，请重新登录")
            for attachment in data.get("attachments", []):
                self._check_cancelled()
                if is_live_card(attachment):
                    continue
                prop = attachment.get("property") or {}
                if not isinstance(prop, dict):
                    continue
                att_kind = _scalar(attachment.get("type")).lower()
                module = _scalar(prop.get("module")).lower()
                job_id = self._job_id(attachment)
                if att_kind == "read" or module == "insertread" or job_id.startswith("read-"):
                    special_targets.append(self._resolve_special_target(
                        course, attachment, defaults, parsed["chapterId"], user_id,
                        mooc_host=parsed["host"], fallback_enc=parsed["enc"],
                    ))
                elif (att_kind in {"document", "doc", "pdf"} or module == "insertdoc") and len(doc_targets) < MAX_CHAPTER_DOCS:
                    object_id = self._object_id(attachment)
                    if object_id and object_id not in seen_docs:
                        seen_docs.add(object_id)
                        doc_targets.append(self._resolve_doc_target(
                            course, attachment, defaults, user_id, course_enc=parsed["enc"],
                        ))
        targets = special_targets or doc_targets
        if not targets:
            raise RuntimeError("该章节未找到可累计阅读时长的阅读任务或文档")
        result = self._run_read_targets(targets, user_id, seconds, on_progress=on_progress)
        result["targets"] = len(targets)
        result["mode"] = targets[0]["mode"]
        result["course_title"] = course_title
        return result

    @staticmethod
    def _download_name(name, filename, url, pdf):
        def clean(value):
            value = unicodedata.normalize("NFKC", _scalar(value))
            return re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', "_", value).strip(" .")

        friendly = clean(name) or clean(filename) or "resource"
        extension = ".pdf" if pdf else Path(clean(filename)).suffix
        if not re.fullmatch(r"\.[a-zA-Z0-9]{1,12}", extension):
            extension = Path(urlsplit(url).path).suffix
        if not re.fullmatch(r"\.[a-zA-Z0-9]{1,12}", extension):
            extension = ""
        current_extension = Path(friendly).suffix
        if extension and re.fullmatch(r"\.[a-zA-Z0-9]{1,12}", current_extension):
            friendly = friendly[:-len(current_extension)]
        if friendly.split(".", 1)[0].upper() in (
            {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            | {f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)}
        ):
            friendly = "_" + friendly
        friendly = friendly.encode("utf-8")[:160].decode("utf-8", errors="ignore").rstrip(" .") or "resource"
        return friendly + extension

    @staticmethod
    def _download_directory(directory):
        path = reject_links(directory)
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve(strict=True)

    def _open_download(self, url):
        for _ in range(6):
            url = _trusted_url(url)
            response = self._request(
                "get", url, "资源下载", stream=True, statuses=(200, *_REDIRECTS),
                headers={"Referer": VIDEO_REFERER, "Accept-Encoding": "identity"},
            )
            if response.status_code == 200:
                return response
            try:
                location = response.headers.get("Location")
                if not location:
                    raise RuntimeError("下载重定向缺少目标地址")
                url = _trusted_url(location, base=url)
            finally:
                response.close()
        raise RuntimeError("下载重定向次数过多")

    def _publish_download(self, part, directory, name):
        stem, suffix = Path(name).stem, Path(name).suffix
        for index in range(10000):
            self._check_cancelled()
            candidate = directory / (name if index == 0 else f"{stem} ({index}){suffix}")
            try:
                # Atomic no-clobber publication on NTFS and normal Unix filesystems.
                os.link(part, candidate)
            except FileExistsError:
                continue
            except OSError as exc:
                if exc.errno not in {errno.EPERM, errno.EACCES, errno.ENOSYS, errno.ENOTSUP, errno.EXDEV}:
                    raise
                # Filesystems without hard links still get exclusive creation.
                try:
                    output = candidate.open("xb")
                except FileExistsError:
                    continue
                try:
                    with output, part.open("rb") as source:
                        while True:
                            self._check_cancelled()
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                except BaseException:
                    candidate.unlink()
                    raise
            return candidate
        raise RuntimeError("下载目录中同名文件过多")

    def download_resource(self, course, resource, directory, on_progress=None):
        self._check_cancelled()
        _, _, object_id = self._resource_parts(course, resource)
        status = self._media_status(object_id)
        pdf = bool(status.get("pdf")) and (
            resource.get("kind") == "document" or status.get("pagenum") is not None
            or not any(status.get(key) for key in ("httphd", "httpshd", "http", "https", "download", "downloadUrl", "url"))
        )
        fields = ("pdf",) if pdf else ("httphd", "httpshd", "http", "https", "download", "downloadUrl", "url")
        raw_url = next((status[key] for key in fields if status.get(key)), None)
        url = _trusted_url(raw_url)
        name = self._download_name(resource.get("name"), status.get("filename"), url, pdf)
        target_directory = self._download_directory(directory)
        part = None
        received = 0
        try:
            response = self._open_download(url)
            try:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if content_type in {"text/html", "application/xhtml+xml"} or (
                    content_type == "application/json" and not name.lower().endswith(".json")
                ):
                    raise RuntimeError("下载返回了网页或错误信息，请检查登录状态")
                if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
                    raise RuntimeError("下载响应编码不支持长度校验")
                length = response.headers.get("Content-Length")
                if length is not None and not re.fullmatch(r"\d+", length):
                    raise RuntimeError("下载响应的文件长度无效")
                total = int(length) if length is not None else None
                self._check_cancelled()
                descriptor, temporary = tempfile.mkstemp(prefix=".course-", suffix=".part", dir=target_directory)
                part = Path(temporary)
                with os.fdopen(descriptor, "wb") as output:
                    if callable(on_progress):
                        on_progress(0, total)
                    for chunk in response.iter_content(chunk_size=256 * 1024):
                        self._check_cancelled()
                        if not chunk:
                            continue
                        received += len(chunk)
                        if total is not None and received > total:
                            raise RuntimeError("下载大小超过平台声明的文件长度")
                        output.write(chunk)
                        if callable(on_progress):
                            on_progress(received, total)
                    self._check_cancelled()
                    if total is not None and received != total:
                        raise RuntimeError(f"文件下载不完整（收到 {received} 字节，应为 {total} 字节）")
            except requests.RequestException as exc:
                raise RuntimeError(f"下载连接中断（{type(exc).__name__}）") from None
            finally:
                response.close()
            self._check_cancelled()
            target = self._publish_download(part, target_directory, name)
            return {"path": str(target), "name": target.name, "bytes": received}
        finally:
            if part is not None:
                part.unlink(missing_ok=True)
