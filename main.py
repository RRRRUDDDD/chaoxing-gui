# -*- coding: utf-8 -*-
import argparse
import configparser
import enum
import math
import sys
import threading
import time
import traceback
from concurrent.futures.thread import ThreadPoolExecutor
from contextlib import nullcontext
from contextvars import copy_context
from dataclasses import dataclass, field
from queue import PriorityQueue
from typing import Any

from tqdm import tqdm

from api.answer import Tiku
from api.base import Chaoxing, Account, StudyResult
from api.exceptions import LoginError, InputFormatError
from api.logger import logger
from api.notification import Notification
from api.live import Live
from api.live_process import LiveProcessor

class ChapterResult(enum.Enum):
    SUCCESS=0
    ERROR=1
    NOT_OPEN=2
    PENDING=3
    EMPTY=4
    SKIPPED=5


def log_error(func):
    def wrapper(*args, **kwargs):
        try:
            func(*args, **kwargs)
        except BaseException as e:
            logger.error(f"Error in thread {threading.current_thread().name}: {e}")
            traceback.print_exception(type(e), e, e.__traceback__)
            raise

    return wrapper


def str_to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def validate_jobs(value):
    """Validate the same worker limit for CLI and API callers."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise InputFormatError("并发章节数必须为 1 到 16 的整数")
    try:
        jobs = int(value)
    except ValueError as exc:
        raise InputFormatError("并发章节数必须为 1 到 16 的整数") from exc
    if not 1 <= jobs <= 16:
        raise InputFormatError("并发章节数必须为 1 到 16 的整数")
    return jobs


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="Samueli924/chaoxing",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument("--use-cookies", action="store_true", help="使用cookies登录")

    parser.add_argument(
        "-c", "--config", type=str, default=None, help="使用配置文件运行程序"
    )
    parser.add_argument("-u", "--username", type=str, default=None, help="手机号账号")
    parser.add_argument("-p", "--password", type=str, default=None, help="登录密码")
    parser.add_argument(
        "-l", "--list", type=str, default=None, help="要学习的课程ID列表, 以 , 分隔"
    )
    parser.add_argument(
        "-s", "--speed", type=float, default=1.0, help="视频播放倍速 (默认1, 最大2)"
    )
    parser.add_argument(
        "-j", "--jobs", type=int, default=4, help="同时进行的章节数 (默认4, 如果一个章节有多个任务点，不会限制同时处理任务点的数量)"
    )

    parser.add_argument(
        "-v",
        "--verbose",
        "--debug",
        action="store_true",
        help="启用调试模式, 输出DEBUG级别日志",
    )
    parser.add_argument(
        "-a", "--notopen-action", type=str, default="retry", 
        choices=["retry", "ask", "continue"],
        help="遇到关闭任务点时的行为: retry-重试, ask-询问, continue-继续"
    )
    parser.add_argument(
        "--retry-interval", type=float, default=1.0, help="重试等待时间, 单位秒 (默认1.0)"
    )

    # 在解析之前捕获 -h 的行为
    if len(sys.argv) == 2 and sys.argv[1] in {"-h", "--help"}:
        parser.print_help()
        sys.exit(0)

    return parser.parse_args()


def load_config_from_file(config_path):
    """从配置文件加载设置"""
    config = configparser.ConfigParser()
    config.read(config_path, encoding="utf8")
    
    common_config: dict[str, Any] = {}
    tiku_config: dict[str, Any] = {}
    notification_config: dict[str, Any] = {}
    
    # 检查并读取common节
    if config.has_section("common"):
        common_config = dict(config.items("common"))
        # 处理course_list，将字符串转换为列表
        if "course_list" in common_config and common_config["course_list"]:
            common_config["course_list"] = [item.strip() for item in common_config["course_list"].split(",") if item.strip()]
        # 处理speed，将字符串转换为浮点数
        if "speed" in common_config:
            common_config["speed"] = float(common_config["speed"])
        if "jobs" in common_config:
            common_config["jobs"] = int(common_config["jobs"])
        # 处理notopen_action，设置默认值为retry
        if "notopen_action" not in common_config:
            common_config["notopen_action"] = "retry"
        if "retry_interval" in common_config:
            common_config["retry_interval"] = float(common_config["retry_interval"])
        else:
            common_config["retry_interval"] = 1.0
        if "use_cookies" in common_config:
            common_config["use_cookies"] = str_to_bool(common_config["use_cookies"])
        if "username" in common_config and common_config["username"] is not None:
            common_config["username"] = common_config["username"].strip()
        if "password" in common_config and common_config["password"] is not None:
            common_config["password"] = common_config["password"].strip()

    # 检查并读取tiku节
    if config.has_section("tiku"):
        tiku_config = dict(config.items("tiku"))
        # 处理数值类型转换
        for key in ["delay", "cover_rate"]:
            if key in tiku_config:
                tiku_config[key] = float(tiku_config[key])

    # 检查并读取notification节
    if config.has_section("notification"):
        notification_config = dict(config.items("notification"))
    
    return common_config, tiku_config, notification_config


def build_config_from_args(args):
    """从命令行参数构建配置"""
    common_config = {
        "use_cookies": args.use_cookies,
        "username": args.username,
        "password": args.password,
        "course_list": [item.strip() for item in args.list.split(",") if item.strip()] if args.list else None,
        "speed": args.speed if args.speed else 1.0,
        "jobs": args.jobs,
        "notopen_action": args.notopen_action if args.notopen_action else "retry",
        "retry_interval": args.retry_interval or 1.0,
    }
    return common_config, {}, {}


def init_config():
    """初始化配置"""
    args = parse_args()
    
    if args.config:
        return load_config_from_file(args.config)
    else:
        return build_config_from_args(args)


def init_chaoxing(common_config, tiku_config):
    """初始化超星实例"""
    username = common_config.get("username", "")
    password = common_config.get("password", "")
    use_cookies = common_config.get("use_cookies", False)
    validate_jobs(common_config.get("jobs", 4))
    
    # 如果没有提供用户名密码，从命令行获取
    if (not username or not password) and not use_cookies:
        if not common_config.get("interactive", True):
            raise InputFormatError("用户名或密码不能为空")
        username = input("请输入你的手机号, 按回车确认\n手机号:")
        password = input("请输入你的密码, 按回车确认\n密码:")
    
    account = Account(username, password)
    
    # 设置题库
    tiku = Tiku()
    tiku.config_set(tiku_config)  # 载入配置
    tiku = tiku.get_tiku_from_config()  # 载入题库
    try:
        tiku.init_tiku()
    except BaseException:
        close_tiku = getattr(tiku, 'close', None)
        if callable(close_tiku):
            close_tiku()
        raise
    
    # 获取查询延迟设置
    query_delay = tiku_config.get("delay", 0)
    # 获取AI题库并发配置（仅在使用AI题库时生效）
    ai_concurrency = tiku_config.get("ai_concurrency")
    
    # 实例化超星API
    chaoxing = Chaoxing(account=account, tiku=tiku, query_delay=query_delay, ai_concurrency=ai_concurrency)
    
    return chaoxing


def process_job(chaoxing: Chaoxing, course: dict, job: dict, job_info: dict, speed: float, progress_callback=None) -> StudyResult:
    """处理单个任务点"""
    # 视频任务
    if job["type"] == "video":
        logger.trace(f"识别到视频任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        # 超星的接口没有返回当前任务是否为Audio音频任务
        video_result = chaoxing.study_video(
            course, job, job_info, _speed=speed, _type="Video", progress_callback=progress_callback
        )
        if video_result.is_failure():
            logger.warning("当前任务非视频任务, 正在尝试音频任务解码")
            video_result = chaoxing.study_video(
                course, job, job_info, _speed=speed, _type="Audio", progress_callback=progress_callback)
        if video_result.is_failure():
            logger.warning(
                f"出现异常任务 -> 任务章节: {course['title']} 任务ID: {job['jobid']}, 已跳过"
            )
        return video_result
    # 文档任务
    elif job["type"] == "document":
        logger.trace(f"识别到文档任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        return chaoxing.study_document(course, job)
    # 测验任务
    elif job["type"] == "workid":
        logger.trace(f"识别到章节检测任务, 任务章节: {course['title']}")
        return chaoxing.study_work(course, job, job_info)
    # 阅读任务
    elif job["type"] == "read":
        logger.trace(f"识别到阅读任务, 任务章节: {course['title']}")
        return chaoxing.study_read(course, job, job_info)
    # 直播任务
    elif job["type"] == "live":
        logger.trace(f"识别到直播任务, 任务章节: {course['title']} 任务ID: {job['jobid']}")
        try:
            # 准备直播所需参数
            defaults = {
                "userid": chaoxing.get_uid(),
                "clazzId": course.get("clazzId"),
                "knowledgeid": job_info.get("knowledgeid")
            }

            live = Live(
                attachment=job,
                defaults=defaults,
                course_id=course.get("courseId"),
                session=chaoxing.session_manager.get_session(),
            )

            # 直播刷取是同步循环, 直接在当前线程等待完成
            return StudyResult.SUCCESS if LiveProcessor.run_live(live, speed) else StudyResult.ERROR
        except Exception as e:
            logger.error(f"处理直播任务时出错: {str(e)}")
            return StudyResult.ERROR

    logger.error(f"未知任务类型: {job['type']}")
    return StudyResult.ERROR


@dataclass(order=True)
class ChapterTask:
    index: int
    point: dict[str, Any] = field(compare=False)
    result: ChapterResult = field(default=ChapterResult.PENDING, compare=False)
    tries: int = field(default=0, compare=False)


@dataclass(frozen=True)
class CourseResult:
    tasks: tuple[ChapterTask, ...]

    @property
    def completed(self):
        return [task for task in self.tasks if task.result == ChapterResult.SUCCESS]

    @property
    def empty(self):
        return [task for task in self.tasks if task.result == ChapterResult.EMPTY]

    @property
    def skipped(self):
        return [task for task in self.tasks if task.result == ChapterResult.SKIPPED]

    @property
    def failed(self):
        return [task for task in self.tasks if task.result not in
                {ChapterResult.SUCCESS, ChapterResult.EMPTY, ChapterResult.SKIPPED}]

    @property
    def success(self):
        return not self.failed and not self.skipped


def _known_job_count(point):
    try:
        return max(0, int(point.get('jobCount', 1)))
    except (TypeError, ValueError):
        return 1


def _chapter_counts(point, result):
    if '_task_stats' not in point:
        count = _known_job_count(point)
        point['_task_stats'] = {
            'total': count,
            'completed': count if result == ChapterResult.SUCCESS else 0,
            'failed': count if result == ChapterResult.ERROR else 0,
            'skipped': count if result == ChapterResult.SKIPPED else 0,
        }


def _job_key(job):
    identifier = job.get('jobid') or job.get('objectid') or job.get('id')
    if identifier is None or str(identifier) == '':
        return None
    return str(job.get('type', '')), str(identifier)


def _record_job_counts(point):
    outcomes = list(point['_job_results'].values())
    point['_task_stats'] = {
        'total': len(outcomes),
        'completed': sum(result == StudyResult.SUCCESS for result in outcomes),
        'failed': sum(result.is_failure() for result in outcomes),
        'skipped': sum(result == StudyResult.SKIPPED for result in outcomes),
    }


def _session_context(chaoxing):
    manager = getattr(chaoxing, 'session_manager', None)
    return manager.context() if manager is not None else nullcontext()


def _close_thread_session(chaoxing):
    manager = getattr(chaoxing, 'session_manager', None)
    if manager is not None:
        manager.close_current_session()


class JobProcessor:
    def __init__(self, chaoxing: Chaoxing, course: dict[str, Any],
                 tasks: list[ChapterTask], config: dict[str, Any]):
        self.chaoxing = chaoxing
        self.course = course
        self.speed = config["speed"]
        self.max_tries = 5
        self.tasks = tasks
        for task in tasks:
            task.point.pop('_job_results', None)
            task.point.pop('_task_stats', None)
        self.failed_tasks: list[ChapterTask] = []
        self.task_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.retry_queue: PriorityQueue[ChapterTask] = PriorityQueue()
        self.threads: list[threading.Thread] = []
        self.worker_num = validate_jobs(config.get("jobs", 4))
        self.config = config
        self.retry_interval = float(config.get("retry_interval", 1.0))
        if not math.isfinite(self.retry_interval) or not 0 <= self.retry_interval <= 300:
            raise InputFormatError("重试间隔必须为 0 到 300 秒")
        self._stop = threading.Event()
        self._sentinel = ChapterTask(sys.maxsize, {})
        self._workers = []
        self._retry_worker = None
        self._started = False

    def run(self):
        if self._started:
            raise RuntimeError('A course processor can only run once')
        self._started = True
        if not self.tasks:
            return CourseResult(())
        for task in self.tasks:
            self.task_queue.put(task)
        try:
            self._retry_worker = self._start_thread(self.retry_thread, 'chaoxing-retry')
            for i in range(min(self.worker_num, len(self.tasks))):
                self._workers.append(self._start_thread(self.worker_thread, f'chaoxing-worker-{i + 1}'))
            self.task_queue.join()
            self.retry_queue.join()
            return CourseResult(tuple(self.tasks))
        finally:
            self._stop.set()
            for _ in self._workers:
                self.task_queue.put(self._sentinel)
            if self._retry_worker is not None:
                self.retry_queue.put(self._sentinel)
            for thread in self.threads:
                thread.join()

    def _start_thread(self, target, name):
        context = copy_context()
        thread = threading.Thread(target=context.run, args=(target,), name=name)
        thread.start()
        self.threads.append(thread)
        return thread

    def _finish_task(self, task):
        _chapter_counts(task.point, task.result)
        if task.result == ChapterResult.ERROR:
            self.failed_tasks.append(task)
        callback = self.config.get('chapter_result_callback')
        if callable(callback):
            try:
                callback(self.course, task.point, task.result)
            except Exception as exc:
                logger.error('更新章节结果失败: {}', exc)

    def worker_thread(self):
        try:
            while True:
                task = self.task_queue.get()
                deferred_ack = False
                try:
                    if task is self._sentinel:
                        return
                    try:
                        if self._stop.is_set():
                            task.result = ChapterResult.ERROR
                        else:
                            with _session_context(self.chaoxing):
                                task.result = process_chapter(self.chaoxing, self.course, task.point,
                                                              self.speed, self.config)
                    except BaseException as exc:
                        logger.error('章节处理异常: {} -> {}', task.point.get('title'), exc)
                        task.point['_error'] = str(exc)
                        task.result = ChapterResult.ERROR

                    if task.result == ChapterResult.NOT_OPEN and self.config.get('notopen_action') == 'continue':
                        task.result = ChapterResult.SKIPPED
                        if task.point.get('_job_results'):
                            task.point['_job_results'] = {
                                key: StudyResult.SKIPPED if result.is_failure() else result
                                for key, result in task.point['_job_results'].items()
                            }
                            _record_job_counts(task.point)
                    if task.result in {ChapterResult.ERROR, ChapterResult.NOT_OPEN}:
                        task.tries += 1
                        if task.tries < self.max_tries and not self._stop.is_set():
                            self.retry_queue.put(task)
                            deferred_ack = True
                            continue
                        if task.result == ChapterResult.NOT_OPEN:
                            task.point['_error'] = '章节未开放，已达到最大重试次数'
                        task.result = ChapterResult.ERROR
                        logger.error('章节重试耗尽: {}', task.point.get('title'))
                    elif task.result not in {ChapterResult.SUCCESS, ChapterResult.EMPTY, ChapterResult.SKIPPED}:
                        task.result = ChapterResult.ERROR
                    self._finish_task(task)
                finally:
                    if not deferred_ack:
                        self.task_queue.task_done()
        finally:
            _close_thread_session(self.chaoxing)

    def retry_thread(self):
        while True:
            task = self.retry_queue.get()
            try:
                if task is self._sentinel:
                    return
                try:
                    if self._stop.wait(self.retry_interval):
                        task.result = ChapterResult.ERROR
                        self._finish_task(task)
                    else:
                        # Transfer before acknowledging the previous attempt:
                        # join() must not return while a retry is outstanding.
                        self.task_queue.put(task)
                finally:
                    self.task_queue.task_done()
            finally:
                self.retry_queue.task_done()


def process_chapter(chaoxing: Chaoxing, course: dict[str, Any], point: dict[str, Any],
                    speed: float, config: dict[str, Any] | None = None) -> ChapterResult:
    """Process every job and retain counts without treating skipped work as done."""
    config = config or {}
    logger.info('当前章节: {}', point["title"])
    if point.get('_job_results'):
        _record_job_counts(point)
    else:
        point.pop('_task_stats', None)
    point.pop('_error', None)

    start_callback = config.get('chapter_start_callback')
    if callable(start_callback):
        try:
            start_callback(course, point)
        except Exception as exc:
            logger.debug('调用章节开始回调失败: {}', exc)

    if point.get('has_finished', False):
        _chapter_counts(point, ChapterResult.SUCCESS)
        return ChapterResult.SUCCESS

    chaoxing.rate_limiter.limit_rate(random_time=True, random_min=0, random_max=0.2)
    jobs, job_info = chaoxing.get_job_list(course, point)
    if job_info.get('notOpen', False):
        return ChapterResult.NOT_OPEN

    outcomes = point.setdefault('_job_results', {})
    # The upstream parser filters passed attachments. Keep their identities so
    # a shrinking pending list cannot discard earlier successful work.
    for job in job_info.get('passed_jobs', []):
        key = _job_key(job)
        if key is not None:
            outcomes[key] = StudyResult.SUCCESS
    pending = {}
    for job in jobs:
        key = _job_key(job)
        if key is None:
            raise ValueError('任务点缺少稳定标识')
        if outcomes.get(key) not in {StudyResult.SUCCESS, StudyResult.SKIPPED}:
            pending[key] = job
            outcomes.setdefault(key, StudyResult.ERROR)
    _record_job_counts(point)

    video_progress_callback = config.get('video_progress_callback')

    def run_job(job):
        try:
            with _session_context(chaoxing):
                result = process_job(chaoxing, course, job, job_info, speed,
                                     progress_callback=video_progress_callback)
                return result if isinstance(result, StudyResult) else StudyResult.ERROR
        finally:
            _close_thread_session(chaoxing)

    if pending:
        with ThreadPoolExecutor(max_workers=min(5, len(pending))) as executor:
            futures = [(key, executor.submit(copy_context().run, run_job, job))
                       for key, job in pending.items()]
            for key, future in futures:
                try:
                    outcomes[key] = future.result()
                except BaseException as exc:
                    logger.error('任务点执行失败: {}', exc)
                    point['_error'] = str(exc)
                    outcomes[key] = StudyResult.ERROR
    _record_job_counts(point)
    if point['_task_stats']['failed']:
        return ChapterResult.ERROR
    if point['_task_stats']['skipped']:
        return ChapterResult.SKIPPED

    callback = config.get('chapter_done_callback')
    if callable(callback):
        try:
            callback(course, point)
        except Exception as exc:
            logger.debug('调用章节完成回调失败: {}', exc)
    return ChapterResult.SUCCESS if outcomes else ChapterResult.EMPTY


def process_course(chaoxing: Chaoxing, course: dict[str, Any], config: dict,
                   point_list=None) -> CourseResult:
    """Return final chapter outcomes, reusing a supplied chapter snapshot."""
    logger.info("开始学习课程: {}", course['title'])
    validate_jobs(config.get('jobs', 4))
    if point_list is None:
        point_list = chaoxing.get_course_point(course['courseId'], course['clazzId'], course['cpi'])
    if not isinstance(point_list, dict) or not isinstance(point_list.get('points'), list):
        raise ValueError('课程章节响应格式错误')
    tasks = [ChapterTask(index=i, point=point) for i, point in enumerate(point_list['points'])]
    return JobProcessor(chaoxing, course, tasks, config).run()


def filter_courses(all_course, course_list, *, interactive=False):
    """Validate API selections; interactive selection is explicitly CLI-only."""
    if not course_list:
        if not interactive:
            raise InputFormatError('请选择至少一门有效课程')
        print("*" * 10 + "课程列表" + "*" * 10)
        for course in all_course:
            print(f"ID: {course['courseId']} 课程名: {course['title']}")
        print("*" * 28)
        try:
            course_list = input(
                "请输入想要学习的课程列表,以逗号分隔,例: 2151141,189191,198198（直接回车选择全部）\n"
            ).split(',')
            if not any(item.strip() for item in course_list):
                course_list = [str(course['courseId']) for course in all_course]
        except Exception as exc:
            raise InputFormatError('输入格式错误') from exc

    if not isinstance(course_list, (list, tuple)) or not course_list:
        raise InputFormatError('请选择至少一门有效课程')
    if any(isinstance(item, bool) or not isinstance(item, (str, int)) for item in course_list):
        raise InputFormatError('课程 ID 格式错误')
    selected_ids = {str(item).strip() for item in course_list}
    available_ids = {str(course['courseId']) for course in all_course}
    unknown_ids = selected_ids - available_ids
    if unknown_ids:
        raise InputFormatError('课程选择已失效，请重新选择: ' + ', '.join(sorted(unknown_ids)))

    course_task = []
    course_ids = set()
    for course in all_course:
        course_id = str(course['courseId'])
        if course_id in selected_ids and course_id not in course_ids:
            course_task.append(course)
            course_ids.add(course_id)
    if not course_task:
        raise InputFormatError('没有可学习的课程')
    return course_task


def format_time(num, suffix='', divisor=''):
    total_time = round(num)
    sec = total_time % 60
    mins = (total_time % 3600) // 60
    hrs = total_time // 3600

    if hrs > 0:
        return f"{hrs:02d}:{mins:02d}:{sec:02d}"

    return f"{mins:02d}:{sec:02d}"


def main():
    """主程序入口"""
    chaoxing = None
    notification = None
    try:
        # 初始化配置
        common_config, tiku_config, notification_config = init_config()
        
        # 强制播放按照配置文件调节
        common_config["speed"] = min(2.0, max(1.0, common_config.get("speed", 1.0)))
        common_config["notopen_action"] = common_config.get("notopen_action", "retry")
        
        # 初始化超星实例
        chaoxing = init_chaoxing(common_config, tiku_config)
        
        # 设置外部通知
        notification = Notification()
        notification.config_set(notification_config)
        notification = notification.get_notification_from_config()
        notification.init_notification()
        
        # 检查当前登录状态
        _login_state = chaoxing.login(login_with_cookies=common_config.get("use_cookies", False))
        if not _login_state["status"]:
            raise LoginError(_login_state["msg"])
        
        # 获取所有的课程列表
        all_course = chaoxing.get_course_list()
        
        # 过滤要学习的课程
        course_task = filter_courses(all_course, common_config.get("course_list"), interactive=True)
        
        # 开始学习
        logger.info(f"课程列表过滤完毕, 当前课程任务数量: {len(course_task)}")
        results = [process_course(chaoxing, course, common_config) for course in course_task]
        failed = sum(len(result.failed) for result in results)
        skipped = sum(len(result.skipped) for result in results)
        if failed or skipped:
            message = f"课程处理结束：{failed} 个章节失败，{skipped} 个章节跳过"
            logger.warning(message)
            notification.send(f"chaoxing : {message}")
            return 1
        
        logger.info("所有课程学习任务已完成")
        notification.send("chaoxing : 所有课程学习任务已完成")
        return 0
        
    except SystemExit as e:
        if e.code != 0:
            logger.error(f"错误: 程序异常退出, 返回码: {e.code}")
        sys.exit(e.code)
    except KeyboardInterrupt as e:
        logger.error(f"错误: 程序被用户手动中断, {e}")
        return 130
    except BaseException as e:
        logger.error(f"错误: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
        try:
            notification.send(f"chaoxing : 出现错误 {type(e).__name__}: {e}\n{traceback.format_exc()}")
        except Exception:
            pass  # 如果通知发送失败，忽略异常
        raise e
    finally:
        if chaoxing is not None:
            chaoxing.close()


if __name__ == "__main__":
    sys.exit(main())
