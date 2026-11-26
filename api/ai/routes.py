"""Unified Flask Blueprint for all AI features (/api/ai/rpc)."""
from __future__ import annotations

from typing import Any, Callable, Dict

from flask import Blueprint, jsonify, request

from api.logger import logger
from api.privacy import redact
from api.ai.agent_runs import agent_run_manager
from api.ai.config import SEARCH_PROVIDERS_META, default_platforms
from api.ai.llm import test_model_connection
from api.ai.search import perform_web_search
from api.ai.study_ai import (
    chat_with_tutor,
    check_practice_answer,
    explain_question_with_ai,
    generate_study_graph_with_ai,
)


def create_ai_blueprint(get_db, get_config, get_engine, get_server, get_chaoxing_bridge=None) -> Blueprint:
    bp = Blueprint("ai", __name__)

    def _handlers() -> Dict[str, Callable[[Dict[str, Any]], Any]]:
        db = get_db()
        cfg_store = get_config()
        engine = get_engine()
        server = get_server()
        chaoxing_bridge = get_chaoxing_bridge() if callable(get_chaoxing_bridge) else None

        return {
            # Config & models
            "config.get": lambda p: {
                "config": cfg_store.load(),
                "summary": cfg_store.summary_stats(),
                "searchProviders": SEARCH_PROVIDERS_META,
                "server": server.status(),
            },
            "config.save": lambda p: _save_config(cfg_store, server, p, chaoxing_bridge=chaoxing_bridge),
            "config.summary": lambda p: {
                "summary": cfg_store.summary_stats(),
                "stats": db.get_stats(),
                "server": server.status(),
            },
            "presets.platforms": lambda p: {"platforms": default_platforms()},
            "models.test": lambda p: _test_model(cfg_store, p),
            "search.test": lambda p: _test_search(cfg_store, p),

            # Local OCS HTTP server
            "server.status": lambda p: server.status(),
            "server.toggle": lambda p: _toggle_server(cfg_store, server, p),

            # Query engine & logs & stats
            "query.run": lambda p: engine.answer_question(
                {
                    "title": p.get("title") or p.get("question") or "",
                    "options": p.get("options") or "",
                    "type": p.get("type") or "single",
                    "images": p.get("images") if isinstance(p.get("images"), list) else [],
                },
                allow_local=p.get("allowLocal", True) is not False,
                allow_ai=p.get("allowAi", True) is not False,
                record_log=True,
                request_path="/test-query",
                request_method="POST",
            ),
            "logs.list": lambda p: {"items": db.list_request_logs(limit=int(p.get("limit") or 80))},
            "logs.clear": lambda p: {"deleted": db.clear_request_logs()},
            "stats.get": lambda p: db.get_stats(),

            # Folders
            "folders.list": lambda p: {"items": db.list_folders()},
            "folders.create": lambda p: db.create_folder(p.get("name", ""), int(p.get("parentId") or 0)),
            "folders.rename": lambda p: db.rename_folder(int(p["id"]), p.get("name", "")),
            "folders.move": lambda p: (db.move_folder(int(p["id"]), int(p.get("targetParentId") or 0)) or {"ok": True}),
            "folders.delete": lambda p: (
                db.delete_folder(int(p["id"]), delete_questions=bool(p.get("deleteQuestions", False))) or {"ok": True}
            ),
            "folders.clear": lambda p: {"deleted": db.clear_folder(int(p["id"]))},

            # Questions
            "questions.list": lambda p: db.list_questions(
                folder_id=p.get("folderId") if p.get("folderId") is not None else None,
                keyword=str(p.get("keyword") or ""),
                question_type=str(p.get("questionType") or ""),
                source=str(p.get("source") or "all"),
                pending_only=bool(p.get("pendingOnly", False)),
                page=int(p.get("page") or 1),
                page_size=int(p.get("pageSize") or 50),
            ),
            "questions.save": lambda p: db.save_question(p),
            "questions.delete": lambda p: {"deleted": db.delete_questions(p.get("ids") or [])},
            "questions.move": lambda p: {"moved": db.move_questions(p.get("ids") or [], int(p.get("targetFolderId") or 0))},
            "questions.correction": lambda p: db.set_pending_correction(
                int(p["id"]),
                bool(p.get("pending", False)),
                corrected_answer=p.get("answer"),
                explanation=p.get("explanation"),
            ),
            "questions.import": lambda p: db.import_questions(
                fmt=str(p.get("format") or "json"),
                content=str(p.get("content") or ""),
                folder_id=int(p.get("folderId") or 0),
            ),
            "questions.export": lambda p: db.export_questions(
                fmt=str(p.get("format") or "json"),
                folder_id=p.get("folderId") if p.get("folderId") is not None else None,
            ),

            # Practice
            "practice.next": lambda p: {
                "items": db.list_practice_candidates(
                    folder_id=p.get("folderId") if p.get("folderId") is not None else None,
                    question_type=str(p.get("questionType") or ""),
                    mode=str(p.get("mode") or "smart"),
                    limit=int(p.get("limit") or 20),
                )
            },
            "practice.submit": lambda p: _submit_practice(db, p),
            "practice.explain": lambda p: explain_question_with_ai(
                db, cfg_store, int(p["questionId"]), str(p.get("userAnswer") or "")
            ),
            "practice.history": lambda p: db.list_practice_history(limit=int(p.get("limit") or 50)),

            # Study Knowledge Graph
            "study.subjects.list": lambda p: {"items": db.list_subjects()},
            "study.subjects.save": lambda p: db.save_subject(
                name=str(p.get("name") or ""),
                description=str(p.get("description") or ""),
                subject_id=int(p["id"]) if p.get("id") is not None else None,
            ),
            "study.subjects.delete": lambda p: (db.delete_subject(int(p["id"])) or {"ok": True}),
            "study.graph.get": lambda p: db.get_study_graph(int(p["subjectId"])),
            "study.graph.node.save": lambda p: db.save_graph_node(p),
            "study.graph.node.delete": lambda p: (db.delete_graph_node(int(p["id"])) or {"ok": True}),
            "study.graph.edge.save": lambda p: db.save_graph_edge(
                int(p["subjectId"]), int(p["fromId"]), int(p["toId"]), str(p.get("relation") or "")
            ),
            "study.graph.edge.delete": lambda p: (db.delete_graph_edge(int(p["id"])) or {"ok": True}),
            "study.graph.link": lambda p: (
                db.link_question_node(int(p["questionId"]), int(p["nodeId"]), unlink=bool(p.get("unlink", False)))
                or {"ok": True}
            ),
            "study.graph.review": lambda p: db.review_graph_node(int(p["nodeId"]), str(p.get("quality") or "good")),
            "study.graph.generate": lambda p: generate_study_graph_with_ai(
                db,
                cfg_store,
                subject_id=int(p["subjectId"]),
                topic=str(p.get("topic") or ""),
                folder_id=int(p["folderId"]) if p.get("folderId") is not None and str(p.get("folderId")) != "" else None,
            ),

            # AI Tutor Agent Chat
            "agent.sessions.list": lambda p: {"items": db.list_agent_sessions()},
            "agent.sessions.create": lambda p: db.create_agent_session(str(p.get("title") or "新对话")),
            "agent.sessions.rename": lambda p: db.rename_agent_session(str(p["id"]), str(p.get("title") or "")),
            "agent.sessions.clear": lambda p: db.clear_agent_session(str(p["id"])),
            "agent.sessions.delete": lambda p: (db.delete_agent_session(str(p["id"])) or {"ok": True}),
            "agent.chat": lambda p: chat_with_tutor(
                db,
                cfg_store,
                session_id=str(p.get("sessionId") or ""),
                message=str(p.get("message") or ""),
                include_bank=p.get("includeBank", True) is not False,
                include_mistakes=bool(p.get("includeMistakes", False)),
                subject_id=int(p["subjectId"]) if p.get("subjectId") is not None and str(p.get("subjectId")) != "" else None,
                enable_tools=p.get("enableTools", True) is not False,
                model_id=str(p.get("modelId") or "").strip() or None,
                regenerate=bool(p.get("regenerate", False)),
                engine=engine,
                chaoxing_bridge=chaoxing_bridge,
                chaoxing_ctx=p.get("chaoxing") if isinstance(p.get("chaoxing"), dict) else None,
            ),
            "agent.chat.start": lambda p: agent_run_manager.start_run(
                lambda on_event=None, cancel_event=None: chat_with_tutor(
                    db,
                    cfg_store,
                    session_id=str(p.get("sessionId") or ""),
                    message=str(p.get("message") or ""),
                    include_bank=p.get("includeBank", True) is not False,
                    include_mistakes=bool(p.get("includeMistakes", False)),
                    subject_id=int(p["subjectId"]) if p.get("subjectId") is not None and str(p.get("subjectId")) != "" else None,
                    enable_tools=p.get("enableTools", True) is not False,
                    model_id=str(p.get("modelId") or "").strip() or None,
                    regenerate=bool(p.get("regenerate", False)),
                    engine=engine,
                    chaoxing_bridge=chaoxing_bridge,
                    chaoxing_ctx=p.get("chaoxing") if isinstance(p.get("chaoxing"), dict) else None,
                    on_event=on_event,
                    cancel_event=cancel_event,
                )
            ),
            "agent.chat.poll": lambda p: agent_run_manager.poll_run(str(p.get("runId") or "")),
            "agent.chat.cancel": lambda p: agent_run_manager.cancel_run(str(p.get("runId") or "")),
        }

    @bp.route("/api/ai/rpc", methods=["POST"])
    def ai_rpc():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"status": False, "msg": "请求体必须为 JSON 对象"}), 400
        action = str(body.get("action") or "").strip()
        params = body.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return jsonify({"status": False, "msg": "params 必须为 JSON 对象"}), 400

        handlers = _handlers()
        handler = handlers.get(action)
        if handler is None:
            return jsonify({"status": False, "msg": f"未知 AI 操作: {action}"}), 400
        try:
            result = handler(params)
            return jsonify({"status": True, "data": result})
        except ValueError as exc:
            return jsonify({"status": False, "msg": redact(exc)}), 400
        except KeyError as exc:
            return jsonify({"status": False, "msg": f"缺少必要参数: {exc}"}), 400
        except Exception as exc:
            logger.error("AI RPC {} 执行异常: {}", action, exc)
            return jsonify({"status": False, "msg": redact(exc)}), 500

    return bp


def _save_config(cfg_store, server, params: Dict[str, Any], chaoxing_bridge=None) -> Dict[str, Any]:
    if "config" in params and isinstance(params["config"], dict):
        saved = cfg_store.save(params["config"])
    else:
        saved = cfg_store.update(params)
    # Keep web_config.json tiku_config ai_enabled/ai_priority synchronized with ai_config.json
    if chaoxing_bridge is not None and hasattr(chaoxing_bridge, "sync_tiku_ai_settings"):
        try:
            chaoxing_bridge.sync_tiku_ai_settings(
                ai_enabled=bool(saved["engine"].get("enabled", False)),
                ai_priority=saved["engine"].get("priority"),
            )
        except Exception as exc:
            logger.warning("同步题库协同配置到 Web 配置失败: {}", exc)
    # Sync local server state if enabled flag/port changed
    desired_running = bool(saved["engine"].get("localServerEnabled", False))
    desired_port = int(saved["engine"].get("localServerPort", 3005))
    cur = server.status()
    if desired_running and (not cur["running"] or cur["port"] != desired_port):
        server.start(desired_port)
    elif not desired_running and cur["running"]:
        server.stop()
    return {
        "config": saved,
        "summary": cfg_store.summary_stats(),
        "server": server.status(),
    }


def _toggle_server(cfg_store, server, params: Dict[str, Any]) -> Dict[str, Any]:
    enable = bool(params.get("enable"))
    port = params.get("port")
    patch: Dict[str, Any] = {"localServerEnabled": enable}
    if port is not None:
        patch["localServerPort"] = int(port)
    if "token" in params:
        patch["localServerToken"] = str(params.get("token") or "")
    saved = cfg_store.update({"engine": patch})
    if enable:
        status = server.start(saved["engine"]["localServerPort"])
        if not status["running"]:
            cfg_store.update({"engine": {"localServerEnabled": False}})
    else:
        status = server.stop()
    return status


def _test_model(cfg_store, params: Dict[str, Any]) -> Dict[str, Any]:
    platform = params.get("platform")
    model = params.get("model")
    if isinstance(platform, dict) and isinstance(model, dict):
        return test_model_connection(platform, model)
    model_id = str(params.get("modelId") or "").strip()
    pair = cfg_store.find_model(model_id)
    if not pair:
        raise ValueError("未找到指定模型，请先保存配置")
    return test_model_connection(pair[0], pair[1])


def _test_search(cfg_store, params: Dict[str, Any]) -> Dict[str, Any]:
    cfg = cfg_store.load()
    search_cfg = dict(cfg["search"])
    if isinstance(params.get("search"), dict):
        search_cfg.update(params["search"])
    query = str(params.get("query") or "人工智能的定义").strip()
    return perform_web_search(query, search_cfg)


def _submit_practice(db, params: Dict[str, Any]) -> Dict[str, Any]:
    qid = int(params["questionId"])
    user_answer = str(params.get("userAnswer") or "")
    q = db.get_question(qid)
    if not q:
        raise ValueError("题目不存在")
    if "isCorrect" in params and isinstance(params["isCorrect"], bool):
        is_correct = params["isCorrect"]
    else:
        is_correct = check_practice_answer(q, user_answer)
    rec = db.record_practice(
        question_id=qid,
        user_answer=user_answer,
        is_correct=is_correct,
        note=str(params.get("note") or ""),
        source=str(params.get("source") or "practice"),
    )
    return {
        **rec,
        "correctAnswer": q["answer"],
        "explanation": q.get("explanation") or "",
    }
