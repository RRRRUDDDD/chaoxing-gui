"""Read-only reconciliation of platform task completion, not answer correctness."""

from api.course_tools import CourseTools, ToolCancelled
from api.decode import decode_job_progress, is_live_card
from api.logger import logger
from api.privacy import redact

_KINDS = frozenset({'video', 'audio', 'document', 'doc', 'read', 'work', 'live'})


def _boolean(value):
    if value is True or type(value) is int and value == 1 or isinstance(value, str) and value.lower() in {'true', '1'}:
        return True
    if value is False or type(value) is int and value == 0 or isinstance(value, str) and value.lower() in {'false', '0'}:
        return False
    return None


def verify_course(chaoxing, course, cancel_check=None):
    report = {'status': 'unknown', 'progress': None, 'chapters_checked': 0,
              'tasks_checked': 0, 'pending': 0, 'reason': '平台完成状态未确认'}
    tools = CourseTools(chaoxing, cancel_check, read_only=True)
    try:
        tools._check_cancelled()
        before = tools._course_page(course)
        chapters = tools._chapters(before)
        baseline = decode_job_progress(before)
        if not chapters or baseline is None:
            raise ValueError('缺少明确章节清单或平台总任务进度')
        states = {}
        for chapter in chapters:
            for page, data in tools.iter_card_pages(course, chapter, strict=True):
                for card in data.get('attachments', []):
                    tools._check_cancelled()
                    if not isinstance(card.get('property', {}), dict):
                        raise ValueError('附件属性格式异常')
                    job_id = tools._job_id(card)
                    is_task = _boolean(card.get('job'))
                    if is_task is None and 'job' in card:
                        raise ValueError('任务标记无法识别')
                    # Passed cards can lose the job flag but retain their job ID.
                    if not is_task and not job_id:
                        continue
                    kind = 'live' if is_live_card(card) else str(card.get('type', '')).lower()
                    if kind not in _KINDS:
                        raise ValueError('存在未支持的任务类型')
                    identity = job_id or tools._object_id(card) or str(card.get('id') or '')
                    if not identity:
                        raise ValueError('任务缺少稳定标识')
                    passed = _boolean(card.get('isPassed'))
                    if passed is None:
                        raise ValueError('任务缺少明确完成状态')
                    key = (chapter['id'], kind, identity)
                    if key in states and states[key] != passed:
                        raise ValueError('重复任务的完成状态不一致')
                    states[key] = passed
            report['chapters_checked'] += 1
        tools._check_cancelled()
        after = tools._course_page(course)
        progress = decode_job_progress(after)
        report.update(progress=progress, tasks_checked=len(states), pending=sum(not v for v in states.values()))
        if baseline != progress or chapters != tools._chapters(after):
            raise ValueError('复核期间平台进度或章节清单发生变化')
        if len(states) != progress['total'] or sum(states.values()) != progress['done']:
            raise ValueError('原始任务附件与平台总进度不一致')
        tools._check_cancelled()
        if report['pending']:
            report.update(status='pending', reason='平台仍有未完成任务点')
        else:
            report.update(status='confirmed', reason='平台总进度与全部任务卡片均已确认完成')
    except ToolCancelled:
        report.update(status='cancelled', reason='已停止平台复核')
    except (RuntimeError, ValueError) as exc:
        report['reason'] = redact(exc)[:300]
    except Exception as exc:
        report['reason'] = '平台复核读取失败: ' + type(exc).__name__
    logger.info('课程执行结束，平台复核 {}：{}', report['status'], report['reason'])
    return report
