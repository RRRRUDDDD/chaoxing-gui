"""Comprehensive tests for the integrated AI subsystem."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from api.answer import Tiku
from api.ai.config import AIConfigStore
from api.ai.db import AIDB
from api.ai.engine import (
    AIEngine,
    extract_answer_from_output,
    get_most_frequent_answer,
)
from api.ai.forgetting import (
    effective_mastery,
    lapse_forgetting,
    next_forgetting_stage,
    parse_study_quality,
    reviewed_at_for_quality,
)
from api.ai.llm import ModelCallError, ModelCallResult
from api.ai.ocs_server import LocalOcsServer
from api.ai.routes import create_ai_blueprint
from api.ai.study_ai import (
    chat_with_tutor,
    check_practice_answer,
    generate_study_graph_with_ai,
)
from api.ai.tiku_adapter import TikuHybrid
from flask import Flask


class TestAISubsystem(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "ai.db")
        self.cfg_path = os.path.join(self.tmpdir.name, "ai_config.json")
        self.db = AIDB(self.db_path)
        self.cfg = AIConfigStore(self.cfg_path)
        self.engine = AIEngine(self.db, self.cfg)
        self.server = LocalOcsServer(self.engine, self.cfg)

    def tearDown(self):
        self.server.stop()
        self.tmpdir.cleanup()

    def test_forgetting_curve_parity(self):
        self.assertEqual(parse_study_quality("差"), "poor")
        self.assertEqual(parse_study_quality("一般"), "fair")
        self.assertEqual(parse_study_quality("good"), "good")
        stage, _days, ret = lapse_forgetting(2, 0.0)
        self.assertEqual(stage, 2)
        self.assertAlmostEqual(ret, 1.0, places=4)
        now_iso = reviewed_at_for_quality(1, "good")
        self.assertEqual(next_forgetting_stage(0, None, True, now_iso), 0)
        self.assertEqual(next_forgetting_stage(1, "2025-01-01T00:00:00Z", True, "2025-01-02T00:00:00Z"), 2)
        self.assertEqual(next_forgetting_stage(2, "2025-01-01T00:00:00Z", False, "2025-01-02T00:00:00Z"), 1)
        self.assertEqual(effective_mastery(0, 0, None), 0)
        self.assertGreaterEqual(effective_mastery(1, 4, now_iso), 3)

    def test_folders_cycle_guard_and_questions_crud_import_export(self):
        f1 = self.db.create_folder("高等数学", 0)
        f2 = self.db.create_folder("微积分", f1["id"])
        with self.assertRaises(ValueError):
            self.db.move_folder(f1["id"], f2["id"])
        self.db.rename_folder(f2["id"], "一元微积分")

        saved = self.db.save_question(
            {
                "question": "1. sin(x) 的导数是？",
                "options": "A. cos(x)\nB. -cos(x)",
                "questionType": "single",
                "answer": "cos(x)",
                "folderId": f2["id"],
                "isAi": True,
            }
        )
        self.assertEqual(saved["folderName"], "一元微积分")

        hit = self.db.find_exact_local_match("sin(x) 的导数是？", "A. cos(x)\nB. -cos(x)", "single")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["answer"], "cos(x)")

        # Pending correction hides from exact local match until fixed
        self.db.set_pending_correction(saved["id"], True)
        self.assertIsNone(self.db.find_exact_local_match("sin(x) 的导数是？"))
        self.db.set_pending_correction(saved["id"], False, corrected_answer="cos(x)")
        self.assertIsNotNone(self.db.find_exact_local_match("sin(x) 的导数是？"))

        # Export & import JSON + CSV
        exported = self.db.export_questions(fmt="json")
        self.assertEqual(exported["count"], 1)
        csv_text = "question,options,questionType,answer,explanation\n地球是圆的吗,,judgement,正确,常识\n"
        imp = self.db.import_questions(fmt="csv", content=csv_text, folder_id=0)
        self.assertEqual(imp["imported"], 1)
        self.assertEqual(self.db.list_questions()["total"], 2)

        # Delete folder moves questions back to default folder
        self.db.delete_folder(f1["id"], delete_questions=False)
        reloaded = self.db.get_question(saved["id"])
        self.assertEqual(reloaded["folderId"], 0)

    def test_answer_extraction_and_majority_vote(self):
        self.assertEqual(extract_answer_from_output('```json\n{"answer":"传动角"}\n```'), "传动角")
        self.assertEqual(extract_answer_from_output('思考完毕\n{"answer":"键盘###鼠标"}'), "键盘###鼠标")
        self.assertEqual(get_most_frequent_answer(["B", "A", "A"]), "A")

    def test_engine_layered_models_summary_and_same_question_dedup(self):
        cfg = self.cfg.load()
        cfg["platforms"][0]["apiKey"] = "sk-test-deepseek"
        cfg["selectedTextModels"] = ["deepseek/deepseek-chat", "deepseek/deepseek-reasoner"]
        cfg["selectedSummaryModels"] = ["deepseek/deepseek-chat"]
        cfg["engine"]["enabled"] = True
        cfg["engine"]["autoSaveToBank"] = True
        cfg["engine"]["enableSameQuestionCheck"] = True
        self.cfg.save(cfg)

        def fake_call_model(platform, model, messages, **kwargs):
            last_text = messages[-1]["content"]
            if "题目判重助手" in last_text:
                return ModelCallResult(content='{"same":true,"matched_id":1}', model_name=model["displayName"])
            if "答题总结复核专家" in last_text:
                return ModelCallResult(content='{"answer":"北京###上海"}', reasoning="复核确认", model_name=model["displayName"])
            return ModelCallResult(content='{"answer":"北京###上海"}', reasoning="推理过程", model_name=model["displayName"])

        with patch("api.ai.engine.call_model", side_effect=fake_call_model):
            res = self.engine.answer_question(
                {"title": "中国的首都是____，最大的城市是____。", "type": "completion"}
            )
            self.assertTrue(res["ok"])
            self.assertEqual(res["answer"], "北京###上海")
            self.assertIn("总结复核", res["modelName"])
            # Auto-saved into SQLite
            self.assertEqual(self.db.list_questions()["total"], 1)

            # Exact local hit without calling model
            res2 = self.engine.answer_question(
                {"title": "中国的首都是____，最大的城市是____。", "type": "completion"}
            )
            self.assertEqual(res2["source"], "local")

            # Slightly reworded question triggers AI same-question dedup!
            res3 = self.engine.answer_question(
                {"title": "我国的首都是____，最大的城市是____？", "type": "completion"}
            )
            self.assertEqual(res3["source"], "local_same")
            self.assertEqual(res3["answer"], "北京###上海")

    def test_hybrid_tiku_cooperation_priorities(self):
        cfg = self.cfg.load()
        cfg["platforms"][0]["apiKey"] = "sk-test-key"
        cfg["selectedTextModels"] = ["deepseek/deepseek-chat"]
        cfg["engine"]["enabled"] = True
        cfg["engine"]["priority"] = "local_ocs_ai"
        self.cfg.save(cfg)

        tiku = TikuHybrid(ai_engine=self.engine, config_store=self.cfg)
        tiku.config_set({"ai_enabled": True, "ai_priority": "local_ai_only"})
        tiku.init_tiku()
        self.assertFalse(tiku.DISABLE)

        fake_res = ModelCallResult(content='{"answer":"正确"}', model_name="DeepSeek Chat")
        with patch("api.ai.engine.call_model", return_value=fake_res):
            ans = tiku._query({"title": "地球绕太阳转", "type": "judgement", "options": ""})
            self.assertEqual(ans, "正确")
            # Cached in local SQLite now
            self.assertIsNotNone(self.db.find_exact_local_match("地球绕太阳转"))

        # Verify Tiku factory respects explicit ai_enabled=False for backward compatibility
        base_tiku = Tiku()
        base_tiku.config_set({"ai_enabled": False})
        resolved = base_tiku.get_tiku_from_config()
        self.assertTrue(resolved.DISABLE)

    def test_practice_study_graph_and_agent_chat(self):
        cfg = self.cfg.load()
        cfg["platforms"][0]["apiKey"] = "sk-test-key"
        cfg["selectedTextModels"] = ["deepseek/deepseek-chat"]
        self.cfg.save(cfg)

        q = self.db.save_question(
            {
                "question": "下列属于输入设备的是？",
                "options": "A. 键盘\nB. 显示器",
                "questionType": "single",
                "answer": "键盘",
            }
        )
        self.assertTrue(check_practice_answer(q, "键盘"))
        self.assertTrue(check_practice_answer(q, "A"))
        self.assertFalse(check_practice_answer(q, "显示器"))

        subj = self.db.save_subject(name="计算机基础", description="硬件与软件")
        graph_json = json.dumps(
            {
                "nodes": [
                    {"key": "k1", "name": "计算机硬件", "summary": "组成部件", "parentKey": None, "linkedQuestionIds": [q["id"]]},
                    {"key": "k2", "name": "输入输出设备", "summary": "外设", "parentKey": "k1", "linkedQuestionIds": [q["id"]]},
                ],
                "edges": [{"fromKey": "k1", "toKey": "k2", "relation": "包含"}],
            },
            ensure_ascii=False,
        )
        with patch("api.ai.study_ai.call_model", return_value=ModelCallResult(content=graph_json, model_name="DeepSeek")):
            gen = generate_study_graph_with_ai(self.db, self.cfg, subject_id=subj["id"], folder_id=0)
            self.assertEqual(gen["createdNodeCount"], 2)
            self.assertEqual(len(gen["graph"]["nodes"]), 2)
            self.assertGreaterEqual(len(gen["graph"]["edges"]), 1)

        # Practice updates mastery and linked knowledge graph forgetting stage
        rec = self.db.record_practice(question_id=q["id"], user_answer="键盘", is_correct=True)
        self.assertEqual(rec["mastery"], 1)
        node_id = gen["graph"]["nodes"][0]["id"]
        rev = self.db.review_graph_node(node_id, "good")
        self.assertGreaterEqual(rev["mastery"], 1)

        # Agent chat persists messages
        sess = self.db.create_agent_session("复习计划")
        with patch("api.ai.study_ai.call_model", return_value=ModelCallResult(content="建议先复习输入输出设备。", model_name="DeepSeek")):
            reply = chat_with_tutor(
                self.db,
                self.cfg,
                session_id=sess["id"],
                message="帮我制定复习计划",
                subject_id=subj["id"],
            )
            self.assertEqual(len(reply["session"]["messages"]), 2)
            self.assertIn("输入输出设备", reply["reply"]["content"])

    def test_rpc_blueprint_end_to_end(self):
        test_app = Flask(__name__)
        test_app.register_blueprint(
            create_ai_blueprint(lambda: self.db, lambda: self.cfg, lambda: self.engine, lambda: self.server)
        )
        client = test_app.test_client()

        resp = client.post("/api/ai/rpc", json={"action": "config.get", "params": {}})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertTrue(body["status"])
        self.assertGreaterEqual(len(body["data"]["config"]["platforms"]), 5)

        resp2 = client.post(
            "/api/ai/rpc",
            json={
                "action": "questions.save",
                "params": {"question": "测试题目", "answer": "测试答案", "questionType": "single"},
            },
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.get_json()["status"])

        resp3 = client.post("/api/ai/rpc", json={"action": "unknown.action", "params": {}})
        self.assertEqual(resp3.status_code, 400)

    def test_ollama_disabled_by_default(self):
        cfg = self.cfg.load()
        ollama = next((p for p in cfg["platforms"] if p["id"] == "ollama"), None)
        self.assertIsNotNone(ollama)
        self.assertFalse(ollama["enabled"])
        summary = self.cfg.summary_stats()
        self.assertEqual(summary["configuredPlatforms"], 0)

    def test_agent_chaoxing_tools_and_react_fallback(self):
        cfg = self.cfg.load()
        cfg["platforms"][0]["apiKey"] = "sk-test-key"
        cfg["selectedTextModels"] = ["deepseek/deepseek-chat"]
        self.cfg.save(cfg)

        class FakeChaoxingBridge:
            def __init__(self):
                self.started = []
                self.stopped = []
                self.web_settings = {"speed": 1.0, "jobs": 1, "notopen_action": "retry"}

            def list_courses(self, creds):
                return [
                    {"courseId": "c-101", "title": "高等数学", "teacherfactor": "张老师", "clazzId": "cl-1", "cpi": "1"},
                    {"courseId": "c-102", "title": "大学英语", "teacherfactor": "李老师", "clazzId": "cl-2", "cpi": "2"},
                ]

            def get_course_chapters(self, creds, course_id, course_obj=None):
                return {"totalChapters": 10, "completedChapters": 7, "pendingChapters": 3, "pendingSample": []}

            def start_study(self, creds, *, course_ids, speed=None, jobs=None, notopen_action=None, shutdown_on_complete=False):
                rec = {"task_id": "task-agent-1", "course_ids": course_ids, "speed": speed or 1.0, "jobs": jobs or 1}
                self.started.append(rec)
                return rec

            def get_task_status(self, username, task_id=None, log_limit=15):
                return {"hasTask": True, "taskId": task_id or "task-agent-1", "status": {"status": "running", "progress": 1, "total": 2}}

            def stop_task(self, username, task_id=None):
                self.stopped.append(task_id or "task-agent-1")
                return {"task_id": task_id or "task-agent-1", "state": "stopping"}

            def get_web_settings(self, username=None):
                return dict(self.web_settings)

            def update_web_settings(self, patch):
                self.web_settings.update(patch)
                return dict(self.web_settings)

        bridge = FakeChaoxingBridge()
        test_app = Flask(__name__)
        test_app.register_blueprint(
            create_ai_blueprint(
                lambda: self.db,
                lambda: self.cfg,
                lambda: self.engine,
                lambda: self.server,
                get_chaoxing_bridge=lambda: bridge,
            )
        )
        client = test_app.test_client()

        # 1) Native multi-step tool calling: list courses -> start study by fuzzy name -> final reply
        native_responses = iter([
            ModelCallResult(
                content="",
                model_name="DeepSeek Chat",
                tool_calls=[{"id": "call_1", "name": "chaoxing_list_courses", "arguments": {}}],
            ),
            ModelCallResult(
                content="",
                model_name="DeepSeek Chat",
                tool_calls=[{
                    "id": "call_2",
                    "name": "chaoxing_start_study",
                    "arguments": {"course_ids": ["高等数学"], "speed": 1.5},
                }],
            ),
            ModelCallResult(
                content="已为您启动《高等数学》1.5倍速刷课任务（task-agent-1）。",
                model_name="DeepSeek Chat",
            ),
        ])
        with patch("api.ai.study_ai.call_model", side_effect=lambda *a, **kw: next(native_responses)):
            resp = client.post(
                "/api/ai/rpc",
                json={
                    "action": "agent.chat",
                    "params": {
                        "message": "帮我以 1.5 倍速刷高等数学",
                        "enableTools": True,
                        "chaoxing": {"username": "13800138000", "password": "", "use_cookies": True},
                    },
                },
            )
            self.assertEqual(resp.status_code, 200)
            payload = resp.get_json()
            self.assertTrue(payload["status"])
            data = payload["data"]
            self.assertEqual(len(data["toolCalls"]), 2)
            self.assertEqual(data["toolCalls"][1]["name"], "chaoxing_start_study")
            self.assertTrue(data["toolCalls"][1]["ok"])
            self.assertEqual(bridge.started[0]["course_ids"], ["c-101"])
            self.assertEqual(bridge.started[0]["speed"], 1.5)
            self.assertTrue(any(e["type"] == "task_started" and e["taskId"] == "task-agent-1" for e in data["uiEffects"]))

        # 2) Automatic ReAct fallback when model/gateway rejects native tools with HTTP 400
        react_calls = []

        def fake_react_caller(*args, **kwargs):
            react_calls.append(kwargs.get("tools"))
            if kwargs.get("tools"):
                raise ModelCallError("HTTP 400: tools parameter unsupported", status_code=400, retryable=False)
            if len(react_calls) == 2:
                return ModelCallResult(
                    content='<tool_call>{"name":"chaoxing_update_settings","arguments":{"ai_enabled":true,"speed":2.0}}</tool_call>',
                    model_name="Reasoner",
                )
            return ModelCallResult(content="已将刷课倍速设为 2.0 并开启 AI 协同。", model_name="Reasoner")

        with patch("api.ai.study_ai.call_model", side_effect=fake_react_caller):
            res = chat_with_tutor(
                self.db,
                self.cfg,
                session_id="",
                message="打开 AI 协同并把默认倍速改成 2 倍",
                enable_tools=True,
                engine=self.engine,
                chaoxing_bridge=bridge,
                chaoxing_ctx={"username": "13800138000", "use_cookies": True},
            )
            self.assertEqual(bridge.web_settings["speed"], 2.0)
            self.assertTrue(self.cfg.load()["engine"]["enabled"])
            self.assertEqual(len(res["toolCalls"]), 1)
            self.assertEqual(res["toolCalls"][0]["name"], "chaoxing_update_settings")
            self.assertTrue(any(e["type"] == "config_changed" for e in res["uiEffects"]))

        # 3) Unauthenticated Chaoxing tool call fails gracefully without crashing
        unauth_responses = iter([
            ModelCallResult(
                content="",
                model_name="DeepSeek Chat",
                tool_calls=[{"id": "c_u1", "name": "chaoxing_list_courses", "arguments": {}}],
            ),
            ModelCallResult(content="请先登录超星学习通账号。", model_name="DeepSeek Chat"),
        ])
        with patch("api.ai.study_ai.call_model", side_effect=lambda *a, **kw: next(unauth_responses)):
            res_unauth = chat_with_tutor(
                self.db,
                self.cfg,
                session_id="",
                message="看看我的课程",
                enable_tools=True,
                chaoxing_bridge=bridge,
                chaoxing_ctx=None,
            )
            self.assertFalse(res_unauth["toolCalls"][0]["ok"])
            self.assertIn("尚未登录", res_unauth["toolCalls"][0]["summary"])

    def test_agent_study_tools_dedup_parallel_coercion_and_anthropic_merge(self):
        from api.ai.agent_tools import _extract_react_tool_calls, execute_agent_tool, run_agent_loop
        from api.ai.llm import _to_anthropic_payload

        cfg = self.cfg.load()
        cfg["platforms"][0]["apiKey"] = "sk-test-key"
        cfg["selectedTextModels"] = ["deepseek/deepseek-chat"]
        self.cfg.save(cfg)

        # Seed question, pending correction, practice history, and knowledge graph
        q1 = self.db.save_question({
            "question": "OSI 模型共有多少层？",
            "options": "A. 5层\nB. 7层",
            "questionType": "single",
            "answer": "6层",
            "isPendingCorrection": True,
        })
        self.db.record_practice(question_id=q1["id"], user_answer="5层", is_correct=False)
        subj = self.db.save_subject(name="计算机网络", description="网络协议栈")
        node = self.db.save_graph_node({"subjectId": subj["id"], "name": "OSI七层模型", "summary": "物理层到应用层"})
        self.db.link_question_node(q1["id"], node["id"])

        # 1) Direct execution of new AI study/bank/graph tools
        ov, _ = execute_agent_tool("ai_get_study_overview", {}, db=self.db, config_store=self.cfg)
        self.assertTrue(ov["ok"])
        self.assertEqual(len(ov["subjects"]), 1)
        self.assertGreaterEqual(len(ov["dueNodes"]), 1)

        kg, _ = execute_agent_tool("ai_get_knowledge_graph", {"subject_name": "网络"}, db=self.db, config_store=self.cfg)
        self.assertTrue(kg["ok"])
        self.assertEqual(kg["subject"]["name"], "计算机网络")
        self.assertEqual(kg["nodeCount"], 1)

        rev, rev_eff = execute_agent_tool(
            "ai_review_knowledge_node", {"node_id": str(node["id"]), "quality": "good"}, db=self.db, config_store=self.cfg
        )
        self.assertTrue(rev["ok"])
        self.assertGreaterEqual(rev["mastery"], 1)
        self.assertTrue(any(e["type"] == "graph_changed" for e in rev_eff))

        corr, corr_eff = execute_agent_tool(
            "ai_correct_question",
            {"question_id": str(q1["id"]), "answer": "7层", "explanation": "物理层至应用层共7层", "pending": "false"},
            db=self.db,
            config_store=self.cfg,
        )
        self.assertTrue(corr["ok"])
        self.assertEqual(corr["answer"], "7层")
        self.assertFalse(corr["pendingCorrection"])
        self.assertTrue(any(e["type"] == "bank_changed" for e in corr_eff))

        pstats, _ = execute_agent_tool("ai_practice_stats", {"mode": "wrong", "limit": "5"}, db=self.db, config_store=self.cfg)
        self.assertTrue(pstats["ok"])
        self.assertEqual(pstats["total"], 1)
        self.assertEqual(len(pstats["weakQuestions"]), 1)

        # 2) Nested JSON & fenced tool_call parsing + Anthropic consecutive user message merging
        nested_text = '<tool_call>{"name":"chaoxing_start_study","arguments":{"course_ids":["c1"],"meta":{"k":1}}}</tool_call>'
        parsed_nested = _extract_react_tool_calls(nested_text)
        self.assertEqual(len(parsed_nested), 1)
        self.assertEqual(parsed_nested[0]["arguments"]["meta"]["k"], 1)

        fenced_text = '```tool_call\n{"name":"ai_get_study_overview","arguments":{}}\n```'
        parsed_fenced = _extract_react_tool_calls(fenced_text)
        self.assertEqual(len(parsed_fenced), 1)
        self.assertEqual(parsed_fenced[0]["name"], "ai_get_study_overview")

        _, anth_msgs = _to_anthropic_payload([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ])
        self.assertEqual(len(anth_msgs), 1)
        self.assertEqual(anth_msgs[0]["role"], "user")
        self.assertEqual(len(anth_msgs[0]["content"]), 2)

        # 3) Parallel read-only execution + deduplication cache + empty-content fallback synthesis
        list_calls = {"count": 0}

        class CountingBridge:
            def __init__(self):
                self.web_settings = {"speed": 1.0, "jobs": 1, "notopen_action": "retry", "selectedCourseIds": ["c-1"]}

            def list_courses(self, creds):
                list_calls["count"] += 1
                return [{"courseId": "c-1", "title": "高等数学", "clazzId": "cl-1", "cpi": "1"}]

            def get_web_settings(self, username=None):
                return dict(self.web_settings)

            def update_web_settings(self, patch):
                self.web_settings.update(patch)
                return dict(self.web_settings)

            def resume_task(self, creds, task_id=None):
                return {"task_id": task_id or "task-resumed-1", "status": "running"}

        cbridge = CountingBridge()
        # Verify string coercion in chaoxing_update_settings
        upd, _ = execute_agent_tool(
            "chaoxing_update_settings",
            {"speed": "1.5", "jobs": "4", "ai_enabled": "true"},
            db=self.db,
            config_store=self.cfg,
            chaoxing_bridge=cbridge,
        )
        self.assertTrue(upd["ok"])
        self.assertEqual(cbridge.web_settings["speed"], 1.5)
        self.assertEqual(cbridge.web_settings["jobs"], 4)
        self.assertTrue(self.cfg.load()["engine"]["enabled"])

        # Verify resume_task tool
        resumed, res_eff = execute_agent_tool(
            "chaoxing_resume_task",
            {},
            db=self.db,
            config_store=self.cfg,
            chaoxing_bridge=cbridge,
            chaoxing_ctx={"username": "13800138000", "use_cookies": True},
        )
        self.assertTrue(resumed["ok"])
        self.assertTrue(any(e["type"] == "task_started" and e["taskId"] == "task-resumed-1" for e in res_eff))

        # Parallel read-only calls + repeated read-only dedup + empty final text fallback
        seq = iter([
            ModelCallResult(
                content="",
                model_name="DeepSeek",
                tool_calls=[
                    {"id": "p1", "name": "chaoxing_list_courses", "arguments": {}},
                    {"id": "p2", "name": "ai_get_study_overview", "arguments": {}},
                ],
            ),
            ModelCallResult(
                content="",
                model_name="DeepSeek",
                tool_calls=[
                    {"id": "p3", "name": "chaoxing_list_courses", "arguments": {}},
                ],
            ),
            ModelCallResult(content="", model_name="DeepSeek"),
        ])
        plat, mod = self.cfg.resolve_agent_model()
        final_res, trace, _ = run_agent_loop(
            plat,
            mod,
            [{"role": "user", "content": "查一下"}],
            db=self.db,
            config_store=self.cfg,
            chaoxing_bridge=cbridge,
            chaoxing_ctx={"username": "13800138000", "use_cookies": True},
            llm_caller=lambda *a, **kw: next(seq),
        )
        # list_courses was requested twice, executed only ONCE thanks to read-only dedup cache
        self.assertEqual(list_calls["count"], 1)
        self.assertEqual(len(trace), 3)
        self.assertIn("已完成以下工具操作", final_res.content)

    def test_agent_memory_regenerate_rename_and_async_runs(self):
        import time

        cfg = self.cfg.load()
        cfg["platforms"][0]["apiKey"] = "sk-test-key"
        cfg["selectedTextModels"] = ["deepseek/deepseek-chat"]
        self.cfg.save(cfg)

        test_app = Flask(__name__)
        test_app.register_blueprint(
            create_ai_blueprint(lambda: self.db, lambda: self.cfg, lambda: self.engine, lambda: self.server)
        )
        client = test_app.test_client()

        seen_prompts = []

        def fake_llm(platform, model, messages, **kwargs):
            seen_prompts.append(messages)
            if len(seen_prompts) == 1:
                return ModelCallResult(
                    content="",
                    model_name="DeepSeek",
                    tool_calls=[{"id": "t1", "name": "ai_get_study_overview", "arguments": {}}],
                )
            if len(seen_prompts) == 2:
                return ModelCallResult(content="第一版回复内容", model_name="DeepSeek")
            return ModelCallResult(content="第二版重新生成回复", model_name="DeepSeek")

        with patch("api.ai.study_ai.call_model", side_effect=fake_llm):
            # Start async run & poll to completion
            r_start = client.post(
                "/api/ai/rpc",
                json={"action": "agent.chat.start", "params": {"message": "看看我的学习总览", "enableTools": True}},
            )
            self.assertEqual(r_start.status_code, 200)
            run_id = r_start.get_json()["data"]["runId"]

            final_snap = None
            for _ in range(50):
                r_poll = client.post("/api/ai/rpc", json={"action": "agent.chat.poll", "params": {"runId": run_id}})
                snap = r_poll.get_json()["data"]
                if snap["status"] != "running":
                    final_snap = snap
                    break
                time.sleep(0.02)
            self.assertIsNotNone(final_snap)
            self.assertEqual(final_snap["status"], "completed")
            sess_id = final_snap["result"]["session"]["id"]
            self.assertEqual(final_snap["result"]["reply"]["content"], "第一版回复内容")

            # Follow-up turn includes tool memory in history
            r_turn2 = client.post(
                "/api/ai/rpc",
                json={"action": "agent.chat", "params": {"sessionId": sess_id, "message": "再总结一下", "enableTools": False}},
            )
            self.assertEqual(r_turn2.status_code, 200)
            history_joined = "\n".join(m["content"] for m in seen_prompts[-1] if m["role"] == "assistant")
            self.assertIn("ai_get_study_overview", history_joined)

            # Regenerate replaces trailing assistant message without duplicating user message
            before_count = len(self.db.get_agent_session(sess_id)["messages"])
            r_regen = client.post(
                "/api/ai/rpc",
                json={"action": "agent.chat", "params": {"sessionId": sess_id, "regenerate": True, "enableTools": False}},
            )
            self.assertEqual(r_regen.status_code, 200)
            after_msgs = r_regen.get_json()["data"]["session"]["messages"]
            self.assertEqual(len(after_msgs), before_count)
            self.assertEqual(after_msgs[-1]["content"], "第二版重新生成回复")

        # Session rename & clear
        r_ren = client.post(
            "/api/ai/rpc",
            json={"action": "agent.sessions.rename", "params": {"id": sess_id, "title": "期中复习总览"}},
        )
        self.assertEqual(r_ren.status_code, 200)
        self.assertEqual(r_ren.get_json()["data"]["title"], "期中复习总览")

        r_clr = client.post(
            "/api/ai/rpc",
            json={"action": "agent.sessions.clear", "params": {"id": sess_id}},
        )
        self.assertEqual(r_clr.status_code, 200)
        self.assertEqual(r_clr.get_json()["data"]["messages"], [])

    def test_web_config_and_ai_engine_cooperation_two_way_sync(self):
        import app as web_app

        web_cfg_path = os.path.join(self.tmpdir.name, "web_config.json")
        with (
            patch.object(web_app, "CONFIG_FILE", web_cfg_path),
            patch.object(web_app, "get_ai_config", return_value=self.cfg),
            patch("api.ai.get_ai_config", return_value=self.cfg),
        ):
            client = web_app.app.test_client()

            # 1) Outer POST /api/config syncs ai_enabled & ai_priority into ai_config.json
            resp = client.post(
                "/api/config",
                json={
                    "settings": {
                        "speed": 1.5,
                        "tiku_config": {"ai_enabled": True, "ai_priority": "local_ai_only"},
                    }
                },
            )
            self.assertEqual(resp.status_code, 200)
            ai_cfg = self.cfg.load()
            self.assertTrue(ai_cfg["engine"]["enabled"])
            self.assertEqual(ai_cfg["engine"]["priority"], "local_ai_only")

            # 2) Inner AI RPC config.save syncs engine.enabled & priority back into web_config.json
            test_ai_app = Flask(__name__)
            test_ai_app.register_blueprint(
                create_ai_blueprint(
                    lambda: self.db,
                    lambda: self.cfg,
                    lambda: self.engine,
                    lambda: self.server,
                    get_chaoxing_bridge=lambda: web_app.chaoxing_agent_bridge,
                )
            )
            ai_client = test_ai_app.test_client()
            ai_cfg["engine"]["enabled"] = False
            ai_cfg["engine"]["priority"] = "local_ai_ocs"
            resp_save = ai_client.post(
                "/api/ai/rpc",
                json={"action": "config.save", "params": {"config": ai_cfg}},
            )
            self.assertEqual(resp_save.status_code, 200)

            saved_web = web_app.load_web_config()
            self.assertFalse(saved_web["settings"]["tiku_config"]["ai_enabled"])
            self.assertEqual(saved_web["settings"]["tiku_config"]["ai_priority"], "local_ai_ocs")

            # 3) Outer GET /api/config reflects ai_config.json even if web_config.json had stale/empty tiku_config
            self.cfg.update({"engine": {"enabled": True, "priority": "ocs_only"}})
            get_res = client.get("/api/config").get_json()["data"]
            self.assertEqual(get_res["aiEngine"], {"enabled": True, "priority": "ocs_only"})
            self.assertTrue(get_res["settings"]["tiku_config"]["ai_enabled"])
            self.assertEqual(get_res["settings"]["tiku_config"]["ai_priority"], "ocs_only")


if __name__ == "__main__":
    unittest.main()
