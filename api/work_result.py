"""Read-only work-result inspection; never retries or changes a submission."""

import math
import re

from bs4 import BeautifulSoup

from api.captcha import is_captcha_response
from api.logger import logger
from api.session import HTTP_TIMEOUT

_RECORDS = 'https://mooc1.chaoxing.com/mooc-ans/work/record-list'
_DETAIL = 'https://mooc1.chaoxing.com/mooc-ans/work/record-detail'
# 平台在打开作业页时就以 0 分占位创建作答记录，提交后原地更新同一条记录的成绩
# （2026-10-04 实测：22:22:09.687 提交成功，22:22:10 该记录即为 100 分，序号不变）。
# 因此判定本次提交不能只看新增序号，还要比对已有记录的分数变化。
_RECHECK_ATTEMPTS = 3
_RECHECK_INTERVALS = (4.0, 8.0)


def parse_records(text):
    soup = BeautifulSoup(text, 'lxml')
    records = {}
    for number in soup.select('.viewNum'):
        match = re.fullmatch(r'第\s*(\d+)\s*次', number.get_text(strip=True))
        if not match:
            raise ValueError('作答序号无法解析')
        container = number.parent
        score_node = None
        while container is not None:
            if len(container.select('.viewNum')) != 1:
                break
            candidates = container.select('.viewScore')
            if candidates:
                if len(candidates) != 1:
                    raise ValueError('作答成绩不唯一')
                score_node = candidates[0]
                break
            container = container.parent
        score = None
        if score_node is not None:
            value = re.fullmatch(r'(\d+(?:\.\d+)?)\s*分', score_node.get_text(strip=True))
            if value:
                parsed = float(value[1])
                score = parsed if math.isfinite(parsed) else None
        times = int(match[1])
        if times in records:
            raise ValueError('重复的作答记录')
        records[times] = score
    if not records and not re.search(r'暂无(?:作答|答题|提交)?记录|没有作答记录', soup.get_text()):
        raise ValueError('未能确认作答记录列表')
    return records


def parse_detail(text):
    soup = BeautifulSoup(text, 'lxml')
    rows = []
    for node in soup.select('.TiMu.singleQuesId'):
        answers = []
        for label in ('我的答案', '正确答案'):
            element = next((span for span in node.find_all('span')
                            if re.fullmatch(label + r'\s*[:：]', span.get_text(strip=True))), None)
            answer = element.find_next_sibling(['div', 'span']) if element is not None else None
            answers.append(answer.get_text(' ', strip=True) if answer is not None else None)
        # Equality of displayed strings is informative, not a grading oracle.
        rows.append({'id': node.get('data', ''), 'parse_ok': all(a is not None for a in answers),
                     'same_text': answers[0] == answers[1] if all(a is not None for a in answers) else None})
    return rows


class WorkResultReader:
    def __init__(self, session, course, form, *, cancelled, wait, rate_limit):
        self.session = session
        self.cancelled = cancelled
        self.wait = wait
        self.rate_limit = rate_limit
        self.params = {
            'courseId': str(course.get('courseId', '')), 'classId': str(course.get('clazzId', '')),
            'cpi': str(course.get('cpi', '')), 'workId': str(form.get('workId') or form.get('workRelationId') or ''),
            'workAnswerId': str(form.get('workAnswerId') or ''), 'api': '1', 'mooc2': '1', 'ut': 's',
        }
        self.baseline = None

    def _read(self, url, **extra):
        if self.cancelled():
            raise InterruptedError('已停止成绩回查')
        self.rate_limit()
        if self.cancelled():
            raise InterruptedError('已停止成绩回查')
        response = self.session.get(url, params={**self.params, **extra}, timeout=HTTP_TIMEOUT,
                                    allow_redirects=False)
        try:
            if response.status_code != 200 or is_captcha_response(response):
                raise ValueError('成绩页面不可用或需要验证')
            if self.cancelled():
                raise InterruptedError('已停止成绩回查')
            return response.text
        finally:
            response.close()

    def capture_baseline(self):
        if not all(self.params[key] for key in ('courseId', 'classId', 'workId', 'workAnswerId')):
            return
        try:
            self.baseline = parse_records(self._read(_RECORDS))
            logger.debug('提交前作答记录基线：{}', dict(sorted(self.baseline.items())))
        except Exception as exc:
            logger.debug('提交前作答记录不可用: {}: {}', type(exc).__name__, exc)

    def check(self):
        result = {'submitted': True, 'result_status': 'unknown', 'score': None,
                  'unknown': None, 'reason': '无法确认本次提交对应的作答记录'}
        if self.cancelled():
            return {**result, 'result_status': 'cancelled', 'reason': '已停止成绩回查'}
        if self.baseline is None:
            return result
        saw_matched_record = False
        for attempt in range(_RECHECK_ATTEMPTS):
            try:
                records = parse_records(self._read(_RECORDS))
            except InterruptedError:
                return {**result, 'result_status': 'cancelled', 'reason': '已停止成绩回查'}
            except Exception as exc:
                result['reason'] = '成绩回查失败: ' + type(exc).__name__
                break
            new = set(records) - set(self.baseline)
            updated = {times for times, score in records.items()
                       if times in self.baseline and self.baseline[times] != score}
            logger.debug('成绩回查第{}/{}次：平台共{}条记录，新增{}，更新{}，对应成绩{}',
                         attempt + 1, _RECHECK_ATTEMPTS, len(records), sorted(new),
                         sorted(updated), [records[times] for times in sorted(new | updated)])
            # More than one changed record could be a concurrent submission elsewhere.
            if len(new) + len(updated) > 1:
                result['reason'] = '发现{}条与本次提交相关的作答记录，无法确认对应关系'.format(
                    len(new) + len(updated))
                break
            if new or updated:
                times = next(iter(new | updated))
                score = records[times]
                if score is not None:
                    result.update(result_status='confirmed', score=score,
                                  reason='已读取本次提交后新增记录的成绩' if new
                                  else '已读取本次提交更新的作答记录成绩')
                    try:
                        rows = parse_detail(self._read(_DETAIL, times=str(times), isdisplaytable='0',
                                                       firstHeader='2', isWork='false', workSystem='0', archive='false'))
                        result['unknown'] = sum(not row['parse_ok'] for row in rows) if rows else None
                    except InterruptedError:
                        return {**result, 'result_status': 'cancelled', 'reason': '已停止成绩回查'}
                    except Exception:
                        result['reason'] += '；逐题详情未能确认'
                    return result
                saw_matched_record = True
            if attempt < _RECHECK_ATTEMPTS - 1 and self.wait(_RECHECK_INTERVALS[attempt]):
                return {**result, 'result_status': 'cancelled', 'reason': '已停止成绩回查'}
        if saw_matched_record:
            result['reason'] = '已定位本次提交对应的作答记录，成绩尚未生成'
        elif result['reason'] == '无法确认本次提交对应的作答记录':
            result['reason'] = '未见本次提交引起的作答记录变化'
        return result
