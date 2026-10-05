# -*- coding: utf-8 -*-
import random
import re
import sys
import threading
import time
from enum import Enum
from hashlib import md5
from typing import Optional, Literal

import requests
from loguru import logger
from requests import RequestException
from tqdm import tqdm

from api.answer import Tiku
from api.answer_check import cut, match_answer
from api.captcha import CAPTCHA_PROTOCOL_VERIFIED, CxCaptcha, is_captcha_response
from api.cipher import AESCipher
from api.config import GlobalConst as gc
from api.cookies import save_cookies
from api.logger import truncated
from api.privacy import register_secret
from api.work_result import WorkResultReader
from api.session import HTTP_TIMEOUT, SessionManager
from api.decode import (
    card_page_has_payload,
    decode_course_list,
    decode_course_point,
    decode_course_card,
    decode_course_folder,
    decode_questions_info,
)
from api.exceptions import CaptchaNotPassed, MaxRetryExceeded


def _is_cancelled(cancel_check):
    if callable(cancel_check):
        try:
            return bool(cancel_check())
        except Exception as exc:
            logger.debug("读取停止信号失败: {}", exc)
    return False


# 登录页只会带这两个特征; 单独的 "login" 一词在正常课程页脚本里也会出现,
# 旧启发式会误判有效会话为失效 (多余地触发重登录)。
_LOGIN_PAGE_MARKERS = ("passport2.chaoxing.com", "fanyalogin")


def _wait_for_cancel(seconds, cancel_check):
    """Keep CLI waits unchanged; task waits check their own signal in slices."""
    if not callable(cancel_check):
        time.sleep(seconds)
        return False
    remaining = seconds
    while remaining > 0:
        if _is_cancelled(cancel_check):
            return True
        interval = min(0.2, remaining)
        time.sleep(interval)
        remaining -= interval
    return _is_cancelled(cancel_check)


def get_timestamp():
    return str(int(time.time() * 1000))


class _SilentProgress:
    """Fallback when a console progress bar cannot be drawn."""

    def __init__(self, initial=0):
        self.n = initial

    def refresh(self):
        return None

    def close(self):
        return None


def _progress_disabled():
    """Desktop hosts pipe stderr; drawing a bar there raises Errno 22."""
    stream = sys.stderr
    if stream is None:
        return True
    try:
        return not stream.isatty()
    except OSError:
        return True


def _open_progress(total, initial, desc):
    try:
        return tqdm(
            total=total,
            initial=initial,
            desc=desc,
            unit_scale=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
            disable=_progress_disabled(),
        )
    except OSError as exc:
        logger.debug("进度条不可用，改为静默执行: {}", exc)
        return _SilentProgress(initial)


def _multi_cut(answer: str, origin_html: str = ""):
    """
    将多选题答案字符串按特定字符进行切割, 并返回切割后的答案列表

    参数:
    answer(str): 多选题答案字符串.

    返回:
    list[str]: 切割后的答案列表,如果无法切割, 则返回默认的选项列表None

    注意:
    如果无法从网页中提取题目信息,将记录警告日志并返回None
    """
    # ',' 在常规被正确划分的选项中出现, 导致无法正确划分选项 (#391),
    # 因此先由 cut() 按 '\n' 匹配, 匹配不到再按照其他字符匹配
    res = cut(answer)
    if res is None:
        logger.warning(
            f"未能从网页中提取题目信息, 以下为相关信息：\n\t{answer}\n\n{origin_html}\n"
        )  # 尝试输出网页内容和选项信息
        logger.warning("未能正确提取题目选项信息! 请反馈并提供以上信息")
        return None
    else:
        return res


def _random_answer(q: dict, options: str, origin_html: str = "") -> str:
    """题库没有可用答案时的随机作答。"""
    answer = ""
    if not options:
        return answer

    if q["type"] == "multiple":
        logger.debug(f"当前选项列表[cut前] -> {options}")
        _op_list = _multi_cut(options, origin_html)
        logger.debug(f"当前选项列表[cut后] -> {_op_list}")

        if not _op_list:
            logger.error(
                "选项为空, 未能正确提取题目选项信息! 请反馈并提供以上信息"
            )
            return answer

        available_options = len(_op_list)
        select_count = 0

        # 根据可用选项数量调整可能选择的选项数
        if available_options <= 1:
            select_count = available_options
        else:
            max_possible = min(4, available_options)
            min_possible = min(2, available_options)

            weights_map = {
                2: [1.0],
                3: [0.3, 0.7],
                4: [0.1, 0.5, 0.4],
                5: [0.1, 0.4, 0.3, 0.2],
            }

            weights = weights_map.get(max_possible, [0.3, 0.4, 0.3])
            possible_counts = list(range(min_possible, max_possible + 1))

            weights = weights[:len(possible_counts)]

            weights_sum = sum(weights)
            if weights_sum > 0:
                weights = [w / weights_sum for w in weights]

            select_count = random.choices(possible_counts, weights=weights, k=1)[0]

        selected_options = random.sample(_op_list, select_count) if select_count > 0 else []

        for option in selected_options:
            answer += option[:1]  # 取首字为答案，例如A或B

        answer = "".join(sorted(answer))
    elif q["type"] == "single":
        answer = random.choice(options.split("\n"))[:1]  # 取首字为答案, 例如A或B
    # 判断题处理
    elif q["type"] == "judgement":
        answer = "true" if random.choice([True, False]) else "false"
    logger.info(f"随机选择 -> {answer}")
    return answer


def _fill_answers_into_form(questions: dict, is_save: bool):
    """将每道题的 answerField 写回提交表单。

    - is_save=True: 仅在 answerSource 为 cover 时写入答案（随机答案留空）。
    - is_save=False: 所有 answer* 字段直接写入（提交时保留随机答案）。
    """
    for q in questions["questions"]:
        src = q.get(f'answerSource{q["id"]}', "")
        # 写入所有 answer* 字段（包括 answer{id}, answer{id}_0 等）
        for key, val in q["answerField"].items():
            if not isinstance(key, str) or not key.startswith("answer"):
                continue
            if is_save:
                questions[key] = val if src == "cover" else ""
            else:
                questions[key] = val

        # 写入 answertype{id}
        answertype_key = f'answertype{q["id"]}'
        if answertype_key in q["answerField"]:
            questions[answertype_key] = q["answerField"][answertype_key]


class Account:
    username = None
    password = None

    def __init__(self, _username, _password):
        register_secret(_username)
        register_secret(_password)
        self.username = _username
        self.password = _password


class RateLimiter:
    def __init__(self, call_interval):
        self.last_call = time.time()
        self.lock = threading.Lock()
        self.call_interval = call_interval

    def limit_rate(self, random_time=False, random_min=0.0, random_max=1.0):
        with self.lock:
            now = time.time()
            extra = random.uniform(random_min, random_max) if random_time else 0.0
            # Reserve the wake time before sleeping so other workers do not
            # queue on this lock for the whole delay. The random pause counts
            # toward the interval, matching the previous single-thread timing.
            wake = max(now + extra, self.last_call + self.call_interval)
            self.last_call = wake
            delay = wake - now
        if delay > 0:
            time.sleep(delay)


class StudyResult(Enum):
    SUCCESS = 0
    FORBIDDEN = 1  # 403
    ERROR = 2
    SKIPPED = 4

    def is_failure(self):
        return self not in {StudyResult.SUCCESS, StudyResult.SKIPPED}

class Chaoxing:
    def __init__(self, account: Account = None, tiku: Tiku = None, **kwargs):
        self.account = account
        self.cipher = AESCipher()
        self.tiku = tiku
        self.kwargs = kwargs
        self.session_manager = SessionManager(account.username if account else None)
        self._closed = False
        self._root_course_list_html = None
        self.rate_limiter = RateLimiter(0.5) # 其他接口速率限制比较松
        self.video_log_limiter = RateLimiter(2) # 上报进度极其容易卡验证码，限制2s一次
        self._captcha_lock = threading.Lock()
        self._captcha_generation = 0
        self._captcha_passed = False

    CAPTCHA_ATTEMPTS = 3

    def solve_captcha(self, cancel_check=None) -> bool:
        """Pass the platform's verification page once for all worker threads.

        Threads that hit the page while another thread is solving it wait and
        reuse that result instead of submitting their own attempt.
        """
        if _is_cancelled(cancel_check):
            return False
        if not CAPTCHA_PROTOCOL_VERIFIED:
            logger.warning("验证码自动处理尚待真实样本验证，请在浏览器中手动完成验证")
            return False
        seen = self._captcha_generation
        while not self._captcha_lock.acquire(timeout=0.1):
            if _is_cancelled(cancel_check):
                return False
        try:
            if _is_cancelled(cancel_check):
                return False
            if self._captcha_generation != seen:
                if self._captcha_passed:
                    self.session_manager.get_session()  # Refresh this worker's cookie snapshot.
                return self._captcha_passed
            passed = False
            try:
                solver = CxCaptcha(self.session_manager, self.account.username if self.account else None)
                for attempt in range(1, self.CAPTCHA_ATTEMPTS + 1):
                    if _is_cancelled(cancel_check):
                        break
                    try:
                        passed = solver.attempt(cancel_check=lambda: _is_cancelled(cancel_check))
                    except Exception as exc:
                        logger.warning("验证码第 {} 次识别失败: {}", attempt, exc)
                    if passed:
                        logger.info("验证码已自动通过")
                        break
                    if attempt < self.CAPTCHA_ATTEMPTS and _wait_for_cancel(1, cancel_check):
                        break
                if not passed and not _is_cancelled(cancel_check):
                    logger.error("验证码自动识别失败，请在浏览器中打开学习通手动完成验证后重试")
            finally:
                self._captcha_passed = passed
                self._captcha_generation += 1
            return passed and not _is_cancelled(cancel_check)
        finally:
            self._captcha_lock.release()

    def login(self, login_with_cookies=False):
        self._root_course_list_html = None
        if login_with_cookies:
            logger.info("Logging in with cookies")
            self.session_manager.update_cookies()
            if not self._validate_cookie_session():
                logger.warning("Cookie 登录校验失败，尝试使用账号密码重新登录")
                if self.account and self.account.username and self.account.password:
                    return self.login(login_with_cookies=False)
                return {"status": False, "msg": "cookies 已失效，请更新 cookies 或提供账号密码"}
            session = self.session_manager.get_session()
            self.session_manager.set_cookies(session.cookies)
            save_cookies(session, self.account.username if self.account else None)
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}

        if not self.account or not self.account.username or not self.account.password:
            return {"status": False, "msg": "用户名或密码不能为空"}
        # A fresh login must not inherit cookies from an earlier login attempt.
        self.session_manager.set_cookies({})
        _session = self.session_manager.get_session()
        _url = "https://passport2.chaoxing.com/fanyalogin"
        _data = {
            "fid": "-1",
            "uname": self.cipher.encrypt(self.account.username),
            "password": self.cipher.encrypt(self.account.password),
            "refer": "https%3A%2F%2Fi.chaoxing.com",
            "t": True,
            "forbidotherlogin": 0,
            "validate": "",
            "doubleFactorLogin": 0,
            "independentId": 0,
        }
        logger.trace("正在尝试登录...")
        resp = _session.post(_url, headers=gc.HEADERS, data=_data, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") is True:
            save_cookies(_session, self.account.username)
            self.session_manager.set_cookies(_session.cookies)
            logger.info("登录成功...")
            return {"status": True, "msg": "登录成功"}
        else:
            return {"status": False, "msg": str(result.get("msg2", "登录失败"))}

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.session_manager.close()
        finally:
            close_tiku = getattr(self.tiku, 'close', None)
            if callable(close_tiku):
                close_tiku()

    def _validate_cookie_session(self) -> bool:
        session = self.session_manager.get_session()
        if not (self._cookie_value('_uid') or self._cookie_value('UID')):
            return False

        try:
            resp = session.post(
                "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata",
                data={"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0},
                timeout=8,
            )
        except RequestException as exc:
            logger.debug("Cookie validation request failed: {}", exc)
            return False

        if resp.status_code != 200:
            return False

        if "passport2.chaoxing.com" in resp.url or any(marker in resp.text for marker in _LOGIN_PAGE_MARKERS):
            return False

        self._root_course_list_html = resp.text
        return True

    def get_fid(self):
        return self._cookie_value('fid')

    def _cookie_value(self, name):
        values = {cookie.value for cookie in self.session_manager.get_session().cookies
                  if cookie.name == name and cookie.value}
        if len(values) > 1:
            raise ValueError(f'登录会话包含冲突的 {name}，请重新登录')
        return next(iter(values), None)

    def get_uid(self):
        uid = self._cookie_value('_uid') or self._cookie_value('UID')
        if uid:
            return uid
        raise ValueError("Cannot get uid !")

    def get_course_list(self, cancel_check=None):
        _session = self.session_manager.get_session()
        _url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/courselistdata"
        _data = {"courseType": 1, "courseFolderId": 0, "query": "", "superstarClass": 0}
        logger.trace("正在读取所有的课程列表...")

        # 接口突然抽风, 增加headers
        # 有可能只是referer的问题
        _headers = {
            "Referer": "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction?moocDomain=https://mooc1-1.chaoxing.com/mooc-ans",
        }
        cached = self._root_course_list_html
        self._root_course_list_html = None
        if cached is None:
            _resp = self._post_past_captcha(_session, _url, cancel_check, headers=_headers, data=_data)
            _resp.raise_for_status()
            cached = _resp.text
        # logger.trace(f"原始课程列表内容:\n{cached}")
        logger.info("课程列表读取完毕...")
        course_list = decode_course_list(cached)

        _interaction_url = "https://mooc2-ans.chaoxing.com/mooc2-ans/visit/interaction"
        _interaction_resp = self._get_past_captcha(_session, _interaction_url, cancel_check)
        _interaction_resp.raise_for_status()
        course_folder = decode_course_folder(_interaction_resp.text)
        for folder in course_folder:
            _data = {
                "courseType": 1,
                "courseFolderId": folder["id"],
                "query": "",
                "superstarClass": 0,
            }
            _resp = self._post_past_captcha(_session, _url, cancel_check, data=_data)
            _resp.raise_for_status()
            course_list += decode_course_list(_resp.text)
        return course_list

    def get_course_point(self, _courseid, _clazzid, _cpi):
        _session = self.session_manager.get_session()
        _url = f"https://mooc2-ans.chaoxing.com/mooc2-ans/mycourse/studentcourse?courseid={_courseid}&clazzid={_clazzid}&cpi={_cpi}&ut=s"
        logger.trace("开始读取课程所有章节...")
        _resp = _session.get(_url)
        _resp.raise_for_status()
        # logger.trace(f"原始章节列表内容:\n{_resp.text}")
        logger.info("课程章节读取成功...")
        return decode_course_point(_resp.text)

    def get_job_list(self, course: dict, point: dict, cancel_check=None) -> tuple[list[dict], dict]:
        if _is_cancelled(cancel_check):
            return [], {}
        _session = self.session_manager.get_session()
        self.rate_limiter.limit_rate()
        job_list = []
        job_info = {}
        passed_jobs = []
        cards_params = {
            "clazzid": course["clazzId"],
            "courseid": course["courseId"],
            "knowledgeid": point["id"],
            "ut": "s",
            "cpi": course["cpi"],
            "v": "2025-0424-1038-3",
            "mooc2": 1
        }

        # Card indexes are contiguous. Stop at the first page without a card
        # payload instead of always probing num=0..6. Seven remains the cap.
        for _possible_num in range(7):
            if _is_cancelled(cancel_check):
                return [], {}

            logger.trace("开始读取章节所有任务点...")

            cards_params.update({"num": str(_possible_num)})
            _resp = self._get_past_captcha(_session, "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards",
                                           cancel_check, params=cards_params)
            if _is_cancelled(cancel_check):
                return [], {}
            _resp.raise_for_status()
            if _resp.status_code != 200:
                raise RequestException(f"任务卡片请求失败: HTTP {_resp.status_code}")
            if not card_page_has_payload(_resp.text):
                break

            _job_list, _job_info = decode_course_card(_resp.text)
            if _job_info.get("notOpen", False):
                # 直接返回, 节省一次请求
                logger.info("该章节未开放")
                return [], _job_info

            job_list += _job_list
            passed_jobs.extend(_job_info.get('passed_jobs', []))
            job_info.update(_job_info)

        job_info['passed_jobs'] = passed_jobs
        if _is_cancelled(cancel_check):
            return [], {}
        if not job_list and not passed_jobs:
            result = self.study_emptypage(course, point, cancel_check=cancel_check)
            if result != StudyResult.SUCCESS:
                raise RequestException(f"空页面任务失败: {point.get('title', point.get('id'))}")
            job_info['empty'] = True
        # logger.trace(f"原始任务点列表内容:\n{_resp.text}")
        logger.info("章节任务点读取成功...")

        return job_list, job_info

    def _request_past_captcha(self, session, send, url, cancel_check=None, **kwargs):
        """Send a read-only request; after a verification page, pass it and retry.

        Reads here have no side effects, so a second attempt is safe. A page
        that is still a verification page raises CaptchaNotPassed: it is never
        page content.
        """
        response = send(url, **kwargs)
        if not is_captcha_response(response):
            return response
        logger.warning("请求触发验证码")
        if self.solve_captcha(cancel_check) and not _is_cancelled(cancel_check):
            response.close()
            response = send(url, **kwargs)
            if not is_captcha_response(response):
                return response
        response.close()
        raise CaptchaNotPassed()

    def _get_past_captcha(self, session, url, cancel_check=None, **kwargs):
        return self._request_past_captcha(session, session.get, url, cancel_check, **kwargs)

    def _post_past_captcha(self, session, url, cancel_check=None, **kwargs):
        return self._request_past_captcha(session, session.post, url, cancel_check, **kwargs)

    def get_enc(self, clazzId, jobid, objectId, playingTime, duration, userid):
        return md5(
            f"[{clazzId}][{userid}][{jobid}][{objectId}][{playingTime * 1000}][d_yHJ!$pdA~5][{duration * 1000}][0_{duration}]".encode()
        ).hexdigest()

    def video_progress_log(
            self,
            _session,
            _course,
            _job,
            _job_info,
            _dtoken,
            _duration,
            _playingTime,
            _type: str = "Video",
            headers: Optional[dict] = None,
            cancel_check=None,
    ) -> tuple[bool, int]:

        if headers is None:
            logger.warning("null headers")
            headers = gc.VIDEO_HEADERS

        self.video_log_limiter.limit_rate(random_time=True, random_max=2)

        if "courseId" in _job["otherinfo"]:
            logger.error(_job["otherinfo"])
            raise RuntimeError("this is not possible")

        user_id = self.get_uid()
        enc = self.get_enc(_course["clazzId"], _job["jobid"], _job["objectid"], _playingTime, _duration, user_id)
        params = {
            "clazzId": _course["clazzId"],
            "playingTime": _playingTime,
            "duration": _duration,
            "clipTime": f"0_{_duration}",
            "objectId": _job["objectid"],
            "otherInfo": _job["otherinfo"],
            "courseId": _course["courseId"],
            "jobid": _job["jobid"],
            "userid": user_id,
            "isdrag": "3",
            "view": "pc",
            "enc": enc,
            "dtype": _type
        }

        _url = (
            f"https://mooc1.chaoxing.com/mooc-ans/multimedia/log/a/"
            f"{_course['cpi']}/"
            f"{_dtoken}"
        )


        face_capture_enc = _job["videoFaceCaptureEnc"]
        att_duration = _job["attDuration"]
        att_duration_enc = _job["attDurationEnc"]

        if face_capture_enc:
            params["videoFaceCaptureEnc"] = face_capture_enc
        if att_duration:
            params["attDuration"] = att_duration
        if att_duration_enc:
            params["attDurationEnc"] = att_duration_enc

        def send():
            resp = _session.get(_url, params=params, headers=headers)
            if not is_captcha_response(resp):
                return resp
            # A verification page means this report was rejected, not counted.
            # Replaying it once after the pass is not the connection-failure
            # replay that reports must avoid: nothing reached the counter.
            logger.warning("视频进度上报触发验证码")
            if not self.solve_captcha(cancel_check):
                return resp
            if _is_cancelled(cancel_check):
                return resp
            resp.close()
            params["_t"] = get_timestamp()
            return _session.get(_url, params=params, headers=headers)

        rt = _job['rt']
        if not rt:
            rt_search = re.search(r"-rt_([1d])", _job['otherinfo'])
            if rt_search:
                rt_char = rt_search.group(1)
                rt = "0.9" if rt_char == "d" else "1"
                logger.trace(f"Got rt from otherinfo: {rt}")

        if rt:
            logger.trace(f"Got rt: {rt}")
            params.update({"rt": rt,
                           "_t": get_timestamp()})
            resp = send()
        else:
            logger.warning("Failed to get rt")
            for rt in [0.9, 1]:
                params.update({"rt": rt,
                               "_t": get_timestamp()})
                resp = send()
                if is_captcha_response(resp):
                    break
                if resp.status_code == 200:
                    logger.trace(truncated(resp.text))
                    return resp.json()["isPassed"], 200
                elif resp.status_code == 403:
                    logger.warning("出现403报错, 正常尝试切换rt")

                else:
                    logger.warning("未知错误 jobid={}, status_code={}, 摘要:\n{}",
                                   _job.get("jobid"),
                                   resp.status_code,
                                   resp.text[:200]
                    )
                    break

        if is_captcha_response(resp):
            logger.error("验证码未通过，跳过当前任务点；请在浏览器中手动完成验证")
            return False, 403

        if resp.status_code == 200:
            logger.trace(truncated(resp.text))
            return resp.json()["isPassed"], 200

        elif resp.status_code == 403:
            logger.debug(
                "视频进度上报返回403, jobid={}, 摘要={}",
                _job.get("jobid"),
                resp.text[:200],
            )

            # 若出现两个rt参数都返回403的情况, 则跳过当前任务
            logger.error("出现403报错, 尝试修复无效, 正在跳过当前任务点...")
            logger.error("请求url: {}", resp.url)
            logger.error("请求头: {}", dict(_session.headers) | headers)
            return False, 403

        logger.error(f"未知错误: {resp.status_code}")
        logger.error("请求url: {}", resp.url)
        logger.error("请求头：{}", dict(_session.headers) | headers)
        return False, resp.status_code


    def _refresh_video_status(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"],
                              cancel_check=None) -> Optional[dict]:
        self.rate_limiter.limit_rate(random_time=True, random_max=0.2)
        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        info_url = (
            f"https://mooc1.chaoxing.com/ananas/status/{job['objectid']}?"
            f"k={self.get_fid()}&flag=normal"
        )
        try:
            resp = self._get_past_captcha(session, info_url, cancel_check, timeout=8, headers=headers)
        except RequestException as exc:
            logger.debug("刷新视频状态失败: {}", exc)
            return None

        if resp.status_code != 200:
            logger.debug("刷新视频状态返回码异常: {}", resp.status_code)
            logger.debug(resp.text)
            return None

        try:
            data = resp.json()
        except ValueError as exc:
            logger.debug("解析视频状态响应失败: {}", exc)
            return None

        if data.get("status") == "success":
            return data

        return None

    def _recover_after_forbidden(self, session: requests.Session, job: dict, _type: Literal["Video", "Audio"],
                                 cancel_check=None):
        # Keep this login instance's session. The account's persisted file may
        # have been updated by a different login while this course was running.
        return self._refresh_video_status(session, job, _type, cancel_check)


    def study_video(
        self,
        _course,
        _job,
        _job_info,
        _speed: float = 1.0,
        _type: Literal["Video", "Audio"] = "Video",
        progress_callback=None,
        cancel_check=None,
    ) -> StudyResult:
        if _is_cancelled(cancel_check):
            return StudyResult.SKIPPED
        _session = self.session_manager.get_session()

        headers = gc.VIDEO_HEADERS if _type == "Video" else gc.AUDIO_HEADERS
        _video_info = self._refresh_video_status(_session, _job, _type, cancel_check)
        if _is_cancelled(cancel_check):
            return StudyResult.SKIPPED

        if _video_info is None:
            logger.error("获取视频信息失败, 跳过任务: {}", _job["name"])
            return StudyResult.ERROR

        _dtoken = _video_info["dtoken"]

        # Time in the real world: last_iter, gc.THRESHOLD
        # Time in the video (can be scaled with the speed factor): duration, play_time, last_log_time, wait_time

        duration = int(_video_info["duration"])
        play_time = int(_job["playTime"]) // 1000
        last_log_time = 0
        last_iter = time.time()
        wait_time = int(random.uniform(30, 90))

        logger.info(f"开始任务: {_job['name']}, 总时长: {duration}s, 已进行: {play_time}s")

        # 首次上报进度
        if callable(progress_callback):
            try:
                progress_callback(_course, _job, float(play_time), float(duration))
            except Exception as exc:
                logger.debug(f"视频进度回调执行失败(初始): {exc}")

        pbar = _open_progress(duration, play_time, _job["name"])

        try:
            forbidden_retry = 0
            max_forbidden_retry = 2
            if _is_cancelled(cancel_check):
                return StudyResult.SKIPPED

            passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration, play_time, _type,
                                                    headers=headers, cancel_check=cancel_check)
            if _is_cancelled(cancel_check):
                return StudyResult.SKIPPED
            passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration, duration, _type,
                                                    headers=headers, cancel_check=cancel_check)
            if _is_cancelled(cancel_check):
                return StudyResult.SKIPPED

            if passed:
                logger.info("任务瞬间完成: {}", _job['name'])
                return StudyResult.SUCCESS

            while not passed:
                if _is_cancelled(cancel_check):
                    return StudyResult.SKIPPED
                # Sometimes the last request needs to be sent several times to complete the task
                if play_time - last_log_time >= wait_time or play_time == duration:
                    passed, state = self.video_progress_log(_session, _course, _job, _job_info, _dtoken, duration,
                                                            int(play_time), _type, headers=headers,
                                                            cancel_check=cancel_check)
                    if _is_cancelled(cancel_check):
                        return StudyResult.SKIPPED

                    if state == 403:
                        if forbidden_retry >= max_forbidden_retry:
                            logger.warning("403重试失败, 跳过当前任务")
                            return StudyResult.FORBIDDEN
                        forbidden_retry += 1
                        logger.warning(
                            "出现403报错, 正在尝试刷新会话状态 (第{}次)",
                            forbidden_retry,
                        )
                        if _wait_for_cancel(random.uniform(2, 4), cancel_check):
                            return StudyResult.SKIPPED
                        refreshed_meta = self._recover_after_forbidden(_session, _job, _type, cancel_check)
                        if refreshed_meta:
                            # FIXME: Maybe it should be considered an error if those keys aren't present in the refreshed meta, so we perhaps shouldn't use get()
                            _dtoken = refreshed_meta.get("dtoken", _dtoken)
                            # The refreshed duration must feed the next enc/clipTime,
                            # otherwise every later report replays the stale value
                            # and keeps hitting 403 until the task is skipped.
                            duration = refreshed_meta.get("duration", duration)
                            # The /ananas/status response has no playTime field, so
                            # the in-flight play position always stays authoritative.

                            logger.debug("Refreshed token: {}, duration: {}", _dtoken, duration)
                            continue

                    elif not passed and state != 200:
                        return StudyResult.ERROR

                    wait_time = int(random.uniform(30, 90))
                    last_log_time = play_time

                dt = (time.time() - last_iter) * _speed # Since uploading the progress takes time, we assume that the video is still playing in the background, so manually calculate the time elapsed is required
                last_iter = time.time()
                play_time = min(duration, play_time+dt)

                pbar.n = int(play_time)
                try:
                    pbar.refresh()
                except OSError as exc:
                    logger.debug("进度条刷新失败: {}", exc)

                # 实时上报进度给外部（如 Web 前端）
                if callable(progress_callback):
                    try:
                        progress_callback(_course, _job, float(play_time), float(duration))
                    except Exception as exc:
                        logger.debug(f"视频进度回调执行失败: {exc}")

                if _wait_for_cancel(gc.THRESHOLD, cancel_check):
                    logger.warning("已停止, 中断当前任务: {}", _job['name'])
                    return StudyResult.SKIPPED

            logger.info("任务完成: {}", _job['name'])
            return StudyResult.SUCCESS
        finally:
            try:
                pbar.close()
            except OSError as exc:
                logger.debug("进度条关闭失败: {}", exc)

    def study_document(self, _course, _job, cancel_check=None) -> StudyResult:
        """
        Study a document in Chaoxing platform.

        This method makes a GET request to fetch document information for a given course and job.

        Args:
            _course (dict): Dictionary containing course information with keys:
                - courseId: ID of the course
                - clazzId: ID of the class
            _job (dict): Dictionary containing job information with keys:
                - jobid: ID of the job
                - otherinfo: String containing node information
                - jtoken: Authentication token for the job

        Returns:
            StudyResult: SUCCESS when the document read is acknowledged
        """
        _session = self.session_manager.get_session()
        _url = f"https://mooc1.chaoxing.com/ananas/job/document?jobid={_job['jobid']}&knowledgeid={re.findall(r'nodeId_(.*?)-', _job['otherinfo'])[0]}&courseid={_course['courseId']}&clazzid={_course['clazzId']}&jtoken={_job['jtoken']}&_dc={get_timestamp()}"
        try:
            _resp = self._get_past_captcha(_session, _url, cancel_check)
        except CaptchaNotPassed:
            return StudyResult.ERROR
        if _resp.status_code != 200:
            return StudyResult.ERROR
        else:
            return StudyResult.SUCCESS

    def _fetch_work_page(self, session, url, params, cancel_check, max_retries=3, delay=1):
        """拉取章节检测页面, 直到响应能解码出题目; 返回 (response, questions)。

        FIXME: Use tenacity for retrying.
        """
        retries = 0
        while retries < max_retries:
            if _is_cancelled(cancel_check):
                return None, None
            try:
                _resp = self._get_past_captcha(session, url, cancel_check, params=params)
                if _is_cancelled(cancel_check):
                    return None, None

                # 未创建完成该测验则不进行答题，目前遇到的情况是未创建完成等同于没题目
                if '教师未创建完成该测验' in _resp.text:
                    raise PermissionError("教师未创建完成该测验")

                with self.session_manager.context():
                    questions = decode_questions_info(_resp.text)

                if _resp.status_code == 200 and questions.get("questions"):
                    return (_resp, questions)

                logger.warning(
                    f"无效响应 (Code: {getattr(_resp, 'status_code', 'Unknown')}), 重试中... ({retries + 1}/{max_retries})")

            except requests.exceptions.RequestException as e:
                logger.warning(f"请求失败: {str(e)[:50]}, 重试中... ({retries + 1}/{max_retries})")
            retries += 1
            if _wait_for_cancel(delay * (2 ** retries), cancel_check):
                return None, None
        raise MaxRetryExceeded(f"超过最大重试次数 ({max_retries})")

    def _handle_work_question(self, q, cancel_check, origin_html) -> bool:
        """Fill one answer; True only when it came from the question bank."""
        if _is_cancelled(cancel_check):
            return False
        logger.debug("当前题目信息 -> {}", truncated(q))
        # 添加搜题延迟 #428 - 默认0s延迟
        query_delay = self.kwargs.get("query_delay", 0)
        if query_delay:
            if _wait_for_cancel(query_delay, cancel_check):
                return False
        if _is_cancelled(cancel_check):
            return False
        # An undecodable encrypted font goes straight to the no-answer path.
        res = None if q.get("undecodable") else self.tiku.query(q)
        if _is_cancelled(cancel_check):
            return False
        answer = ""
        found = False
        if not res:
            # 随机答题
            answer = _random_answer(q, q["options"], origin_html)
            q[f'answerSource{q["id"]}'] = "random"
        else:
            matched = match_answer(res, q, self.tiku.true_list, self.tiku.false_list)
            answer = matched.answer or ""
            if matched.fields:
                q["answerField"].update(matched.fields)

            if not answer:  # 检查 answer 是否为空
                logger.warning(f"找到答案但答案未能匹配 -> {res}\t随机选择答案")
                answer = _random_answer(q, q["options"], origin_html)  # 如果为空，则随机选择答案
                q[f'answerSource{q["id"]}'] = "random"
            else:
                logger.info(f"成功获取到答案：{answer}")
                q[f'answerSource{q["id"]}'] = "cover"
                found = True
        # 填充答案
        q["answerField"][f'answer{q["id"]}'] = answer
        logger.info(f'{q["title"]} 填写答案为 {answer}')
        return found


    def study_work(self, _course, _job, _job_info, cancel_check=None) -> StudyResult:
        if _is_cancelled(cancel_check) or not self.tiku or self.tiku.DISABLE:
            return StudyResult.SKIPPED
        # 学习通这里根据参数差异能重定向至两个不同接口, 需要定向至https://mooc1.chaoxing.com/mooc-ans/workHandle/handle
        _session = self.session_manager.get_session()

        _url = "https://mooc1.chaoxing.com/mooc-ans/api/work"

        params = {
            "api": "1",
            "workId": _job["jobid"].replace("work-", ""),
            "jobid": _job["jobid"],
            "originJobId": _job["jobid"],
            "needRedirect": "true",
            "skipHeader": "true",
            "knowledgeid": str(_job_info["knowledgeid"]),
            "ktoken": _job_info["ktoken"],
            "cpi": _job_info["cpi"],
            "ut": "s",
            "clazzId": _course["clazzId"],
            "type": "",
            "enc": _job["enc"],
            "mooc2": "1",
            "courseid": _course["courseId"],
        }

        final_resp = {}
        questions = {}

        try:
            final_resp, questions = self._fetch_work_page(_session, _url, params, cancel_check)
        except Exception as e:
            if _is_cancelled(cancel_check):
                return StudyResult.SKIPPED
            logger.error(f"请求失败: {e}")
            return StudyResult.ERROR

        if _is_cancelled(cancel_check):
            return StudyResult.SKIPPED
        origin_html = final_resp.text  # 用于配合输出网页源码, 帮助修复#391错误

        # 搜题
        total_questions = len(questions["questions"])
        found_answers = 0

        for q in questions["questions"]:
            if _is_cancelled(cancel_check):
                return StudyResult.SKIPPED
            with self.session_manager.context():
                found_answers += self._handle_work_question(q, cancel_check, origin_html)
        if _is_cancelled(cancel_check):
            return StudyResult.SKIPPED
        cover_rate = (found_answers / total_questions) * 100
        logger.info(f"章节检测题库覆盖率： {cover_rate:.0f}%")

        # 提交模式  现在与题库绑定,留空直接提交, 1保存但不提交
        if self.tiku.get_submit_params() == "1":
            questions["pyFlag"] = "1"
        elif cover_rate >= self.tiku.COVER_RATE * 100:
            questions["pyFlag"] = ""
        else:
            questions["pyFlag"] = "1"
            logger.info(f"章节检测题库覆盖率低于{self.tiku.COVER_RATE * 100:.0f}% ，不予提交")

        # 组建提交表单
        if questions["pyFlag"] == "1":
            _fill_answers_into_form(questions, is_save=True)
        else:
            _fill_answers_into_form(questions, is_save=False)

        del questions["questions"]

        reader = None
        if questions["pyFlag"] == "":
            reader = WorkResultReader(
                _session, _course, questions,
                cancelled=lambda: _is_cancelled(cancel_check),
                wait=lambda seconds: _wait_for_cancel(seconds, cancel_check),
                rate_limit=self.rate_limiter.limit_rate,
            )
            reader.capture_baseline()
        if _is_cancelled(cancel_check):
            return StudyResult.SKIPPED
        res = _session.post(
            "https://mooc1.chaoxing.com/mooc-ans/work/addStudentWorkNew",
            data=questions,
            headers={
                "Host": "mooc1.chaoxing.com",
                "sec-ch-ua-platform": '"Windows"',
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "sec-ch-ua": '"Microsoft Edge";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "sec-ch-ua-mobile": "?0",
                "Origin": "https://mooc1.chaoxing.com",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                # "Referer": "https://mooc1.chaoxing.com/mooc-ans/work/doHomeWorkNew?courseId=246831735&workAnswerId=52680423&workId=37778125&api=1&knowledgeid=913820156&classId=107515845&oldWorkId=07647c38d8de4c648a9277c5bed7075a&jobid=work-07647c38d8de4c648a9277c5bed7075a&type=&isphone=false&submit=false&enc=1d826aab06d44a1198fc983ed3d243b1&cpi=338350298&mooc2=1&skipHeader=true&originJobId=work-07647c38d8de4c648a9277c5bed7075a",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
            },
        )
        if res.status_code == 200:
            res_json = res.json()
            if res_json["status"]:
                logger.info(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题成功 -> {res_json["msg"]}')
            else:
                msg = str(res_json.get("msg", ""))
                # 作业已过期：直接视为跳过本作业，不再重试
                if "已过期" in msg:
                    logger.warning(
                        f'{"提交" if questions["pyFlag"] == "" else "保存"}答题失败(作业已过期，将跳过本作业) -> {msg}'
                    )
                    return StudyResult.SKIPPED

                logger.error(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题失败 -> {msg}')
                return StudyResult.ERROR
        else:
            logger.error(f'{"提交" if questions["pyFlag"] == "" else "保存"}答题失败 -> {res.text}')
            return StudyResult.ERROR
        if reader is not None:
            result = reader.check()
            _job["work_result"] = result
            if result["result_status"] == "confirmed":
                logger.info("章节测验已提交，平台记录成绩：{} 分（不代表全部答对）", result["score"])
            else:
                logger.warning("章节测验已提交，成绩未确认：{}", result["reason"])
        return StudyResult.SKIPPED if questions["pyFlag"] == "1" else StudyResult.SUCCESS

    def study_read(self, _course, _job, _job_info, cancel_check=None) -> StudyResult:
        """
        阅读任务学习, 仅完成任务点, 并不增长时长
        """
        _session = self.session_manager.get_session()
        try:
            _resp = self._get_past_captcha(
                _session,
                "https://mooc1.chaoxing.com/ananas/job/readv2",
                cancel_check,
                params={
                    "jobid": _job["jobid"],
                    "knowledgeid": _job_info["knowledgeid"],
                    "jtoken": _job["jtoken"],
                    "courseid": _course["courseId"],
                    "clazzid": _course["clazzId"],
                },
            )
        except CaptchaNotPassed:
            return StudyResult.ERROR
        if _resp.status_code != 200:
            logger.error(f"阅读任务学习失败 -> [{_resp.status_code}]{_resp.text}")
            return StudyResult.ERROR
        else:
            _resp_json = _resp.json()
            logger.info(f"阅读任务学习 -> {_resp_json['msg']}")
            return StudyResult.SUCCESS

    def study_emptypage(self, _course, point, cancel_check=None) -> StudyResult:
        _session = self.session_manager.get_session()
        # &cpi=0&verificationcode=&mooc2=1&microTopicId=0&editorPreview=0
        try:
            _resp = self._get_past_captcha(
                _session,
                "https://mooc1.chaoxing.com/mooc-ans/mycourse/studentstudyAjax",
                cancel_check,
                params={
                    "courseId": _course["courseId"],
                    "clazzid": _course["clazzId"],
                    "chapterId": point["id"],
                    "cpi": _course["cpi"],
                    "verificationcode": "",
                    "mooc2": 1,
                    "microTopicId": 0,
                    "editorPreview": 0,
                },
            )
        except CaptchaNotPassed:
            logger.error(f"空页面任务失败 -> {point['title']}")
            return StudyResult.ERROR
        if _resp.status_code != 200:
            logger.error(f"空页面任务失败 -> [{_resp.status_code}]{point['title']}")
            return StudyResult.ERROR
        else:
            logger.info(f"空页面任务完成 -> {point['title']}")
            return StudyResult.SUCCESS
