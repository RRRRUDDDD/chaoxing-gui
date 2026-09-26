import json
import unittest
from unittest.mock import Mock, patch

import requests

from api.answer import Tiku
from api.ocs_tiku import (
    HandlerSyntaxError,
    TikuOcs,
    compile_handler,
    load_wrappers,
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
        resolved = resolve_data({
            "title": {"handler": "return (env)=> env.title.replace('单选题','')"},
            "question": "${title}",
        }, env)
        self.assertEqual(resolved["title"], "中国梦")
        self.assertEqual(resolved["question"], "单选题中国梦")

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

        def fake_get(url, params=None, headers=None, timeout=None, verify=None):
            response = Mock()
            response.raise_for_status.return_value = None
            if url.startswith("https://empty.test"):
                response.json.return_value = {}
            else:
                self.assertEqual(params["question"], "中国梦是什么")
                self.assertEqual(params["kind"], "single")
                response.json.return_value = {"code": 1, "question": "中国梦是什么", "answer": "复兴"}
            return response

        with patch("api.ocs_tiku.requests.get", side_effect=fake_get):
            answer = loaded._query({"title": "中国梦是什么", "type": "single", "options": "A. 复兴"})
        self.assertEqual(answer, "复兴")

    def test_subscription_and_invalid_config(self):
        payload = [_wrapper()]
        response = Mock()
        response.raise_for_status.return_value = None
        response.content = b"[]"
        response.json.return_value = payload
        with patch("api.ocs_tiku.requests.get", return_value=response) as get:
            wrappers = load_wrappers({"subscription": "https://bank.test/ocs.json"})
        self.assertEqual(wrappers[0]["name"], "示例题库")
        get.assert_called_once()

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

    def test_request_failure_tries_next_bank(self):
        tiku = TikuOcs()
        tiku.config_set({"wrappers": [
            _wrapper(name="失败", url="https://down.test/search"),
            _wrapper(name="成功", url="https://up.test/search"),
        ]})
        tiku.init_tiku()

        def fake_get(url, params=None, headers=None, timeout=None, verify=None):
            if "down.test" in url:
                raise requests.ConnectionError("offline")
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"code": 1, "question": None, "answer": "B"}
            return response

        with patch("api.ocs_tiku.requests.get", side_effect=fake_get):
            self.assertEqual(tiku._query({"title": "题目", "type": "single", "options": ["A. 一", "B. 二"]}), "B")


if __name__ == "__main__":
    unittest.main()
