"""AI services for Practice grading/explanation, Study Knowledge Graph generation, and AI Tutor Agent chat."""
from __future__ import annotations

from datetime import datetime
import json
import re
import threading
from typing import Any, Callable, Dict, List, Optional

from api.answer_check import match_answer
from api.privacy import redact
from api.ai.agent_tools import run_agent_loop
from api.ai.config import AIConfigStore
from api.ai.db import AIDB, normalize_for_match
from api.ai.engine import strip_markdown_code_block
from api.ai.forgetting import utc_now_iso
from api.ai.llm import call_model

_HISTORY_MAX_MESSAGES = 20
_HISTORY_MAX_CHARS = 16000
_CHAOXING_COMMAND_HINT_RE = re.compile(
    r"^(帮我|请)?(查看|列出|看看|获取|停止|暂停|取消|恢复|启动|开始|打开|关闭|修改|设置).{0,24}(课程|章节|刷课|任务|进度|日志|关机|倍速|并发|协同|设置)",
    flags=re.I,
)


def _resolve_model_or_raise(config_store: AIConfigStore, model_id: Optional[str] = None):
    cfg = config_store.load()
    pair = None
    if model_id:
        pair = config_store.find_model(str(model_id).strip(), cfg)
    if not pair:
        pair = config_store.resolve_agent_model(cfg)
    if not pair:
        raise ValueError("请先在「模型设置」中配置 API Key 并选择一个可用 AI 模型")
    return cfg, pair[0], pair[1]


def _format_tool_memory_note(tool_calls: Any) -> str:
    if not isinstance(tool_calls, list) or not tool_calls:
        return ""
    notes: List[str] = []
    for tc in tool_calls[:6]:
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("name") or "").strip()
        summary = str(tc.get("summary") or "").strip()
        status = "成功" if tc.get("ok") else "失败"
        extra_bits: List[str] = []
        preview = str(tc.get("resultPreview") or "")
        for key in ("taskId", "courseId"):
            m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', preview)
            if m:
                extra_bits.append(f"{key}={m.group(1)}")
        extra = f" ({', '.join(extra_bits)})" if extra_bits else ""
        notes.append(f"{name}[{status}]: {summary[:80]}{extra}")
    if not notes:
        return ""
    return "\n\n[本轮已调用工具记录: " + "; ".join(notes) + "]"


def _build_history_messages(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for item in history[-_HISTORY_MAX_MESSAGES:]:
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if role == "assistant" and item.get("toolCalls"):
            content = content + _format_tool_memory_note(item.get("toolCalls"))
        candidates.append({"role": role, "content": content})

    if not candidates:
        return []

    # Trim oldest turns if total character length exceeds budget, always keeping latest 4 messages
    kept_reversed: List[Dict[str, Any]] = []
    total_chars = 0
    for idx, msg in enumerate(reversed(candidates)):
        msg_len = len(msg["content"])
        if idx >= 4 and total_chars + msg_len > _HISTORY_MAX_CHARS:
            break
        kept_reversed.append(msg)
        total_chars += msg_len
    return list(reversed(kept_reversed))


def _clean_session_title(text: str) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact[:28] or "新对话"


def check_practice_answer(question: Dict[str, Any], user_answer: str) -> bool:
    correct = str(question.get("answer") or "").strip()
    given = str(user_answer or "").strip()
    if not correct or not given:
        return False
    if normalize_for_match(correct) == normalize_for_match(given):
        return True
    qtype = str(question.get("questionType") or "").strip().lower()
    if qtype in ("multiple", "completion"):
        expected_parts = {normalize_for_match(p) for p in re.split(r"###|[;；,，\n]+", correct) if normalize_for_match(p)}
        given_parts = {normalize_for_match(p) for p in re.split(r"###|[;；,，\n]+", given) if normalize_for_match(p)}
        if expected_parts and expected_parts == given_parts:
            return True
    if qtype in ("judgement", "judgment"):
        t_set = {"正确", "对", "t", "true", "√", "1", "yes", "是"}
        f_set = {"错误", "错", "f", "false", "×", "0", "no", "否"}
        c_low, g_low = correct.lower(), given.lower()
        if (c_low in t_set and g_low in t_set) or (c_low in f_set and g_low in f_set):
            return True
    # Try chaoxing option matcher (handles A/B/C/D vs option text!)
    opts = [ln.strip() for ln in str(question.get("options") or "").splitlines() if ln.strip()]
    if opts:
        q_info = {"title": question.get("question", ""), "options": opts, "type": qtype or "single"}
        m_corr = match_answer(correct, q_info)
        m_user = match_answer(given, q_info)
        if m_corr.answer is not None and m_user.answer is not None and m_corr.answer == m_user.answer:
            return True
    return False


def explain_question_with_ai(
    db: AIDB,
    config_store: AIConfigStore,
    question_id: int,
    user_answer: str = "",
) -> Dict[str, Any]:
    q = db.get_question(question_id)
    if not q:
        raise ValueError("题目不存在")
    cfg, platform, model = _resolve_model_or_raise(config_store)
    prompt = [
        "你是耐心的学科辅导老师，请为下面这道题提供清晰、准确的中文解析（包含核心考点、排除干扰项理由和记忆要点）。",
        f"【题型】{q.get('questionType') or '通用'}",
        f"【题目】{q['question']}",
    ]
    if q.get("options"):
        prompt.append(f"【选项】\n{q['options']}")
    prompt.append(f"【参考答案】{q['answer']}")
    if user_answer:
        prompt.append(f"【学生作答】{user_answer}")
    res = call_model(
        platform,
        model,
        [{"role": "user", "content": "\n".join(prompt)}],
        timeout=int(cfg["engine"].get("modelTimeout", 45)),
        retry_count=1,
    )
    explanation = res.content.strip()
    if explanation:
        db.save_question({**q, "explanation": explanation})
    return {
        "questionId": question_id,
        "explanation": explanation,
        "reasoning": res.reasoning,
        "modelName": res.model_name,
    }


def generate_study_graph_with_ai(
    db: AIDB,
    config_store: AIConfigStore,
    *,
    subject_id: int,
    topic: str = "",
    folder_id: Optional[int] = None,
) -> Dict[str, Any]:
    graph = db.get_study_graph(subject_id)
    subject = graph["subject"]
    cfg, platform, model = _resolve_model_or_raise(config_store)

    sample_questions: List[Dict[str, Any]] = []
    if folder_id is not None and folder_id >= 0:
        sample_questions = db.list_questions(folder_id=folder_id, page=1, page_size=40)["items"]

    q_block = ""
    if sample_questions:
        lines = [f"- (id={q['id']}) {q['question'][:100]} -> 答案: {q['answer'][:60]}" for q in sample_questions[:30]]
        q_block = "\n【参考题库题目（请尽量提取这些题涉及的知识点，并在 linkedQuestionIds 中填入对应 id）】\n" + "\n".join(lines)

    prompt = (
        f"你是课程知识图谱设计师。请为学科「{subject['name']}」"
        + (f"（说明：{subject['description']}）" if subject.get("description") else "")
        + (f"，聚焦主题「{topic}」" if topic else "")
        + "生成结构化的知识图谱。\n"
        "请只输出一个合法 JSON 对象（不要代码块、不要多余文字），格式如下：\n"
        '{"nodes":[{"key":"k1","name":"核心概念","summary":"50字内精炼概括","parentKey":null,"linkedQuestionIds":[1,2]}],'
        '"edges":[{"fromKey":"k1","toKey":"k2","relation":"前置/包含/推导"}]}\n'
        "要求：生成 6 到 14 个清晰的知识点节点，层级合理，关系有意义。"
        + q_block
    )

    res = call_model(
        platform,
        model,
        [{"role": "user", "content": prompt}],
        timeout=max(60, int(cfg["engine"].get("modelTimeout", 45))),
        retry_count=1,
        max_tokens=4096,
    )
    raw_text = strip_markdown_code_block(res.content)
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI 未返回有效的知识图谱 JSON")
    parsed = json.loads(raw_text[start : end + 1])
    nodes_spec = parsed.get("nodes") or []
    edges_spec = parsed.get("edges") or []
    if not isinstance(nodes_spec, list) or not nodes_spec:
        raise ValueError("AI 生成的知识点列表为空")

    valid_qids = {int(q["id"]) for q in sample_questions}
    key_to_id: Dict[str, int] = {}
    created_nodes: List[Dict[str, Any]] = []

    # Pass 1: create nodes
    for idx, spec in enumerate(nodes_spec[:24]):
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        key = str(spec.get("key") or f"n{idx + 1}").strip()
        saved = db.save_graph_node(
            {
                "subjectId": subject_id,
                "nodeKey": key,
                "name": name,
                "summary": str(spec.get("summary") or "").strip(),
                "sortOrder": idx,
            }
        )
        key_to_id[key] = saved["id"]
        created_nodes.append(saved)
        for qid in spec.get("linkedQuestionIds") or []:
            try:
                qid_int = int(qid)
                if qid_int in valid_qids:
                    db.link_question_node(qid_int, saved["id"])
            except (TypeError, ValueError):
                continue

    # Pass 2: wire parentId + edges
    for spec in nodes_spec[:24]:
        if not isinstance(spec, dict):
            continue
        key = str(spec.get("key") or "").strip()
        pkey = str(spec.get("parentKey") or "").strip()
        if key in key_to_id and pkey in key_to_id and key_to_id[key] != key_to_id[pkey]:
            node_row = next((n for n in created_nodes if n["id"] == key_to_id[key]), None)
            if node_row:
                db.save_graph_node({**node_row, "parentId": key_to_id[pkey]})
            db.save_graph_edge(subject_id, key_to_id[pkey], key_to_id[key], "包含")

    for edge in edges_spec[:40]:
        if not isinstance(edge, dict):
            continue
        fk = str(edge.get("fromKey") or "").strip()
        tk = str(edge.get("toKey") or "").strip()
        if fk in key_to_id and tk in key_to_id and key_to_id[fk] != key_to_id[tk]:
            db.save_graph_edge(subject_id, key_to_id[fk], key_to_id[tk], str(edge.get("relation") or "").strip())

    return {
        "graph": db.get_study_graph(subject_id),
        "createdNodeCount": len(created_nodes),
        "modelName": res.model_name,
    }


def chat_with_tutor(
    db: AIDB,
    config_store: AIConfigStore,
    *,
    session_id: str,
    message: str = "",
    include_bank: bool = True,
    include_mistakes: bool = False,
    subject_id: Optional[int] = None,
    enable_tools: bool = True,
    model_id: Optional[str] = None,
    regenerate: bool = False,
    engine: Any = None,
    chaoxing_bridge: Any = None,
    chaoxing_ctx: Optional[Dict[str, Any]] = None,
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    text = str(message or "").strip()
    sess = db.get_agent_session(session_id) if session_id else None
    history = list(sess.get("messages") or []) if sess else []

    if regenerate:
        if not sess:
            raise ValueError("当前会话不存在，无法重新生成")
        while history and history[-1].get("role") == "assistant":
            history.pop()
        if not text:
            if history and history[-1].get("role") == "user":
                text = str(history.pop().get("content") or "").strip()
            else:
                raise ValueError("没有可重新生成的上一条提问")
        elif history and history[-1].get("role") == "user" and str(history[-1].get("content") or "").strip() == text:
            history.pop()

    if not text:
        raise ValueError("消息不能为空")
    if not sess:
        sess = db.create_agent_session(title=_clean_session_title(text))
        session_id = sess["id"]

    cfg, platform, model = _resolve_model_or_raise(config_store, model_id=model_id)

    # Assemble rich AI study context (local bank hits, mistakes, knowledge graph)
    context_blocks: List[str] = []
    now_local = datetime.now().strftime("%Y-%m-%d %H:%M")
    stats = db.get_stats()
    context_blocks.append(
        f"【当前时间】{now_local}\n"
        f"【学习者概况】本地题库共 {stats['totalQuestions']} 题（待修正 {stats['pendingCorrections']} 题），"
        f"知识图谱学科 {stats['subjectCount']} 个、知识点 {stats['knowledgeNodeCount']} 个，累计刷题 {stats['practiceTotal']} 次。"
    )

    if enable_tools:
        cx_user = str((chaoxing_ctx or {}).get("username") or "").strip()
        cx_task = str((chaoxing_ctx or {}).get("activeTaskId") or "").strip()
        cx_status = str((chaoxing_ctx or {}).get("taskStatus") or "").strip()
        if cx_user:
            task_hint = f"当前活动任务 ID：{cx_task}（状态：{cx_status or '未知'}）" if cx_task else "当前无前端跟踪中的活动任务"
            context_blocks.append(f"【超星学习通会话状态】已登录账号：{cx_user}；{task_hint}。")
        else:
            context_blocks.append("【超星学习通会话状态】当前尚未登录超星学习通账号（如需操控学习通课程/刷课，请提示用户先登录；本地题库、知识图谱与答疑仍可正常使用）。")

    is_pure_ops_cmd = enable_tools and len(text) <= 45 and bool(_CHAOXING_COMMAND_HINT_RE.search(text))
    if include_bank and not is_pure_ops_cmd:
        related = db.find_similar_candidates(text, limit=5, min_ratio=0.25)
        if not related and len(text) >= 4:
            related = db.list_questions(keyword=text[:20], page=1, page_size=5)["items"]
        if related:
            lines = [f"- [ID#{q['id']}] 题目：{q['question'][:120]} | 答案：{q['answer'][:80]}" for q in related[:5]]
            context_blocks.append("【本地题库相关题目】\n" + "\n".join(lines))

    if include_mistakes and not is_pure_ops_cmd:
        mistakes = db.list_practice_candidates(mode="wrong", limit=6)
        if mistakes:
            lines = [
                f"- [ID#{q['id']}] 错题/待修正：{q['question'][:100]} | 答案：{q['answer'][:60]} | 掌握度：{q['mastery']}/3"
                for q in mistakes
            ]
            context_blocks.append("【当前薄弱错题】\n" + "\n".join(lines))

    if subject_id is not None:
        try:
            g = db.get_study_graph(int(subject_id))
            node_lines = [
                f"- [nodeId={n['id']}] {n['name']}（掌握度 {n['mastery']}/3，记忆留存 {int(n['retention'] * 100)}%，下次复习：{n.get('nextReviewDue') or '未安排'}）：{n['summary'][:60]}"
                for n in g["nodes"][:20]
            ]
            if node_lines:
                context_blocks.append(f"【当前学科知识图谱：{g['subject']['name']}】\n" + "\n".join(node_lines))
        except Exception:
            pass

    agent_instructions = (
        "你还具备强大的 Agent 工具调用能力，可直接协同操控超星学习通、本地题库与艾宾浩斯知识图谱：\n"
        "- 学习通运维：查询课程列表、检查章节完成进度、启动/停止/恢复刷课任务、运行学习次数/目录/阅读时长工具、查看实时进度与日志、取消自动关机、读写刷课与题库协同设置。\n"
        "- 题库与学习闭环：检索/保存/修正本地题库题目、调用多模型查题引擎、获取学习总览与到期复习清单、查询学科知识图谱、记录知识点复习评价、查询刷题统计与错题，以及在已开启联网搜索时执行实时搜索。\n"
        "工具调用原则：\n"
        "1. 普通答疑、概念讲解直接回答；当用户询问真实课程/任务/题库/错题/知识图谱数据时，主动调用相应只读工具核实，严禁编造课程 ID、章节进度、任务 ID 或题库内容。\n"
        "2. 需要同时查询多项独立信息时（例如查多门课程章节，或同时查课程列表与当前任务状态），可以在单步内并行发起多个只读工具调用以提高效率。\n"
        "3. 启动刷课或查单门课章节时，若不确定 courseId，先调用 chaoxing_list_courses 获取真实课程列表；仅当用户明确要求「完成后关机」时才将 shutdown_on_complete 设为 true。\n"
        "4. 工具执行完毕后，必须基于工具返回的真实数据，用简洁清晰的 Markdown（适时使用表格展示课程列表、章节进度或复习计划）向用户汇报结果、关键 ID 或失败原因。\n"
    ) if enable_tools else ""

    system_prompt = (
        "你是集成在超星学习通工具中的 AI 学习助教与智能操控 Agent。\n"
        "你擅长讲解课程知识点、解析题目、针对错题出练习题、规划艾宾浩斯复习计划，以及高效协助管理学习通任务。\n"
        + agent_instructions
        + "回复要求：结构清晰、准确、直击考点。讲题时说明核心考点与排除干扰项依据；出练习题时在每题后附带明确答案和简明解析。\n\n"
        + "\n\n".join(context_blocks)
    )

    llm_messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    llm_messages.extend(_build_history_messages(history))
    llm_messages.append({"role": "user", "content": text})

    call_timeout = max(60, int(cfg["engine"].get("modelTimeout", 45)))
    tool_calls_trace: List[Dict[str, Any]] = []
    ui_effects: List[Dict[str, Any]] = []

    if enable_tools:
        res, tool_calls_trace, ui_effects = run_agent_loop(
            platform,
            model,
            llm_messages,
            db=db,
            config_store=config_store,
            engine=engine,
            chaoxing_bridge=chaoxing_bridge,
            chaoxing_ctx=chaoxing_ctx,
            timeout=call_timeout,
            llm_caller=call_model,
            on_event=on_event,
            cancel_event=cancel_event,
        )
    else:
        if on_event is not None:
            try:
                on_event({"type": "step_start", "step": 1, "maxSteps": 1, "phase": "thinking"})
            except Exception:
                pass
        res = call_model(
            platform,
            model,
            llm_messages,
            timeout=call_timeout,
            retry_count=1,
        )

    now_iso = utc_now_iso()
    user_entry = {"role": "user", "content": text, "timestamp": now_iso}
    assistant_entry: Dict[str, Any] = {
        "role": "assistant",
        "content": res.content,
        "reasoning": res.reasoning,
        "modelName": res.model_name,
        "durationMs": res.duration_ms,
        "timestamp": utc_now_iso(),
    }
    if tool_calls_trace:
        assistant_entry["toolCalls"] = tool_calls_trace
    history.extend([user_entry, assistant_entry])
    new_title = sess["title"]
    if new_title in ("新对话", "快捷辅导", "") and len(history) <= 2:
        new_title = _clean_session_title(text)
    updated = db.update_agent_session(session_id, history, title=new_title)
    return {
        "session": updated,
        "reply": assistant_entry,
        "toolCalls": tool_calls_trace,
        "uiEffects": ui_effects,
    }
