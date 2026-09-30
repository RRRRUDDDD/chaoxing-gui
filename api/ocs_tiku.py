"""OCS 风格题库配置。

配置是一个 JSON 数组，或返回该数组的 http(s) 订阅链接。
格式对齐 https://docs.ocsjs.com/docs/other/api#AnswererWrapper 。
handler 只支持文档中的常见表达式，不执行任意脚本。
"""
from __future__ import annotations

import json
import warnings
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import InsecureRequestWarning

from api.logger import logger

_MAX_WRAPPERS = 20
_MAX_HANDLER_LENGTH = 8000
_MAX_SUBSCRIPTION_BYTES = 512_000
_PLACEHOLDERS = ("title", "options", "type")


class HandlerSyntaxError(ValueError):
    pass


def question_env(q_info: Mapping[str, Any]) -> dict[str, str]:
    options = q_info.get("options")
    if isinstance(options, list):
        options_text = "\n".join(str(item) for item in options)
    elif options is None:
        options_text = ""
    else:
        options_text = str(options)
    return {
        "title": str(q_info.get("title") or ""),
        "type": str(q_info.get("type") or ""),
        "options": options_text,
    }


def substitute(value: str, env: Mapping[str, str]) -> str:
    for key in _PLACEHOLDERS:
        value = value.replace("${" + key + "}", env.get(key, ""))
    return value


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
    if session.verify:
        return session.request(method, url, **kwargs)
    # Silence only the requests the user opted out of checking.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", InsecureRequestWarning)
        return session.request(method, url, **kwargs)


def fetch_subscription(url: str, session: requests.Session) -> Any:
    checked = _check_http_url(url)
    response = _send(session, "get", checked, timeout=20)
    response.raise_for_status()
    if len(response.content) > _MAX_SUBSCRIPTION_BYTES:
        raise ValueError("题库订阅内容过大")
    return response.json()


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
    return [normalize_wrapper(item) for item in raw]


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
            compiled_data[key] = compile_handler(source)
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
        "headers": dict(headers),
        "data": compiled_data,
        "run": compile_handler(handler),
    }


def resolve_data(data: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    resolved = {}
    for key, value in data.items():
        if callable(value):
            resolved[key] = value(env)
        elif isinstance(value, str):
            resolved[key] = substitute(value, env)
        else:
            resolved[key] = value
    return resolved


def request_wrapper(wrapper: Mapping[str, Any], env: Mapping[str, str], session: requests.Session) -> Any:
    headers = {key: substitute(value, env) for key, value in wrapper["headers"].items()}
    data = resolve_data(wrapper["data"], env)
    url = substitute(wrapper["url"], env)
    _check_http_url(url)
    if wrapper["method"] == "get":
        response = _send(session, "get", url, params=data or None, headers=headers, timeout=30)
    elif wrapper["content_type"] == "json":
        response = _send(session, "post", url, json=data, headers=headers, timeout=30)
    else:
        response = _send(session, "post", url, data=data, headers=headers, timeout=30)
    response.raise_for_status()
    if wrapper["content_type"] == "json":
        return response.json()
    return response.text


def _pairs(result: Any) -> list[tuple[Any, Any]]:
    if not isinstance(result, list):
        return []
    if result and all(isinstance(item, list) for item in result):
        return [(item[0] if item else None, item[1] if len(item) > 1 else None) for item in result]
    question = result[0] if result else None
    answer = result[1] if len(result) > 1 else None
    return [(question, answer)]


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

    def parse(self) -> Callable[[Any], Any]:
        self._skip()
        if not self._consume_word("return"):
            raise HandlerSyntaxError("handler 必须以 return 开头")
        self._skip()
        name = self._parse_param()
        self._skip()
        if not self._consume("=>"):
            raise HandlerSyntaxError("handler 缺少 =>")
        expression = self._parse_ternary()
        self._skip()
        if self.index != self.length:
            raise HandlerSyntaxError("handler 存在无法识别的内容")

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
                raise HandlerSyntaxError("参数缺少 )")
            self.index += 1
            return name
        return self._ident()

    def _parse_ternary(self) -> Callable:
        condition = self._parse_or()
        self._skip()
        if self._peek() != "?":
            return condition
        self.index += 1
        yes = self._parse_ternary()
        self._skip()
        if self._peek() != ":":
            raise HandlerSyntaxError("三元表达式缺少 :")
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
                    raise HandlerSyntaxError("下标缺少 ]")
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
                raise HandlerSyntaxError("map 缺少 =>")
            body = self._parse_ternary()
            self._skip()
            if self._peek() != ")":
                raise HandlerSyntaxError("map 缺少 )")
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
            raise HandlerSyntaxError("函数调用缺少 )")
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
        if char == "(":
            self.index += 1
            expression = self._parse_ternary()
            self._skip()
            if self._peek() != ")":
                raise HandlerSyntaxError("表达式缺少 )")
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
            raise HandlerSyntaxError("数组缺少 ]")
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
        raise HandlerSyntaxError("字符串没有结束")

    def _parse_number(self) -> int | float:
        start = self.index
        if self._peek() == "-":
            self.index += 1
        if not self._peek().isdigit():
            raise HandlerSyntaxError("数字格式错误")
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
            raise HandlerSyntaxError("缺少标识符")
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
    return _Parser(source).parse()

from difflib import SequenceMatcher

from api.answer import Tiku


def _similarity(question: Any, title: str) -> float:
    if not question:
        return 0.0
    return SequenceMatcher(None, str(question), title).ratio()


def select_answer(result: Any, title: str = "") -> str | None:
    if isinstance(result, str) and result.strip():
        return result.strip()
    answers = []
    for question, answer in _pairs(result):
        if answer is None:
            if isinstance(question, str) and question.strip():
                logger.info(f"题库提示: {question.strip()}")
            continue
        text = _answer_text(answer)
        if text:
            answers.append((question, text))
    if not answers:
        return None
    if len(answers) == 1:
        return answers[0][1]
    return max(answers, key=lambda item: _similarity(item[0], title))[1]


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
        env = question_env(q_info)
        for wrapper in self.wrappers:
            try:
                payload = request_wrapper(wrapper, env, self._session)
                answer = select_answer(wrapper["run"](payload), env["title"])
            except requests.exceptions.SSLError:
                logger.error(ssl_error_message(wrapper["name"]))
                continue
            except Exception as exc:
                logger.error(f"{wrapper['name']} 搜题失败: {exc}")
                continue
            if answer:
                logger.info(f"从{wrapper['name']}获取候选答案")
                return answer
        return None
