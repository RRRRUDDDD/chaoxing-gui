"""OCS 风格题库配置。

配置是一个 JSON 数组，或返回该数组的 http(s) 订阅链接。
格式对齐 https://docs.ocsjs.com/docs/other/api#AnswererWrapper 。
handler 只支持文档中的常见表达式，不执行任意脚本。
"""
from __future__ import annotations

import json
import re
import sys
import time
import warnings
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import requests
from urllib3.exceptions import InsecureRequestWarning

from api.logger import logger
from api.answer_check import match_answer
from api.question_images import build_image_env, restore_image_answer, validate_images, MAX_TOTAL_BYTES
from api.session import get_current_session

_MAX_WRAPPERS = 20
_MAX_HANDLER_LENGTH = 8000
_MAX_SUBSCRIPTION_BYTES = 512_000
_PLACEHOLDERS = ("title", "options", "type", "suggestion_title", "suggestion_options", "images")
_MAX_NESTING = 40
_CONTEXT_AWARE_WARNINGS = bool(getattr(sys.flags, "context_aware_warnings", False))


class HandlerSyntaxError(ValueError):
    pass


class ResponseParseError(ValueError):
    pass


def question_env(q_info: Mapping[str, Any]) -> dict[str, Any]:
    options = q_info.get("options")
    if isinstance(options, list):
        options_text = "\n".join(str(item) for item in options)
    elif options is None:
        options_text = ""
    else:
        options_text = str(options)
    env = {
        "title": str(q_info.get("title") or ""),
        "type": str(q_info.get("type") or "unknown"),
        "options": options_text,
        "images": [],
        "suggestion_title": "",
        "suggestion_options": "",
    }

    # Extension values are caller-provided; do not invent provider-specific data.
    for key in ("suggestion_title", "suggestion_options", "images"):
        if key in q_info:
            env[key] = q_info[key]
    for key in ("suggestion_title", "suggestion_options"):
        if not isinstance(env[key], str):
            raise ValueError(f"{key} 必须是字符串")
    env["images"] = validate_images(env["images"])
    return env


def substitute(value: Any, env: Mapping[str, Any], *, encode: bool = False, depth: int = 0) -> Any:
    if depth > _MAX_NESTING:
        raise ValueError("题库参数嵌套过深")
    if isinstance(value, dict):
        return {key: substitute(item, env, encode=encode, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, env, encode=encode, depth=depth + 1) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match):
        key = match.group(1)
        if key not in _PLACEHOLDERS:
            raise ValueError("题库占位符不受支持或缺少上下文值")
        if key not in env:
            raise ValueError(f"题库占位符 {key} 缺少上下文值")
        resolved = env[key]
        if key == "images" and isinstance(resolved, list):
            resolved = ",".join(resolved)
        if not isinstance(resolved, str):
            raise ValueError("题库文本占位符的上下文值必须是字符串")
        return quote(resolved, safe="") if encode else resolved

    return re.sub(r"\$\{([^{}]*)\}", replace, value)


def _check_http_url(url: str) -> str:
    if not isinstance(url, str):
        raise ValueError("题库地址必须是字符串")
    cleaned = url.strip()
    parts = urlsplit(cleaned)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("题库地址只允许 http 或 https")
    if len(cleaned) > 2000:
        raise ValueError("题库地址过长")
    return cleaned


def verify_ssl_setting(conf: Mapping[str, Any]) -> bool:
    """Certificate checks default to on; only an explicit boolean turns them off."""
    value = conf.get("verify_ssl", True)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ValueError("verify_ssl 只能是 true 或 false")


def ssl_error_message(name: str) -> str:
    return f"题库 {name} 证书校验失败，如确认可信可在高级设置中关闭证书校验"


def _send(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    if session.verify or not _CONTEXT_AWARE_WARNINGS:
        # Python 3.11/3.13 catch_warnings changes global filters. Keep urllib3's
        # default warning rather than silencing unrelated concurrent requests.
        return session.request(method, url, **kwargs)
    # Newer context-aware runtimes can safely silence just this request.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        return session.request(method, url, **kwargs)


def fetch_subscription(url: str, session: requests.Session) -> Any:
    checked = _check_http_url(url)
    response = _send(session, "get", checked, timeout=20)
    try:
        response.raise_for_status()
        if len(response.content) > _MAX_SUBSCRIPTION_BYTES:
            raise ValueError("题库订阅内容过大")
        return response.json()
    finally:
        response.close()


def parse_config_text(text: str, session: requests.Session) -> Any:
    cleaned = text.strip()
    if not cleaned:
        return []
    if cleaned.startswith(("http://", "https://")):
        return fetch_subscription(cleaned, session)
    return json.loads(cleaned)


def load_wrappers(conf: Mapping[str, Any], session: requests.Session) -> list[dict[str, Any]]:
    if isinstance(conf.get("wrappers"), list):
        raw = conf.get("wrappers")
    elif isinstance(conf.get("subscription"), str) and conf.get("subscription").strip():
        raw = fetch_subscription(conf["subscription"], session)
    elif isinstance(conf.get("config"), str) and conf.get("config").strip():
        raw = parse_config_text(conf["config"], session)
    else:
        return []
    if not isinstance(raw, list):
        raise ValueError("题库配置必须是 JSON 数组")
    if len(raw) > _MAX_WRAPPERS:
        raise ValueError(f"题库配置最多 {_MAX_WRAPPERS} 个")
    wrappers = []
    for index, item in enumerate(raw, 1):
        try:
            wrappers.append(normalize_wrapper(item))
        except (ValueError, RecursionError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "配置嵌套过深"
            logger.warning(f"第 {index} 个题库配置已跳过: {message}")
    if raw and not wrappers:
        raise ValueError("所有题库配置均无效，请查看逐项诊断")
    return wrappers


def normalize_wrapper(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("每个题库配置都必须是对象")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("题库配置缺少 name")
    url = _check_http_url(item.get("url"))
    method = str(item.get("method") or "get").strip().lower()
    if method not in ("get", "post"):
        raise ValueError(f"{name} 的 method 只允许 get 或 post")
    request_type = item.get("type", "fetch")
    if request_type not in ("fetch", "GM_xmlhttpRequest"):
        raise ValueError(f"{name} 的 type 只允许 fetch 或 GM_xmlhttpRequest")
    content_type = str(item.get("contentType") or "json").strip().lower()
    if content_type not in ("json", "text"):
        raise ValueError(f"{name} 的 contentType 只允许 json 或 text")
    handler = item.get("handler")
    if not isinstance(handler, str) or not handler.strip():
        raise ValueError(f"{name} 缺少 handler")
    if len(handler) > _MAX_HANDLER_LENGTH:
        raise ValueError(f"{name} 的 handler 过长")
    data = item.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError(f"{name} 的 data 必须是对象")
    compiled_data = {}
    for key, value in data.items():
        if isinstance(value, dict) and "handler" in value:
            source = value["handler"]
            if not isinstance(source, str):
                raise ValueError(f"{key} 的 handler 必须是字符串")
            compiled_data[key] = _compile_field(source, name, f"data.{key}.handler")
        else:
            compiled_data[key] = value
    headers = item.get("headers") or {}
    if not isinstance(headers, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items()):
        raise ValueError(f"{name} 的 headers 必须是字符串字段")
    if len(headers) > 30:
        raise ValueError(f"{name} 的 headers 过多")
    return {
        "name": name.strip(),
        "url": url,
        "method": method,
        "content_type": content_type,
        "request_type": request_type,
        "uses_images": bool(re.search(r"\.images\b|\$\{(?:images|suggestion_title|suggestion_options)\}", json.dumps(data))),
        "headers": dict(headers),
        "secret_values": [value for key, value in {**data, **headers}.items()
                          if isinstance(value, str) and value and re.search("token|key|auth|password|secret", key, re.I)],
        "data": compiled_data,
        "run": _compile_field(handler, name, "handler"),
    }


def _compile_field(source: str, name: str, field: str) -> Callable:
    try:
        return compile_handler(source)
    except HandlerSyntaxError as exc:
        raise HandlerSyntaxError(f"题库 {name} 的 {field}: {exc}") from None


def resolve_data(data: Mapping[str, Any], env: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value(env) if callable(value) else substitute(value, env)
        for key, value in data.items()
    }


def request_wrapper(wrapper: Mapping[str, Any], env: Mapping[str, Any], session: requests.Session) -> Any:
    if wrapper["method"] == "get" and env.get("images"):
        if wrapper["uses_images"] or "${images}" in wrapper["url"]:
            raise ValueError("images_require_post")
    headers = {key: substitute(value, env) for key, value in wrapper["headers"].items()}
    data = resolve_data(wrapper["data"], env)
    url = substitute(wrapper["url"], env, encode=True)
    _check_http_url(url)
    if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > MAX_TOTAL_BYTES:
        raise ValueError("request_body_limit")
    if wrapper["method"] == "get" and "data:image/" in json.dumps(data):
        raise ValueError("images_require_post")
    if wrapper["method"] == "get":
        parts = urlsplit(url)
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key not in data]
        url = urlunsplit(parts._replace(query=urlencode(query)))
        response = _send(session, "get", url, params=data or None, headers=headers, timeout=30)
    else:
        content_type = next((value for key, value in headers.items() if key.lower() == "content-type"), "")
        form = wrapper["request_type"] == "GM_xmlhttpRequest" and content_type == "application/x-www-form-urlencoded"
        body = data if form else json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        response = _send(session, "post", url, data=body, headers=headers, timeout=30)
    try:
        response.raise_for_status()
        if wrapper["content_type"] == "json":
            try:
                return response.json()
            except ValueError:
                raise ResponseParseError("invalid_json_response") from None
        return response.text
    finally:
        response.close()


def normalize_results(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, list) or not result:
        return []
    rows = result if all(isinstance(item, list) for item in result) else [result]
    return [{
        "question": row[0] if row else None,
        "answer": row[1] if len(row) > 1 else None,
        "extra_data": row[2] if len(row) > 2 and isinstance(row[2], dict) else {},
    } for row in rows]


def _answer_text(answer: Any) -> str:
    if isinstance(answer, list):
        return "#".join(str(item).strip() for item in answer if item is not None and str(item).strip())
    if isinstance(answer, bool):
        return "true" if answer else "false"
    return str(answer).strip()


def _truthy(value: Any) -> bool:
    return value not in (None, False, 0, 0.0, "")


def _member(value: Any, prop: str) -> Any:
    if isinstance(value, dict):
        return value.get(prop)
    if prop == "length" and isinstance(value, (str, list)):
        return len(value)
    return None


def _index(value: Any, index: Any) -> Any:
    if isinstance(index, bool) or not isinstance(index, int):
        return None
    if isinstance(value, (list, str)):
        if -len(value) <= index < len(value):
            return value[index]
        return None
    if isinstance(value, dict):
        return value.get(str(index))
    return None


def _call(value: Any, prop: str, args: list[Any]) -> Any:
    if prop == "replace":
        if not isinstance(value, str) or len(args) != 2 or not all(isinstance(item, str) for item in args):
            raise ValueError("replace 只接受两个字符串参数")
        return value.replace(args[0], args[1], 1)
    if prop == "split":
        if not isinstance(value, str) or len(args) != 1 or not isinstance(args[0], str):
            raise ValueError("split 只接受一个字符串参数")
        return value.split(args[0]) if args[0] else list(value)
    if prop == "join":
        if not isinstance(value, list) or len(args) != 1 or not isinstance(args[0], str):
            raise ValueError("join 只接受一个字符串参数")
        return args[0].join("" if item is None else str(item) for item in value)
    if prop == "trim":
        if not isinstance(value, str) or args:
            raise ValueError("trim 不接受参数")
        return value.strip()
    raise ValueError(f"不支持的方法 {prop}")


class _Parser:
    def __init__(self, source: str):
        self.source = source
        self.length = len(source)
        self.index = 0
        self.depth = 0

    def _error(self, message: str) -> HandlerSyntaxError:
        return HandlerSyntaxError(f"{message}（字符位置 {self.index + 1}）")

    def parse(self) -> Callable[[Any], Any]:
        self._skip()
        if not self._consume_word("return"):
            raise self._error("handler 必须以 return 开头")
        self._skip()
        name = self._parse_param()
        self._skip()
        if not self._consume("=>"):
            raise self._error("handler 缺少 =>")
        expression = self._parse_ternary()
        self._skip()
        if self.index != self.length:
            raise self._error("handler 存在无法识别的内容")

        def run(argument: Any, name: str = name, expression: Callable = expression) -> Any:
            return expression({name: argument})

        return run

    def _parse_param(self) -> str:
        if self._peek() == "(":
            self.index += 1
            self._skip()
            name = self._ident()
            self._skip()
            if self._peek() != ")":
                raise self._error("参数缺少 )")
            self.index += 1
            return name
        return self._ident()

    def _parse_ternary(self) -> Callable:
        self.depth += 1
        try:
            if self.depth > _MAX_NESTING:
                raise self._error("表达式嵌套过深")
            return self._parse_conditional()
        finally:
            self.depth -= 1

    def _parse_conditional(self) -> Callable:
        condition = self._parse_or()
        self._skip()
        if self._peek() != "?":
            return condition
        self.index += 1
        yes = self._parse_ternary()
        self._skip()
        if self._peek() != ":":
            raise self._error("三元表达式缺少 :")
        self.index += 1
        no = self._parse_ternary()
        return lambda env, condition=condition, yes=yes, no=no: yes(env) if _truthy(condition(env)) else no(env)

    def _parse_or(self) -> Callable:
        left = self._parse_and()
        while self._consume("||"):
            right = self._parse_and()
            left = lambda env, left=left, right=right: left(env) or right(env)
        return left

    def _parse_and(self) -> Callable:
        left = self._parse_eq()
        while self._consume("&&"):
            right = self._parse_eq()
            left = lambda env, left=left, right=right: left(env) and right(env)
        return left

    def _parse_eq(self) -> Callable:
        left = self._parse_postfix()
        while True:
            self._skip()
            if self._consume("==="):
                op = "==="
            elif self._consume("!=="):
                op = "!=="
            elif self._consume("=="):
                op = "=="
            elif self._consume("!="):
                op = "!="
            else:
                break
            right = self._parse_postfix()
            left = lambda env, left=left, right=right, op=op: _compare(left(env), right(env), op)
        return left

    def _parse_postfix(self) -> Callable:
        expression = self._parse_primary()
        while True:
            self._skip()
            if self._peek() == ".":
                self.index += 1
                self._skip()
                prop = self._ident()
                self._skip()
                if self._peek() == "(":
                    expression = self._parse_call(expression, prop)
                else:
                    expression = lambda env, expression=expression, prop=prop: _member(expression(env), prop)
                continue
            if self._peek() == "[":
                self.index += 1
                index = self._parse_ternary()
                self._skip()
                if self._peek() != "]":
                    raise self._error("下标缺少 ]")
                self.index += 1
                expression = lambda env, expression=expression, index=index: _index(expression(env), index(env))
                continue
            return expression

    def _parse_call(self, target: Callable, prop: str) -> Callable:
        self.index += 1
        self._skip()
        if prop == "map":
            param = self._parse_param()
            self._skip()
            if not self._consume("=>"):
                raise self._error("map 缺少 =>")
            body = self._parse_ternary()
            self._skip()
            if self._peek() != ")":
                raise self._error("map 缺少 )")
            self.index += 1

            def run(env, target=target, param=param, body=body):
                items = target(env)
                if not isinstance(items, list):
                    raise ValueError("map 需要数组")
                output = []
                for item in items:
                    scope = dict(env)
                    scope[param] = item
                    output.append(body(scope))
                return output

            return run
        args = []
        if self._peek() != ")":
            while True:
                args.append(self._parse_ternary())
                self._skip()
                if self._peek() == ",":
                    self.index += 1
                    self._skip()
                    continue
                break
        if self._peek() != ")":
            raise self._error("函数调用缺少 )")
        self.index += 1

        def run(env, target=target, prop=prop, args=tuple(args)):
            return _call(target(env), prop, [arg(env) for arg in args])

        return run

    def _parse_primary(self) -> Callable:
        self._skip()
        char = self._peek()
        if char in ("'", '"'):
            value = self._parse_string()
            return lambda env, value=value: value
        if char == "[":
            return self._parse_array()
        if char == "{":
            return self._parse_object()
        if char == "(":
            self.index += 1
            expression = self._parse_ternary()
            self._skip()
            if self._peek() != ")":
                raise self._error("表达式缺少 )")
            self.index += 1
            return expression
        if char == "-" or char.isdigit():
            value = self._parse_number()
            return lambda env, value=value: value
        name = self._ident()
        constants = {"undefined": None, "null": None, "true": True, "false": False}
        if name in constants:
            value = constants[name]
            return lambda env, value=value: value
        return lambda env, name=name: env.get(name)

    def _parse_object(self) -> Callable:
        self.index += 1
        items = []
        self._skip()
        if self._consume("}"):
            return lambda env: {}
        while True:
            self._skip()
            key = self._parse_string() if self._peek() in ("'", '"') else self._ident()
            if not self._consume(":"):
                raise self._error("对象属性缺少 :，不支持简写、方法或语句块")
            items.append((key, self._parse_ternary()))
            if self._consume("}"):
                break
            if not self._consume(","):
                raise self._error("对象属性缺少 , 或 }")
            if self._consume("}"):
                break
        return lambda env, items=tuple(items): {key: value(env) for key, value in items}

    def _parse_array(self) -> Callable:
        self.index += 1
        items = []
        self._skip()
        if self._peek() == "]":
            self.index += 1
            return lambda env: []
        while True:
            items.append(self._parse_ternary())
            self._skip()
            if self._peek() == ",":
                self.index += 1
                self._skip()
                if self._peek() == "]":
                    self.index += 1
                    break
                continue
            if self._peek() == "]":
                self.index += 1
                break
            raise self._error("数组缺少 ]")
        return lambda env, items=tuple(items): [item(env) for item in items]

    def _parse_string(self) -> str:
        quote = self._peek()
        self.index += 1
        chars = []
        while self.index < self.length:
            char = self.source[self.index]
            self.index += 1
            if char == quote:
                return "".join(chars)
            if char == "\\":
                if self.index >= self.length:
                    break
                escaped = self.source[self.index]
                self.index += 1
                chars.append({"n": "\n", "r": "\r", "t": "\t", "\\": "\\", "'": "'", '"': '"', "/": "/"}.get(escaped, escaped))
                continue
            chars.append(char)
        raise self._error("字符串没有结束")

    def _parse_number(self) -> int | float:
        start = self.index
        if self._peek() == "-":
            self.index += 1
        if not self._peek().isdigit():
            raise self._error("数字格式错误")
        while self._peek().isdigit():
            self.index += 1
        if self._peek() == "." and self._peek_at(1).isdigit():
            self.index += 1
            while self._peek().isdigit():
                self.index += 1
            return float(self.source[start:self.index])
        return int(self.source[start:self.index])

    def _ident(self) -> str:
        self._skip()
        start = self.index
        char = self._peek()
        if not char or not (char.isalpha() or char in "_$"):
            raise self._error("此处需要属性名或变量名，可能使用了不支持的语法")
        self.index += 1
        while True:
            char = self._peek()
            if not char or not (char.isalnum() or char in "_$"):
                break
            self.index += 1
        return self.source[start:self.index]

    def _consume_word(self, word: str) -> bool:
        if not self.source.startswith(word, self.index):
            return False
        after = self.index + len(word)
        if after < self.length and (self.source[after].isalnum() or self.source[after] == "_"):
            return False
        self.index = after
        return True

    def _consume(self, text: str) -> bool:
        self._skip()
        if self.source.startswith(text, self.index):
            self.index += len(text)
            return True
        return False

    def _skip(self) -> None:
        while self.index < self.length and self.source[self.index].isspace():
            self.index += 1

    def _peek(self) -> str:
        return self.source[self.index] if self.index < self.length else ""

    def _peek_at(self, offset: int) -> str:
        position = self.index + offset
        return self.source[position] if position < self.length else ""


def _compare(left: Any, right: Any, op: str) -> bool:
    equal = left == right
    return equal if op in ("===", "==") else not equal


def compile_handler(source: str) -> Callable[[Any], Any]:
    if not isinstance(source, str) or len(source) > _MAX_HANDLER_LENGTH:
        raise HandlerSyntaxError("handler 为空或过长")
    parser = _Parser(source)
    try:
        return parser.parse()
    except RecursionError:
        raise parser._error("表达式过于复杂") from None

from difflib import SequenceMatcher

from api.answer import Tiku


def _similarity(question: Any, title: str) -> float:
    if not question:
        return 0.0
    return SequenceMatcher(None, str(question), title).ratio()


def _select_result(rows, title="", question=None, true_list=(), false_list=()):
    answers = []
    for row in rows:
        if row["answer"] is None:
            continue
        text = _answer_text(row["answer"])
        if not text or "data:image/" in text:
            continue
        if question and (question.get("options") or question.get("type") in ("completion", "judgement")):
            matched = match_answer(text, question, true_list, false_list)
            if matched.answer is None:
                continue
        answers.append({**row, "answer": text})
    return max(answers, key=lambda row: _similarity(row["question"], title)) if answers else None


def select_answer(result: Any, title: str = "") -> str | None:
    if isinstance(result, str) and result.strip():
        return result.strip()
    selected = _select_result(normalize_results(result), title)
    return selected["answer"] if selected else None


def _safe_summary(value, secrets=()):
    text = str(value or "")
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"data:image/\S+|https?://\S+", "[redacted]", text)
    text = re.sub(r"(?i)(token|authorization|password|api_key)\s*[:=]\s*\S+", "[redacted]", text)
    return text[:256]


def _result_summary(row, secrets=()):
    extra = row.get("extra_data", {})
    tags = extra.get("tags", [])
    return {"question": _safe_summary(row["question"], secrets), "answer": _safe_summary(row["answer"], secrets),
            "ai": extra.get("ai") if isinstance(extra.get("ai"), bool) else None,
            "tags": [_safe_summary(tag, secrets) for tag in tags[:10] if isinstance(tag, str)] if isinstance(tags, list) else []}


class TikuOcs(Tiku):
    def __init__(self) -> None:
        super().__init__()
        self.name = "OCS题库"
        self.wrappers: list[dict[str, Any]] = []
        # One session reuses connections across questions; Tiku.close() closes it.
        self._session = requests.Session()

    def _init_tiku(self) -> None:
        conf = self._conf or {}
        try:
            self._session.verify = verify_ssl_setting(conf)
            if not self._session.verify:
                logger.warning("已关闭题库 HTTPS 证书校验，请确认题库地址可信")
            self.wrappers = load_wrappers(conf, self._session)
        except requests.exceptions.SSLError:
            logger.error(f"{ssl_error_message('订阅')}，已忽略题库功能")
            self.wrappers = []
            self.DISABLE = True
            return
        except (ValueError, requests.RequestException, json.JSONDecodeError) as exc:
            logger.error(f"题库配置无效，已忽略题库功能: {exc}")
            self.wrappers = []
            self.DISABLE = True
            return
        if not self.wrappers:
            logger.info("未配置题库，已忽略题库功能")
            self.DISABLE = True

    def _query(self, q_info: dict) -> str | None:
        self.query_diagnostics = []
        try:
            env = question_env(q_info)
        except (ValueError, OSError) as exc:
            self.query_diagnostics.append({"stage": "context", "status": "invalid_context", "error": type(exc).__name__})
            return None
        image_env = None
        image_urls = []
        for wrapper in self.wrappers:
            started = time.monotonic()
            report = {"source": _safe_summary(wrapper["name"], wrapper["secret_values"]), "stage": "context", "status": "pending", "candidates": []}
            self.query_diagnostics.append(report)
            try:
                wrapper_env = env
                active_urls = []
                if wrapper["uses_images"] and wrapper["method"] == "get" and q_info.get("_image_context"):
                    report["image_warnings"] = ["images_require_post"]
                if wrapper["uses_images"] and wrapper["method"] == "post" and "images" not in q_info:
                    if image_env is None:
                        image_env, image_urls, failures = build_image_env(q_info, get_current_session())
                    else:
                        failures = []
                    report["image_warnings"] = failures
                    active_urls = image_urls
                    wrapper_env = {**env, **{key: value for key, value in image_env.items() if key not in q_info}}
                report["stage"] = "request"
                payload = request_wrapper(wrapper, wrapper_env, self._session)
                report["stage"] = "handler"
                result = wrapper["run"](payload)
                rows = normalize_results(result)
                if isinstance(result, str) and result.strip():
                    rows = [{"question": None, "answer": result, "extra_data": {}}]
                for row in rows:
                    row["answer"] = restore_image_answer(row["answer"], active_urls)
                report["candidates"] = [_result_summary(row, wrapper["secret_values"]) for row in rows[:20]]
                report["stage"] = "match"
                selected = _select_result(rows, env["title"], q_info, self.true_list, self.false_list)
                if selected:
                    report["status"] = "selected"
                    report["selected"] = _result_summary(selected, wrapper["secret_values"])
                    logger.info(f"从{report['source']}获取候选答案")
                    return selected["answer"]
                report["status"] = "unmatched" if any(row["answer"] for row in rows) else "no_answer"
            except requests.exceptions.SSLError:
                report["status"] = "ssl_error"
                logger.error(ssl_error_message(report["source"]))
            except requests.Timeout:
                report["status"] = "timeout"
            except requests.HTTPError as exc:
                code = getattr(exc.response, "status_code", None)
                report["status"] = "authentication_rejected" if code in (401, 403) else "http_error"
                report["http_status"] = code
            except (ResponseParseError, requests.exceptions.JSONDecodeError):
                report["status"] = "response_parse_error"
            except requests.RequestException:
                report["status"] = "network_error"
            except Exception as exc:
                report["status"] = {"context": "invalid_context", "request": "request_or_response_error",
                                    "handler": "handler_error", "match": "matching_error"}[report["stage"]]
                report["error"] = type(exc).__name__
            finally:
                report["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                if report["status"] != "selected":
                    logger.info("题库 {}: {}", report["source"], report["status"])
        return None
