from pathlib import Path
import json
import unittest
import warnings
import threading
from unittest.mock import Mock, patch

import requests
from urllib3.exceptions import InsecureRequestWarning

from api.answer import Tiku
from api.ocs_tiku import (
    HandlerSyntaxError,
    _CONTEXT_AWARE_WARNINGS,
    TikuOcs,
    compile_handler,
    load_wrappers,
    normalize_wrapper,
    normalize_results,
    question_env,
    request_wrapper,
    substitute,
    resolve_data,
    select_answer,
)


def _wrapper(**overrides):
    item = {
        "name": "示例题库",
        "url": "https://example.test/search",
        "method": "get",
        "contentType": "json",
        "data": {"question": "${title}", "kind": "${type}"},
        "handler": "return (res)=> res.code === 1 ? [res.question, res.answer] : undefined",
    }
    item.update(overrides)
    return item


class HandlerTests(unittest.TestCase):
    def test_yanxi_metadata_handler(self):
        config = _wrapper(
            name="言溪题库", homepage="https://tk.enncy.cn/",
            url="https://tk.enncy.cn/query", method="post", type="GM_xmlhttpRequest",
            headers={"Content-Type": "application/json"},
            data={"token": "", "title": "${title}", "options": "${options}",
                  "type": "${type}", "images": {"handler": "return (env) => env.images"},
                  "v": "2", "suggestion_title": "${suggestion_title}",
                  "suggestion_options": "${suggestion_options}"},
            handler="return (res)=>res.code === 0 ? [res.data.answer, undefined, { tags: res.data.tags }] : [res.data.question, res.data.answer, { ai: res.data.ai, tags: res.data.tags }]",
        )
        wrapper = normalize_wrapper(config)
        result = wrapper["run"]({"code": 1, "data": {
            "question": "题目", "answer": "答案", "ai": True, "tags": ["参考"],
        }})
        self.assertEqual(result, ["题目", "答案", {"ai": True, "tags": ["参考"]}])
        self.assertEqual(select_answer(result), "答案")
        missing = wrapper["run"]({"code": 0, "data": {"answer": "未找到", "tags": []}})
        self.assertEqual(missing, ["未找到", None, {"tags": []}])
        self.assertIsNone(select_answer(missing))
        tiku = TikuOcs()
        self.addCleanup(tiku.close)
        tiku.config_set({"wrappers": [config]})
        tiku.init_tiku()
        self.assertFalse(tiku.DISABLE)
        response = Mock()
        response.json.return_value = {"code": 1, "data": {"question": "题目", "answer": "答案"}}
        # Supplied extension values only: this does not claim a provider image schema.
        context = {"title": "题目", "options": ["A. 答案", "B. 其他"], "type": "single",
                   "suggestion_title": "调用方文本", "suggestion_options": "调用方选项", "images": []}
        with patch.object(tiku._session, "request", return_value=response) as send:
            self.assertEqual(tiku._query(context), "答案")
            data = json.loads(send.call_args.kwargs["data"])
            self.assertEqual(data["suggestion_title"], context["suggestion_title"])
            self.assertEqual(data["images"], context["images"])
            response.close.assert_called_once()
        with patch.object(tiku._session, "request", return_value=response) as send:
            self.assertEqual(tiku._query({"title": "题目"}), "答案")
            data = json.loads(send.call_args.kwargs["data"])
            self.assertEqual(data["images"], [])
            self.assertEqual(data["suggestion_title"], "")
            self.assertEqual(data["suggestion_options"], "")

        response.json.return_value = {'code': 0, 'data': {
            'answer': '题库次数余额不足，请前往 <a href="https://example.test/account">个人中心</a>',
            'tags': [{'text': '余额不足', 'color': 'red'}],
        }}
        with patch.object(tiku._session, 'request', return_value=response), patch('api.ocs_tiku.logger') as log:
            self.assertIsNone(tiku._query(context))
        report = tiku.query_diagnostics[0]
        self.assertEqual(report['status'], 'no_answer')
        self.assertEqual(report['message'], '题库次数余额不足，请前往 个人中心')
        log.error.assert_called_once_with('题库 {}: {}（题库提示：{}）', '言溪题库', 'no_answer', report['message'])

    def test_documented_handlers(self):
        single = compile_handler("return (res)=> res.code === 1 ? [res.question,res.answer] : undefined")
        self.assertEqual(single({"code": 1, "question": "中国梦是什么？", "answer": "复兴"}), ["中国梦是什么？", "复兴"])
        self.assertIsNone(single({"code": 0, "question": "x", "answer": "y"}))

        many = compile_handler("return (res)=> res.code === 1 ? res.results.map(r=>([r.question,r.answer])) : undefined")
        payload = {"code": 1, "results": [{"question": "题", "answer": "答"}]}
        self.assertEqual(many(payload), [["题", "答"]])

        missing = compile_handler("return (res)=>res.code === 1 ? [res.question,res.answer] : [res.msg, undefined]")
        self.assertEqual(missing({"code": 0, "msg": "搜索不到"}), ["搜索不到", None])

        only_answer = compile_handler("return (res)=> res.code === 1 ? [undefined, res.answer] : undefined")
        self.assertEqual(only_answer({"code": 1, "answer": "答案"}), [None, "答案"])

        indexed = compile_handler("return (res)=> res.code === 0 ? undefined : [res.data.title, res.data.answers[0]]")
        self.assertEqual(indexed({"code": 1, "data": {"title": "1+2", "answers": [3, 5]}}), ["1+2", 3])

    def test_field_handler_replaces_placeholder_context(self):
        env = {"title": "单选题中国梦", "options": "A. 一\nB. 二", "type": "single"}
        data = normalize_wrapper(_wrapper(data={
            "title": {"handler": "return (env)=> env.title.replace('单选题','')"},
            "question": "${title}",
        }))["data"]
        resolved = resolve_data(data, env)
        self.assertEqual(resolved["title"], "中国梦")
        self.assertEqual(resolved["question"], "单选题中国梦")

    def test_data_handlers_compile_once_and_fail_during_configuration(self):
        config = _wrapper(data={"question": {"handler": "return (env)=> env.title.trim()"}})
        with patch("api.ocs_tiku.compile_handler", wraps=compile_handler) as compile_once:
            wrapper = normalize_wrapper(config)
            self.assertEqual(compile_once.call_count, 2)  # request data plus response handler
            for title in (" first ", " second "):
                self.assertEqual(resolve_data(wrapper["data"], {"title": title}), {"question": title.strip()})
            self.assertEqual(compile_once.call_count, 2)
        self.assertIsInstance(config["data"]["question"], dict)
        for source in (None, 123, "invalid ??? script"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                normalize_wrapper(_wrapper(data={"question": {"handler": source}}))

    def test_rejects_arbitrary_code(self):
        with self.assertRaises(HandlerSyntaxError):
            compile_handler("return (res)=> __import__('os').system('echo hi')")

    def test_selects_closest_of_several_answers(self):
        result = [["完全不同的题目", "错误"], ["中国梦是什么", "正确"]]
        self.assertEqual(select_answer(result, "中国梦是什么？"), "正确")
        self.assertEqual(select_answer(["提示", None]), None)
        self.assertEqual(select_answer([None, ["甲", "乙"]]), "甲#乙")


class TikuOcsTests(unittest.TestCase):
    def test_config_text_queries_matching_bank_and_falls_through(self):
        config = json.dumps([
            _wrapper(name="空题库", url="https://empty.test/search", handler="return (res)=> undefined"),
            _wrapper(),
        ], ensure_ascii=False)
        tiku = Tiku()
        tiku.config_set({"config": config, "submit": "false", "cover_rate": "0.9"})
        loaded = tiku.get_tiku_from_config()
        self.assertIsInstance(loaded, TikuOcs)
        loaded.init_tiku()

        def fake_get(method, url, params=None, headers=None, timeout=None):
            response = Mock()
            response.raise_for_status.return_value = None
            if url.startswith("https://empty.test"):
                response.json.return_value = {}
            else:
                self.assertEqual(params["question"], "中国梦是什么")
                self.assertEqual(params["kind"], "single")
                response.json.return_value = {"code": 1, "question": "中国梦是什么", "answer": "复兴"}
            return response

        with patch.object(loaded._session, "request", side_effect=fake_get):
            answer = loaded._query({"title": "中国梦是什么", "type": "single", "options": "A. 复兴"})
        self.assertEqual(answer, "复兴")

    def test_subscription_and_invalid_config(self):
        payload = [_wrapper()]
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = b"[]"
        response.json.return_value = payload
        session = requests.Session()
        with patch.object(session, "request", return_value=response) as get:
            wrappers = load_wrappers({"subscription": "https://bank.test/ocs.json"}, session)
        self.assertEqual(wrappers[0]["name"], "示例题库")
        get.assert_called_once()
        session.close()

        broken = TikuOcs()
        broken.config_set({"config": "{not json"})
        broken.init_tiku()
        self.assertTrue(broken.DISABLE)
        self.assertIsNone(broken.query({"title": "题", "type": "single", "options": ""}))

    def test_fixed_provider_config_is_ignored(self):
        tiku = Tiku()
        tiku.config_set({"provider": "TikuYanxi", "tokens": "token-a"})
        loaded = tiku.get_tiku_from_config()
        self.assertIs(loaded, tiku)
        self.assertTrue(loaded.DISABLE)

    def test_single_punctuation_through_ocs_query_and_cache(self):
        import tempfile
        from api.answer import CacheDAO
        with tempfile.TemporaryDirectory() as directory:
            cache = CacheDAO(str(Path(directory) / 'cache.json'))
            bank = TikuOcs()
            self.addCleanup(bank.close)
            bank.config_set({'wrappers': [_wrapper()]})
            bank.init_tiku()
            answer = '坚持独立负责、不参与国际组织的活动'
            question = {'title': '单选顿号离线回归', 'type': 'single', 'options': 'A. ' + answer + chr(10) + 'B. 其他'}
            response = Mock()
            response.json.return_value = {'code': 1, 'question': question['title'], 'answer': answer}
            with (patch.object(bank._session, 'request', return_value=response),
                  patch.object(CacheDAO, 'get_shared', return_value=cache)):
                self.assertEqual(bank.query(question), answer)
                self.assertEqual(bank.query(question), answer)
                bank.close()

    def test_request_failure_tries_next_bank(self):
        tiku = TikuOcs()
        tiku.config_set({"wrappers": [
            _wrapper(name="失败", url="https://down.test/search"),
            _wrapper(name="成功", url="https://up.test/search"),
        ]})
        tiku.init_tiku()

        def fake_get(method, url, params=None, headers=None, timeout=None):
            if "down.test" in url:
                raise requests.ConnectionError("offline")
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"code": 1, "question": None, "answer": "B"}
            return response

        with patch.object(tiku._session, "request", side_effect=fake_get):
            self.assertEqual(tiku._query({"title": "题目", "type": "single", "options": ["A. 一", "B. 二"]}), "B")


class CertificateTests(unittest.TestCase):
    def bank(self, **conf):
        tiku = TikuOcs()
        tiku.config_set({"wrappers": [_wrapper()], **conf})
        tiku.init_tiku()
        self.addCleanup(tiku.close)
        return tiku

    def answer(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 1, "question": "题", "answer": "A"}
        return response

    def test_certificates_are_checked_by_default_and_one_session_is_reused(self):
        tiku = self.bank()
        self.assertFalse(tiku.DISABLE)
        self.assertTrue(tiku._session.verify)
        session = tiku._session
        with patch.object(session, "request", return_value=self.answer()) as send:
            tiku._query({"title": "题", "type": "single", "options": ""})
            tiku._query({"title": "题", "type": "single", "options": ""})
        self.assertEqual(send.call_count, 2)
        self.assertNotIn("verify", send.call_args.kwargs)
        self.assertIs(tiku._session, session)

    def test_explicit_opt_out_and_invalid_values(self):
        for value in (False, "false", " FALSE "):
            with self.subTest(value=value):
                self.assertFalse(self.bank(verify_ssl=value)._session.verify)
        self.assertTrue(self.bank(verify_ssl="true")._session.verify)
        for value in ("no", 0, None, "1"):
            with self.subTest(value=value):
                self.assertTrue(self.bank(verify_ssl=value).DISABLE)

    def test_opt_out_silences_only_its_own_requests(self):
        tiku = self.bank(verify_ssl=False)

        def insecure(*args, **kwargs):
            warnings.warn("unverified", InsecureRequestWarning)
            return self.answer()

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with patch.object(tiku._session, "request", side_effect=insecure):
                tiku._query({"title": "题", "type": "single", "options": ""})
            warnings.warn("other request", InsecureRequestWarning)
        expected = ["other request"] if _CONTEXT_AWARE_WARNINGS else ["unverified", "other request"]
        self.assertEqual([str(item.message) for item in caught], expected)

    def test_opt_out_does_not_suppress_a_concurrent_threads_warning(self):
        tiku = self.bank(verify_ssl=False)
        entered, release = threading.Event(), threading.Event()
        def request(*args, **kwargs):
            entered.set()
            release.wait(3)
            return self.answer()
        with warnings.catch_warnings(record=True) as caught, patch.object(tiku._session, "request", side_effect=request):
            warnings.simplefilter("always")
            worker = threading.Thread(target=lambda: tiku._query({"title": "q"}))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                warnings.warn("unrelated HTTPS request", InsecureRequestWarning)
            finally:
                release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual([str(item.message) for item in caught], ["unrelated HTTPS request"])

    def test_certificate_failure_names_the_bank(self):
        tiku = self.bank()
        with patch.object(tiku._session, "request", side_effect=requests.exceptions.SSLError("bad cert")),                 patch("api.ocs_tiku.logger") as log:
            self.assertIsNone(tiku._query({"title": "题", "type": "single", "options": ""}))
        message = log.error.call_args_list[0].args[0]
        self.assertIn("示例题库", message)
        self.assertIn("关闭证书校验", message)

    def test_close_releases_the_session(self):
        tiku = self.bank()
        session = tiku._session
        with patch.object(session, "close") as close:
            tiku.close()
        close.assert_called_once()


class CompatibilityTests(unittest.TestCase):
    def test_object_literals_and_metadata(self):
        run = compile_handler('return r => ({empty: {}, "tags": [r.tag, {ai: true}],})')
        expected = {"empty": {}, "tags": ["tag", {"ai": True}]}
        self.assertEqual(run({"tag": "tag"}), expected)
        rows = normalize_results([["Q", "A", expected], [None, "B"], []])
        self.assertEqual(rows[0]["extra_data"], expected)
        self.assertEqual(rows[1]["extra_data"], {})
        self.assertEqual(select_answer(["Q", "A", expected]), "A")
        self.assertEqual(normalize_results(None), [])

    def test_unsupported_object_syntax_and_nesting(self):
        for expression in ("{...r}", "{[r.key]: 1}", "{run() {}}", "{x}", "{x: 1 y: 2}", "{return r;}", "{" ):
            with self.subTest(expression=expression), self.assertRaisesRegex(HandlerSyntaxError, "字符位置"):
                compile_handler("return r => (" + expression + ")")
        with self.assertRaisesRegex(HandlerSyntaxError, "嵌套过深|过于复杂"):
            compile_handler("return r => " + "[" * 150 + "1" + "]" * 150)

    def test_diagnostics_and_individual_config_isolation(self):
        secret = "dummy-secret-not-for-logs"
        invalid = _wrapper(name="坏配置", data={"token": secret, "images": {"handler": "return e => ({...e})"}})
        with requests.Session() as session, patch("api.ocs_tiku.logger") as log:
            wrappers = load_wrappers({"wrappers": [invalid, _wrapper()]}, session)
            self.assertEqual(len(wrappers), 1)
            warning = log.warning.call_args
            message = warning.args[0].format(*warning.args[1:])
            for value in ("第 1 个", "坏配置", "data.images.handler", "字符位置"):
                self.assertIn(value, message)
            self.assertNotIn(secret, message)
            self.assertNotIn("...e", message)
            with self.assertRaisesRegex(ValueError, "所有题库配置"):
                load_wrappers({"wrappers": [invalid]}, session)
            with self.assertRaisesRegex(ValueError, "最多"):
                load_wrappers({"wrappers": [_wrapper()] * 21}, session)
        for value in ("invalid", {}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_wrapper(_wrapper(type=value))

    def test_recursive_substitution_and_explicit_context(self):
        supplied = {"title": "原题", "options": ["A", "B"], "type": "single",
                    "suggestion_title": "已提供文本", "suggestion_options": "已提供选项",
                    "images": [], "token": "must-not-leak"}
        env = question_env(supplied)
        self.assertNotIn("token", env)
        self.assertEqual(env["options"], "A\nB")
        self.assertEqual(env["images"], [])
        data = {"nested": [{"title": "${suggestion_title}", "number": 2}], "literal": "${title}"}
        result = substitute(data, env)
        self.assertEqual(result["nested"][0]["title"], "已提供文本")
        self.assertEqual(data["nested"][0]["title"], "${suggestion_title}")
        self.assertEqual(substitute("${title}", {"title": "${options}", "options": "not injected"}), "${options}")
        for text in ("${unknown}",):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "缺少上下文"):
                substitute(text, question_env({"title": "Q"}))
        with self.assertRaises(ValueError):
            substitute("${title}", {"title": {}})
        for _ in range(50):
            data = [data]
        with self.assertRaisesRegex(ValueError, "嵌套过深"):
            substitute(data, env)

    def test_response_type_does_not_control_request_encoding(self):
        for transport in ("fetch", "GM_xmlhttpRequest"):
            for response_type in ("json", "text"):
                for content_type in (None, "application/json", "application/x-www-form-urlencoded"):
                    with self.subTest(transport=transport, response=response_type, content_type=content_type):
                        headers = {"content-type": content_type} if content_type else {}
                        wrapper = normalize_wrapper(_wrapper(method="post", type=transport,
                            contentType=response_type, headers=headers, data={"title": "${title}"}))
                        response = Mock(text="plain")
                        response.json.return_value = {"answer": "A"}
                        with requests.Session() as session, patch.object(session, "request", return_value=response) as send:
                            result = request_wrapper(wrapper, question_env({"title": "题目"}), session)
                        kwargs = send.call_args.kwargs
                        if transport == "GM_xmlhttpRequest" and content_type == "application/x-www-form-urlencoded":
                            self.assertEqual(kwargs["data"], {"title": "题目"})
                        else:
                            self.assertEqual(json.loads(kwargs["data"]), {"title": "题目"})
                        self.assertEqual(kwargs["headers"], headers)
                        self.assertEqual(result, {"answer": "A"} if response_type == "json" else "plain")
                        response.close.assert_called_once()

    def test_get_overrides_existing_parameters_and_encodes_values(self):
        from urllib.parse import parse_qs, urlsplit
        wrapper = normalize_wrapper(_wrapper(url="https://example.test/${title}?title=old&keep=1&keep=2&empty=",
            data={"title": "${title}"}))
        env = question_env({"title": "A&B/#?中文"})
        response = Mock()
        with requests.Session() as session, patch.object(session, "request", return_value=response) as send:
            request_wrapper(wrapper, env, session)
        url = send.call_args.args[1]
        self.assertIn("A%26B%2F%23%3F", url)
        self.assertEqual(parse_qs(urlsplit(url).query, keep_blank_values=True), {"keep": ["1", "2"], "empty": [""]})
        self.assertEqual(send.call_args.kwargs["params"], {"title": "A&B/#?中文"})
        response.close.assert_called_once()

    def test_response_closed_on_http_and_decode_errors(self):
        for failure in ("http", "json"):
            with self.subTest(failure=failure):
                response = Mock()
                if failure == "http":
                    response.raise_for_status.side_effect = requests.HTTPError("mock")
                else:
                    response.json.side_effect = ValueError("mock")
                with requests.Session() as session, patch.object(session, "request", return_value=response):
                    with self.assertRaises((ValueError, requests.HTTPError)):
                        request_wrapper(normalize_wrapper(_wrapper()), question_env({}), session)
                response.close.assert_called_once()




class QueryDiagnosticsTests(unittest.TestCase):
    def bank(self, wrappers):
        bank = TikuOcs()
        bank.config_set({'wrappers': wrappers})
        bank.init_tiku()
        self.addCleanup(bank.close)
        return bank

    def query_payload(self, bank, payload, question=None):
        response = Mock()
        response.json.return_value = payload
        with patch.object(bank._session, 'request', return_value=response), patch('api.ocs_tiku.logger') as log:
            answer = bank._query(question or {'title': 'Q'})
        messages = [call.args[0].format(*call.args[1:]) for call in log.error.call_args_list]
        return answer, messages

    def test_no_answer_messages_are_provider_neutral(self):
        bank = self.bank([_wrapper(handler='return r => [r.reason, undefined]')])
        for reason in ('题库次数余额不足', 'Token 无效，请检查配置', '未找到匹配题目'):
            with self.subTest(reason=reason):
                answer, messages = self.query_payload(bank, {'reason': reason})
                self.assertIsNone(answer)
                self.assertEqual(bank.query_diagnostics[0]['status'], 'no_answer')
                self.assertEqual(bank.query_diagnostics[0]['message'], reason)
                self.assertEqual(messages, [f'题库 示例题库: no_answer（题库提示：{reason}）'])

    def test_empty_messages_keep_status_only_log(self):
        bank = self.bank([_wrapper(handler='return r => r.rows')])
        for rows in (None, [], [[None, None]], [['', None]], [[' \r\n ', None]],
                     [[{'message': 'do not stringify'}, None]],
                     [[None, None, {'tags': [{'text': 'tag only'}]}]],
                     [['<script>not visible</script><style>not visible</style>', None]]):
            with self.subTest(rows=rows):
                answer, messages = self.query_payload(bank, {'rows': rows, 'message': 'raw response must stay private'})
                self.assertIsNone(answer)
                self.assertNotIn('message', bank.query_diagnostics[0])
                self.assertEqual(messages, ['题库 示例题库: no_answer'])

    def test_messages_use_first_usable_row_within_diagnostic_limit(self):
        bank = self.bank([_wrapper(handler='return r => r.rows')])
        cases = [([[None, None], [' ', None], ['first', None], ['second', None]], 'first'),
                 ([[None, None]] * 19 + [['last visible', None]], 'last visible'),
                 ([[None, None]] * 20 + [['outside limit', None]], None)]
        for rows, expected in cases:
            with self.subTest(expected=expected):
                self.query_payload(bank, {'rows': rows})
                self.assertEqual(bank.query_diagnostics[0].get('message'), expected)
                self.assertLessEqual(len(bank.query_diagnostics[0]['candidates']), 20)

    def test_no_answer_messages_and_snapshots_are_safe(self):
        credential = 'fixture-credential'
        bank = self.bank([_wrapper(data={'token': credential}, handler='return r => [r.reason, undefined]')])
        cases = [
            ('<b>余额不足</b>&nbsp;<script>script-hidden</script><style>style-hidden</style>'
             '<a href="https://example.test/account?token=' + credential + '">个人中心</a>',
             '余额不足', ['<', 'script-hidden', 'style-hidden', 'https://', credential]),
            ('余额不足 fixture&#45;credential https://example.test/private data:image/png;base64,IMAGEDATA',
             '余额不足', [credential, 'https://', 'IMAGEDATA', 'data:image/']),
            ('余额不足\r\n\x1b[31m请检查\x1b[0m\u202e\u200b\x00配置',
             '余额不足 请检查配置', ['\r', '\n', '\x1b', '[31m', '[0m', '\u202e', '\u200b', '\x00']),
            ('提示 token=unlisted password:unlisted api_key=unlisted authorization=unlisted',
             '提示', ['unlisted']),
            ('余额不足 ' + '说明' * 200, '余额不足', []),
        ]
        for reason, expected, forbidden in cases:
            with self.subTest(reason=reason[:30]):
                answer, messages = self.query_payload(bank, {'reason': reason})
                self.assertIsNone(answer)
                report = bank.query_diagnostics[0]
                self.assertEqual(report['status'], 'no_answer')
                self.assertIn(expected, report['message'])
                self.assertLessEqual(len(report['message']), 256)
                self.assertEqual(report['message'], report['candidates'][0]['question'])
                output = str(report) + ''.join(messages)
                for value in forbidden:
                    self.assertNotIn(value, output)

    def test_normalized_credentials_and_terminal_links_remain_redacted(self):
        bank = self.bank([_wrapper(data={'token': 'fixture&amp;credential'},
                                  handler='return r => [r.reason, undefined]')])
        reasons = ('余额不足 fixture&amp;credential', '余额不足 fixture&credential',
                   '余额不足 \x1b]8;;https://example.test/account\x1b\x5c个人中心\x1b]8;;\x1b\x5c')
        for reason in reasons:
            with self.subTest(reason=reason):
                _, messages = self.query_payload(bank, {'reason': reason})
                output = str(bank.query_diagnostics) + ''.join(messages)
                for forbidden in ('fixture', 'credential', 'https://', '\x1b', ']8;;'):
                    self.assertNotIn(forbidden, output)

    def test_message_reaches_existing_task_log_sink(self):
        from api.task_logging import task_log_sink
        bank = self.bank([_wrapper(handler='return r => [r.reason, undefined]')])
        response = Mock()
        response.json.return_value = {'reason': '查询额度不足'}
        store = Mock()
        with task_log_sink(store, 'offline-diagnostic'), patch.object(bank._session, 'request', return_value=response):
            self.assertIsNone(bank._query({'title': 'Q'}))
        messages = [call.args[1] for call in store.append_log.call_args_list]
        self.assertTrue(any('no_answer（题库提示：查询额度不足）' in message for message in messages))

    def test_failure_message_does_not_block_next_bank_or_change_answers(self):
        bank = self.bank([_wrapper(name='first', handler='return r => [r.reason, undefined]'),
                          _wrapper(name='second')])
        answer, messages = self.query_payload(bank, {'reason': '余额不足', 'code': 1,
                                                    'question': 'Q', 'answer': 'Answer\n  formatting'})
        self.assertEqual(answer, 'Answer\n  formatting')
        self.assertEqual([r['status'] for r in bank.query_diagnostics], ['no_answer', 'selected'])
        self.assertNotIn('message', bank.query_diagnostics[1])
        self.assertIn('余额不足', messages[0])

    def test_unmatched_answers_do_not_become_failure_messages(self):
        bank = self.bank([_wrapper()])
        answer, messages = self.query_payload(bank, {'code': 1, 'question': 'not an error', 'answer': 'Gamma'},
                                              {'title': 'Q', 'type': 'single', 'options': 'A. Alpha\nB. Beta'})
        self.assertIsNone(answer)
        self.assertEqual(bank.query_diagnostics[0]['status'], 'unmatched')
        self.assertNotIn('message', bank.query_diagnostics[0])
        self.assertEqual(messages, ['题库 示例题库: unmatched'])

    def test_no_answer_message_is_not_cached(self):
        bank = self.bank([_wrapper(handler='return r => [r.reason, undefined]')])
        response = Mock()
        response.json.return_value = {'reason': '余额不足'}
        with patch('api.answer.CacheDAO.get_shared') as shared, patch.object(bank._session, 'request', return_value=response):
            shared.return_value.get_cache.return_value = None
            self.assertIsNone(bank.query({'title': 'Q', 'type': 'single', 'options': 'A. Alpha\nB. Beta'}))
            shared.return_value.add_cache.assert_not_called()
        self.assertEqual(bank.query_diagnostics[0]['message'], '余额不足')

    def test_unusable_candidate_does_not_block_later_candidate(self):
        bank = self.bank([_wrapper(handler='return r => r.results')])
        payload = Mock()
        payload.json.return_value = {'results': [['Q', 'TCP', {}], ['Q', 'TCP/IP', {'ai': True, 'tags': ['reference']}]]}
        with patch.object(bank._session, 'request', return_value=payload):
            self.assertEqual(bank._query({'title': 'Q', 'type': 'single', 'options': 'A. TCP/IP\nB. UDP'}), 'TCP/IP')
        self.assertEqual(bank.query_diagnostics[0]['selected']['ai'], True)
        self.assertEqual(bank.query_diagnostics[0]['selected']['tags'], ['reference'])
        self.assertEqual(bank.query_diagnostics[0]['status'], 'selected')

    def test_failure_classification_and_no_credential_leak(self):
        for failure, status in [(requests.Timeout('token=secret'), 'timeout'),
                                (requests.ConnectionError('https://host/?token=secret'), 'network_error')]:
            bank = self.bank([_wrapper()])
            with patch.object(bank._session, 'request', side_effect=failure), patch('api.ocs_tiku.logger') as log:
                self.assertIsNone(bank._query({'title': 'Q'}))
            self.assertEqual(bank.query_diagnostics[0]['status'], status)
            self.assertNotIn('secret', str(bank.query_diagnostics) + str(log.mock_calls))
        bank = self.bank([_wrapper()])
        response = Mock()
        response.json.side_effect = ValueError('token=secret')
        with patch.object(bank._session, 'request', return_value=response):
            bank._query({'title': 'Q'})
        self.assertEqual(bank.query_diagnostics[0]['status'], 'response_parse_error')
        response.close.assert_called_once()

    def test_image_context_reused_and_answer_restored(self):
        from tests.test_question_images import picture, URL, SECOND
        config = _wrapper(method='post', data={'images': {'handler': 'return env => env.images'},
            'title': '${suggestion_title}', 'options': '${suggestion_options}'})
        bank = self.bank([config, config])
        empty, answer = Mock(), Mock()
        empty.json.return_value = {'code': 0}
        answer.json.return_value = {'code': 1, 'question': 'Q', 'answer': '[图片2]'}
        q = {'title': 'Q', 'type': 'single', 'options': 'A. ' + SECOND + '\nB. text',
             '_image_context': {'title': URL, 'options': 'A. ' + SECOND + '\nB. text', 'urls': [URL, SECOND]}}
        with patch('api.question_images.download_image', return_value=picture()) as download, \
             patch.object(bank._session, 'request', side_effect=[empty, answer]) as send:
            self.assertEqual(bank._query(q), SECOND)
        self.assertEqual(download.call_count, 2)
        self.assertEqual(len(json.loads(send.call_args.kwargs['data'])['images']), 2)
        self.assertEqual([r['status'] for r in bank.query_diagnostics], ['no_answer', 'selected'])

    def test_legacy_wrapper_does_not_download_images(self):
        bank = self.bank([_wrapper()])
        with patch('api.ocs_tiku.build_image_env') as build, patch.object(bank._session, 'request', side_effect=requests.Timeout):
            bank._query({'title': 'Q', '_image_context': {'urls': ['unused']}})
        build.assert_not_called()



    def test_repeated_failures_open_a_circuit_for_the_rest_of_the_task(self):
        bank = self.bank([_wrapper(name='dead')])
        requests_sent = Mock(side_effect=requests.Timeout('offline bank'))
        with patch.object(bank._session, 'request', requests_sent), patch('api.ocs_tiku.logger') as log:
            for _ in range(3):
                self.assertIsNone(bank._query({'title': 'Q'}))
                self.assertEqual(bank.query_diagnostics[0]['status'], 'timeout')
            self.assertIsNone(bank._query({'title': 'Q'}))
            self.assertEqual(bank.query_diagnostics[0]['status'], 'circuit_open')
        self.assertEqual(requests_sent.call_count, 3)
        self.assertTrue(any('连续失败 3 次' in call.args[0].format(*call.args[1:]) for call in log.error.call_args_list))

    def test_authentication_rejection_opens_the_circuit_immediately(self):
        bank = self.bank([_wrapper(name='auth-bank')])
        error = requests.HTTPError('401 Client Error')
        error.response = Mock(status_code=401)
        requests_sent = Mock(side_effect=error)
        with patch.object(bank._session, 'request', requests_sent), patch('api.ocs_tiku.logger'):
            self.assertIsNone(bank._query({'title': 'Q'}))
            self.assertEqual(bank.query_diagnostics[0]['status'], 'authentication_rejected')
            self.assertIsNone(bank._query({'title': 'Q'}))
            self.assertEqual(bank.query_diagnostics[0]['status'], 'circuit_open')
        self.assertEqual(requests_sent.call_count, 1)

    def test_successful_answer_resets_the_failure_count(self):
        bank = self.bank([_wrapper()])
        good = Mock()
        good.json.return_value = {'code': 1, 'question': 'Q', 'answer': 'A'}
        requests_sent = Mock(side_effect=[requests.Timeout('t1'), requests.Timeout('t2'), good,
                                          requests.Timeout('t3'), requests.Timeout('t4'), requests.Timeout('t5')])
        with patch.object(bank._session, 'request', requests_sent), patch('api.ocs_tiku.logger'):
            self.assertIsNone(bank._query({'title': 'Q'}))
            self.assertIsNone(bank._query({'title': 'Q'}))
            self.assertEqual(bank._query({'title': 'Q'}), 'A')
            for _ in range(2):
                self.assertIsNone(bank._query({'title': 'Q'}))
                self.assertEqual(bank.query_diagnostics[0]['status'], 'timeout')
            self.assertIsNone(bank._query({'title': 'Q'}))
            self.assertEqual(bank.query_diagnostics[0]['status'], 'timeout')
            self.assertIsNone(bank._query({'title': 'Q'}))
            self.assertEqual(bank.query_diagnostics[0]['status'], 'circuit_open')
        self.assertEqual(requests_sent.call_count, 6)

    def test_no_answer_keeps_the_circuit_closed(self):
        bank = self.bank([_wrapper(handler='return r => [r.reason, undefined]')])
        requests_sent = Mock(side_effect=lambda *a, **k: Mock(json=lambda: {'reason': '未找到匹配题目'}))
        with patch.object(bank._session, 'request', requests_sent), patch('api.ocs_tiku.logger'):
            for _ in range(5):
                self.assertIsNone(bank._query({'title': 'Q'}))
                self.assertEqual(bank.query_diagnostics[0]['status'], 'no_answer')
        self.assertEqual(requests_sent.call_count, 5)


class AdditionalCompatibilityTests(unittest.TestCase):
    def test_authentication_and_handler_failures_are_separate(self):
        bank = TikuOcs()
        self.addCleanup(bank.close)
        bank.config_set({'wrappers': [_wrapper()]})
        bank.init_tiku()
        response = Mock(status_code=401)
        response.raise_for_status.side_effect = requests.HTTPError('credential', response=response)
        with patch.object(bank._session, 'request', return_value=response):
            bank._query({'title': 'Q'})
        self.assertEqual(bank.query_diagnostics[0]['status'], 'authentication_rejected')
        self.assertNotIn('credential', str(bank.query_diagnostics))
        # 401 已立即熔断该题库实例，处理失败用新实例（新任务）观察。
        other = TikuOcs()
        self.addCleanup(other.close)
        other.config_set({'wrappers': [_wrapper()]})
        other.init_tiku()
        response = Mock()
        response.json.return_value = {}
        other.wrappers[0]['run'] = Mock(side_effect=ValueError('secret handler data'))
        with patch.object(other._session, 'request', return_value=response):
            other._query({'title': 'Q'})
        self.assertEqual(other.query_diagnostics[0]['status'], 'handler_error')
        self.assertNotIn('secret', str(other.query_diagnostics))

    def test_cache_hit_resets_metadata_without_fetching_images(self):
        bank = TikuOcs()
        bank.query_diagnostics = [{'source': 'previous', 'ai': True}]
        with patch('api.answer.CacheDAO.get_shared') as shared, patch.object(bank, '_query') as query:
            shared.return_value.get_cache.return_value = 'A'
            self.assertEqual(bank.query({'title': 'Q', 'type': 'single', 'options': 'A. one\nB. two'}), 'A')
        query.assert_not_called()
        self.assertEqual(bank.query_diagnostics, [{'source': 'cache', 'status': 'selected'}])
        bank._session.close()

    def test_declared_secrets_are_removed_from_result_metadata(self):
        from api.ocs_tiku import _result_summary
        result = _result_summary({'question': 'Q', 'answer': 'A',
            'extra_data': {'ai': True, 'tags': ['bare-secret', 'https://host/?token=secret']}}, ['bare-secret'])
        self.assertNotIn('secret', str(result))

if __name__ == "__main__":
    unittest.main()
