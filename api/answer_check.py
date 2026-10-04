import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher


def check_single(answer):
    _t = cut(answer)
    if _t is not None and len(_t) == 1:
        return True
    else:
        return False


def check_multiple(answer):
    _t = cut(answer)
    if _t is not None and len(_t) > 0:
        return True
    return False


def check_judgement(answer, true_list, false_list):
    if answer in true_list:
        return 1
    elif answer in false_list:
        return 0
    else:
        return -1


def check_completion(answer):
    if len(answer) > 0:
        return True
    else:
        return False


def check_answer(answer, type, tiku):  # 只会写小杯代码，这里用个tiku感觉怪怪的，但先这么写着
    if type == 'single':
        if check_single(answer) and check_judgement(answer, tiku.true_list, tiku.false_list) == -1:
            return True
    elif type == 'multiple':
        if check_multiple(answer) and check_judgement(answer, tiku.true_list, tiku.false_list) == -1:
            return True
    elif type == 'completion':
        if check_completion(answer):
            return True
    elif type == 'judgement':
        if check_judgement(answer, tiku.true_list, tiku.false_list) != -1:
            return True
    else:  # 未知类型不匹配
        return True
    return False


def cut(answer):
    cut_char = [
        "\n",
        ",",
        "，",
        "|",
        "\r",
        "\t",
        "#",
        "*",
        "-",
        "_",
        "+",
        "@",
        "~",
        "/",
        "\\",
        ".",
        "&",
        " ",
        "、",
    ]
    if answer is None:
        return None

    answer = str(answer)
    for char in cut_char:
        if char not in answer:
            continue
        res = [opt.strip() for opt in answer.split(char) if opt.strip()]
        if res:
            return res
    stripped = answer.strip()
    return [stripped] if stripped else None


# Query candidate selection and form filling share these deterministic resolvers.


@dataclass
class AnswerMatch:
    answer: str | None = None
    reason: str = 'unmatched'
    fields: dict[str, str] = field(default_factory=dict)


def split_answers(answer, kind='multiple', expected=None):
    if isinstance(answer, list):
        return [str(item).strip() for item in answer if item is not None and str(item).strip()]
    text = str(answer or '').strip()
    if not text:
        return []
    if kind not in ('multiple', 'completion'):
        return [text]
    try:
        values = json.loads(text)
        if isinstance(values, list) and all(isinstance(v, (str, int, float)) for v in values):
            return [str(v).strip() for v in values if str(v).strip()]
    except (ValueError, TypeError):
        pass
    if expected == 1:
        return [text]
    # Code and formula punctuation are data, never generic delimiters.
    if re.search(r'\b(?:return|def|class|function|printf|import)\b|[{}]|#include', text):
        return [text]
    pattern = r'###|===|---|#|\r?\n|[;；]'
    if kind == 'multiple':
        pattern += r'|[|,，]'
    return [part.strip() for part in re.split(pattern, text) if part.strip()]


def parse_options(options):
    lines = options if isinstance(options, list) else str(options or '').splitlines()
    parsed = []
    for line in lines:
        found = re.fullmatch(r'\s*([A-Z])(?:[.．、:：)）]\s*|\s+)(.+?)\s*', str(line))
        if not found:
            return []
        parsed.append(found.groups())
    if len({label for label, _ in parsed}) != len(parsed):
        return []
    return parsed


def _normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFC', text)).strip()


def _semantic_marks(text):
    return re.findall(r'不|非|无|未|错误|正确|不能|可以|\b(?:not|no|never)\b|\d+(?:\.\d+)?|[+\-*/<>=!]', text)


def _match_text(text, options):
    normalized = _normalized(text)
    exact = [label for label, value in options if _normalized(value) == normalized]
    if exact:
        return exact[0] if len(exact) == 1 else None
    # Short strings, formulas and image URLs require exact matches.
    if len(normalized) < 10 or re.search(r'https?://|[+\-*/<>=!]', normalized):
        return None
    scores = sorted(((SequenceMatcher(None, normalized, _normalized(value)).ratio(), label)
                     for label, value in options if _semantic_marks(text) == _semantic_marks(value)), reverse=True)
    if not scores or scores[0][0] < 0.85:
        return None
    if len(scores) > 1 and scores[0][0] - scores[1][0] < 0.10:
        return None
    return scores[0][1]


def _match_choices(text, question):
    options = parse_options(question.get('options'))
    if not options:
        return AnswerMatch(reason='invalid_options')
    exact = [label for label, value in options if _normalized(value) == _normalized(text)]
    if exact:
        return AnswerMatch(exact[0], 'exact') if len(exact) == 1 else AnswerMatch(reason='ambiguous')
    plain = re.sub(r'^(?:答案|正确答案)\s*[:：]\s*', '', text).strip()
    if re.fullmatch(r'[A-Z](?:[A-Z]|[\s,，、#;；]+[A-Z])*', plain):
        letters = re.findall('[A-Z]', plain)
        available = {label for label, _ in options}
        if set(letters) <= available and (question['type'] == 'multiple' or len(set(letters)) == 1):
            return AnswerMatch(''.join(label for label, _ in options if label in letters), 'letters')
    parts = split_answers(text, question['type'])
    available = {label for label, _ in options}
    if parts and all(part in available for part in parts):
        if question['type'] == 'multiple' or len(set(parts)) == 1:
            return AnswerMatch(''.join(label for label, _ in options if label in parts), 'letters')
    selected = [_match_text(part, options) for part in parts]
    if not selected or any(label is None for label in selected):
        return AnswerMatch(reason='ambiguous_or_unmatched')
    if question['type'] == 'single' and len(set(selected)) != 1:
        return AnswerMatch(reason='ambiguous')
    return AnswerMatch(''.join(label for label, _ in options if label in selected), 'text')


def match_answer(answer, question, true_list=(), false_list=()):
    text = str(answer or '').strip()
    if not text:
        return AnswerMatch(reason='empty')
    kind = question.get('type')
    if kind in ('single', 'multiple'):
        return _match_choices(text, question)
    if kind == 'judgement':
        value = check_judgement(text, true_list, false_list)
        return AnswerMatch('true' if value == 1 else 'false', 'judgement') if value != -1 else AnswerMatch(reason='unknown_judgement')
    if kind == 'completion':
        prefix = 'answer' + str(question.get('id', '')) + '_'
        keys = [key for key in question.get('answerField', {}) if key.startswith(prefix) and key[len(prefix):].isdigit()]
        keys.sort(key=lambda key: int(key[len(prefix):]))
        if keys:
            parts = split_answers(answer, kind, len(keys))
            if len(parts) != len(keys):
                return AnswerMatch(reason='blank_count_mismatch')
            return AnswerMatch(text, 'completion', dict(zip(keys, parts)))
    return AnswerMatch(text, 'text')
