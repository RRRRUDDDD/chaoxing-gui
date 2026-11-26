"""Agent tool definitions, Chaoxing + AI tool executor, and multi-step tool loop."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from api.logger import logger
from api.privacy import redact
from api.ai.config import VALID_PRIORITIES, AIConfigStore
from api.ai.db import AIDB
from api.ai.forgetting import utc_now_iso
from api.ai.llm import ModelCallError, ModelCallResult, call_model
from api.ai.search import perform_web_search

MAX_AGENT_STEPS = 8
MAX_TOOLS_PER_STEP = 6


class AgentRunCancelled(RuntimeError):
    """Raised when an in-flight agent run is cancelled by the user."""


TOOL_LABELS: Dict[str, str] = {
    "chaoxing_list_courses": "获取学习通课程列表",
    "chaoxing_get_course_chapters": "查询课程章节进度",
    "chaoxing_start_study": "启动学习通自动刷课",
    "chaoxing_start_course_tool": "启动学习通课程工具",
    "chaoxing_get_task_status": "查询学习通任务状态",
    "chaoxing_stop_task": "停止学习通任务",
    "chaoxing_resume_task": "恢复中断的学习通任务",
    "chaoxing_cancel_shutdown": "取消自动关机计划",
    "chaoxing_get_settings": "读取学习通与题库设置",
    "chaoxing_update_settings": "修改学习通与题库设置",
    "ai_search_bank": "检索本地题库",
    "ai_save_question": "保存题目到本地题库",
    "ai_correct_question": "修正本地题库题目",
    "ai_answer_question": "调用 AI 查题引擎答题",
    "ai_get_study_overview": "读取学习总览与到期复习",
    "ai_get_knowledge_graph": "查询学科知识图谱",
    "ai_review_knowledge_node": "记录知识点复习评价",
    "ai_practice_stats": "查询刷题统计与薄弱错题",
    "ai_web_search": "执行联网搜索",
}

READ_ONLY_TOOLS = frozenset({
    "chaoxing_list_courses",
    "chaoxing_get_course_chapters",
    "chaoxing_get_task_status",
    "chaoxing_get_settings",
    "ai_search_bank",
    "ai_get_study_overview",
    "ai_get_knowledge_graph",
    "ai_practice_stats",
    "ai_web_search",
})


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes", "on", "是"):
            return True
        if low in ("false", "0", "no", "off", "否"):
            return False
    return None


def _coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def build_agent_tools(*, search_enabled: bool = True) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "chaoxing_list_courses",
                "description": "获取当前已登录超星学习通账号的全部课程列表（含 courseId、课程名称、教师、是否已保存在默认选课中）。在查看课程、查章节或启动刷课前可调用此工具。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chaoxing_get_course_chapters",
                "description": "查询指定学习通课程的章节目录和学习完成进度（总章节数、已完成数、未完成章节列表、完成率与待完成任务点数）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "course_id": {
                            "type": "string",
                            "description": "课程 ID（courseId），也可传入课程名称关键词自动匹配。",
                        },
                    },
                    "required": ["course_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chaoxing_start_study",
                "description": "启动超星学习通自动刷课任务（自动播放视频、完成文档阅读与章节测验）。同一账号同时只能运行一个任务。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "course_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "要学习的课程 ID 列表（也支持传课程名称关键词，会自动匹配为课程 ID）。",
                        },
                        "speed": {
                            "type": "number",
                            "description": "视频播放倍速，范围 1.0 到 2.0，默认沿用已保存配置。",
                        },
                        "jobs": {
                            "type": "integer",
                            "description": "并发任务数，范围 1 到 8，默认沿用已保存配置。",
                        },
                        "notopen_action": {
                            "type": "string",
                            "enum": ["retry", "continue"],
                            "description": "遇到未开放章节时的策略：retry（重试等待）或 continue（跳过继续）。",
                        },
                        "shutdown_on_complete": {
                            "type": "boolean",
                            "description": "任务全部完成后是否自动关机，默认 false（仅当用户明确要求自动关机时设为 true）。",
                        },
                    },
                    "required": ["course_ids"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chaoxing_start_course_tool",
                "description": "启动学习通课程辅助工具任务：visits（刷课程学习次数）、catalog（读取课程视频/阅读/下载资源清单）、read_time（按章节链接累计阅读时长）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_type": {
                            "type": "string",
                            "enum": ["visits", "catalog", "read_time"],
                            "description": "工具类型：visits / catalog / read_time。",
                        },
                        "course_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "课程 ID 或名称列表（visits / catalog 必填）。",
                        },
                        "count": {
                            "type": "integer",
                            "description": "visits 模式：提交学习次数（1-1000，默认 10）。",
                        },
                        "interval": {
                            "type": "number",
                            "description": "visits 模式：每次请求间隔秒数（1-3600，默认 30）。",
                        },
                        "purpose": {
                            "type": "string",
                            "enum": ["video_time", "read_time", "download"],
                            "description": "catalog 模式：要读取的资源类型。",
                        },
                        "urls": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "read_time 模式：学习通章节链接列表。",
                        },
                        "minutes": {
                            "type": "number",
                            "description": "read_time 模式：每个链接目标阅读分钟数（0.1-1440，默认 30）。",
                        },
                        "shutdown_on_complete": {
                            "type": "boolean",
                            "description": "完成后是否自动关机（catalog 不生效）。",
                        },
                    },
                    "required": ["task_type"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chaoxing_get_task_status",
                "description": "查询当前学习通后台任务的执行状态、课程/章节进度统计、正在播放的视频任务、最近任务历史以及最近执行日志。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "可选任务 ID；不填则自动查询当前运行中或最近一次任务。",
                        },
                        "log_limit": {
                            "type": "integer",
                            "description": "返回最近多少条日志（默认 15，最多 40）。",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chaoxing_stop_task",
                "description": "停止当前正在运行的学习通刷课或课程工具任务。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "可选任务 ID；不填则自动停止当前运行中的任务。",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chaoxing_resume_task",
                "description": "恢复上次因程序退出而中断（interrupted）的学习通刷课或课程工具任务。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "可选任务 ID；不填则自动尝试恢复最近一次可恢复任务。",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chaoxing_cancel_shutdown",
                "description": "取消当前任务已计划的完成后自动关机。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": "可选任务 ID；不填则针对当前任务。",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chaoxing_get_settings",
                "description": "读取当前学习通刷课默认参数（倍速、并发数、未开放章节策略、已保存选课）以及 AI 题库协同引擎配置。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "chaoxing_update_settings",
                "description": "修改并保存学习通默认刷课参数或 AI 题库协同策略（如开启/关闭刷课 AI 协同、调整查题优先级、倍速、并发数、联网搜题等）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "speed": {"type": "number", "description": "默认视频倍速（1.0-2.0）"},
                        "jobs": {"type": "integer", "description": "默认并发数（1-8）"},
                        "notopen_action": {"type": "string", "enum": ["retry", "continue"]},
                        "ai_enabled": {"type": "boolean", "description": "刷课时是否启用 AI 题库协同"},
                        "ai_priority": {
                            "type": "string",
                            "enum": list(VALID_PRIORITIES),
                            "description": "查题优先级：local_ocs_ai / local_ai_ocs / local_ai_only / ocs_only",
                        },
                        "auto_save_to_bank": {"type": "boolean", "description": "新查到的答案是否自动入库"},
                        "search_enabled": {"type": "boolean", "description": "是否启用联网搜题增强"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ai_search_bank",
                "description": "在本地 AI SQLite 题库中按关键词、文件夹或题型检索题目，或列出待修正错题。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "搜索关键词（可为空）"},
                        "pending_only": {"type": "boolean", "description": "是否仅查看待修正错题"},
                        "folder_id": {"type": "integer", "description": "可选：按题库文件夹 ID 筛选"},
                        "question_type": {
                            "type": "string",
                            "enum": ["single", "multiple", "judgement", "completion", "short"],
                            "description": "可选：按题型筛选",
                        },
                        "limit": {"type": "integer", "description": "返回条数上限（默认 8，最多 20）"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ai_save_question",
                "description": "将一道题目、答案及解析保存到本地 AI 题库。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "题目题干"},
                        "answer": {"type": "string", "description": "标准答案"},
                        "options": {"type": "string", "description": "选项（可选，每行一个）"},
                        "question_type": {
                            "type": "string",
                            "enum": ["single", "multiple", "judgement", "completion", "short"],
                            "description": "题型",
                        },
                        "explanation": {"type": "string", "description": "解析（可选）"},
                        "folder_id": {"type": "integer", "description": "目标文件夹 ID（默认 0 为默认文件夹）"},
                    },
                    "required": ["question", "answer"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ai_correct_question",
                "description": "修正本地题库中指定 ID 题目的标准答案与解析，并可解除其「待修正」状态。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "integer", "description": "本地题库题目 ID"},
                        "answer": {"type": "string", "description": "修正后的标准答案"},
                        "explanation": {"type": "string", "description": "补充或修正后的解析（可选）"},
                        "pending": {"type": "boolean", "description": "是否仍保持待修正标记，默认 false（解除待修正）"},
                    },
                    "required": ["question_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ai_answer_question",
                "description": "调用完整 AI 查题管线（本地题库 -> 同题判重 -> 联网搜题 -> 多模型投票）解答一道题目。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "题目题干"},
                        "options": {"type": "string", "description": "选项（可选）"},
                        "type": {
                            "type": "string",
                            "enum": ["single", "multiple", "judgement", "completion", "short"],
                            "description": "题型（默认 single）",
                        },
                        "allow_local": {"type": "boolean", "description": "是否允许优先命中本地题库（默认 true）"},
                    },
                    "required": ["title"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ai_get_study_overview",
                "description": "获取完整的个人学习总览：本地题库目录列表、学科知识图谱列表、刷题正确率以及按艾宾浩斯遗忘曲线筛选出的到期/低留存待复习知识点。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ai_get_knowledge_graph",
                "description": "查看指定学科的知识图谱全部知识点（含掌握度、记忆留存率、下次复习时间、关联题目数及概念关系边）。支持传 subject_id 或学科名称关键词。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subject_id": {"type": "integer", "description": "学科 ID（与 subject_name 二选一）"},
                        "subject_name": {"type": "string", "description": "学科名称或关键词（模糊匹配）"},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ai_review_knowledge_node",
                "description": "在辅导或自测完成后，为某个知识图谱节点记录一次复习评价，自动推进艾宾浩斯记忆阶段与掌握度。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "node_id": {"type": "integer", "description": "知识图谱节点 ID"},
                        "quality": {
                            "type": "string",
                            "enum": ["poor", "fair", "good"],
                            "description": "本次复习掌握情况：poor（遗忘/不会）、fair（模糊/一般）、good（熟练掌握）",
                        },
                    },
                    "required": ["node_id", "quality"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "ai_practice_stats",
                "description": "获取刷题历史正确率、最近练习记录以及优先需要巩固的薄弱/错题列表。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["wrong", "unmastered", "smart"],
                            "description": "筛选模式：wrong（错题/待修正）、unmastered（未满级）、smart（综合薄弱优先），默认 wrong。",
                        },
                        "limit": {"type": "integer", "description": "返回题目条数上限（默认 8，最多 20）"},
                    },
                    "required": [],
                },
            },
        },
    ]

    if search_enabled:
        tools.append({
            "type": "function",
            "function": {
                "name": "ai_web_search",
                "description": "调用已配置的联网搜索引擎检索最新资料、概念定义、公式定理或冷门教材知识点。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词或问题"},
                    },
                    "required": ["query"],
                },
            },
        })

    return tools


def _sanitize_args_for_trace(args: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for k, v in (args or {}).items():
        if str(k).lower() in ("password", "apikey", "api_key", "token", "secret"):
            continue
        if isinstance(v, str) and len(v) > 200:
            cleaned[k] = v[:200] + "…"
        else:
            cleaned[k] = v
    return cleaned


def _canonical_call_key(name: str, args: Dict[str, Any]) -> str:
    try:
        serialized = json.dumps(args or {}, ensure_ascii=False, sort_keys=True)
    except Exception:
        serialized = str(args)
    return f"{name}:{serialized}"


def _summarize_tool_result(name: str, result: Dict[str, Any]) -> str:
    if not result.get("ok"):
        return str(result.get("error") or "执行失败")[:120]
    if name == "chaoxing_list_courses":
        return f"获取到 {result.get('count', 0)} 门课程"
    if name == "chaoxing_get_course_chapters":
        rate = result.get("completionRate")
        rate_str = f"（{rate}%）" if rate is not None else ""
        return (
            f"《{result.get('courseTitle', '')}》共 {result.get('totalChapters', 0)} 章，"
            f"已完成 {result.get('completedChapters', 0)}，未完成 {result.get('pendingChapters', 0)}{rate_str}"
        )
    if name == "chaoxing_start_study":
        return f"已启动刷课任务（{len(result.get('courseIds') or [])} 门课，{result.get('speed')}x）"
    if name == "chaoxing_start_course_tool":
        return f"已启动课程工具任务（{result.get('taskType')}）"
    if name == "chaoxing_get_task_status":
        if not result.get("hasTask"):
            return "当前没有运行中或近期任务"
        st = result.get("status") or {}
        return f"任务状态：{st.get('status')}（进度 {st.get('progress', 0)}/{st.get('total', 0)}）"
    if name == "chaoxing_stop_task":
        return f"停止请求已提交（{result.get('state')}）"
    if name == "chaoxing_resume_task":
        return f"任务恢复状态：{result.get('status')}（{result.get('taskId')}）"
    if name == "chaoxing_cancel_shutdown":
        return "已取消自动关机计划"
    if name == "chaoxing_get_settings":
        return "已读取学习通与 AI 配置"
    if name == "chaoxing_update_settings":
        return "设置已更新并保存"
    if name == "ai_search_bank":
        return f"命中 {len(result.get('items') or [])} / 共 {result.get('total', 0)} 题"
    if name == "ai_save_question":
        return f"已入库（ID #{result.get('id')}）"
    if name == "ai_correct_question":
        return f"已更新题目 #{result.get('id')}"
    if name == "ai_answer_question":
        ans = str(result.get("answer") or "")
        return f"答案：{ans[:60]}" if ans else "已完成查题"
    if name == "ai_get_study_overview":
        due = len(result.get("dueNodes") or [])
        return f"{result.get('stats', {}).get('totalQuestions', 0)} 题 · {len(result.get('subjects') or [])} 学科 · {due} 个待复习知识点"
    if name == "ai_get_knowledge_graph":
        subj = (result.get("subject") or {}).get("name", "")
        return f"《{subj}》共 {result.get('nodeCount', 0)} 个知识点"
    if name == "ai_review_knowledge_node":
        return f"已记录复习（掌握度 {result.get('mastery', 0)}/3）"
    if name == "ai_practice_stats":
        return f"正确率 {result.get('accuracy', 0)}%（共 {result.get('total', 0)} 次练习）"
    if name == "ai_web_search":
        return f"联网返回 {len(result.get('results') or [])} 条参考资料"
    return "执行成功"


def _require_chaoxing_creds(chaoxing_ctx: Optional[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(chaoxing_ctx, dict) or not str(chaoxing_ctx.get("username") or "").strip():
        return None, "当前尚未登录超星学习通账号，请先在主界面登录学习通后再执行该操作。"
    return {
        "username": str(chaoxing_ctx["username"]).strip(),
        "password": str(chaoxing_ctx.get("password") or ""),
        "use_cookies": bool(chaoxing_ctx.get("use_cookies", True)),
        "activeTaskId": str(chaoxing_ctx.get("activeTaskId") or "").strip() or None,
    }, None


def _get_cached_courses(
    creds: Dict[str, Any],
    bridge: Any,
    course_cache: Dict[str, List[Dict[str, Any]]],
    cache_lock: Optional[threading.Lock] = None,
) -> List[Dict[str, Any]]:
    if cache_lock is not None:
        with cache_lock:
            cached = course_cache.get("courses")
            if cached is not None:
                return cached
        courses = bridge.list_courses(creds)
        with cache_lock:
            course_cache["courses"] = courses
        return courses
    cached = course_cache.get("courses")
    if cached is None:
        cached = bridge.list_courses(creds)
        course_cache["courses"] = cached
    return cached


def _resolve_course_refs(
    refs: List[str],
    creds: Dict[str, Any],
    bridge: Any,
    course_cache: Dict[str, List[Dict[str, Any]]],
    cache_lock: Optional[threading.Lock] = None,
) -> Tuple[List[str], List[Dict[str, Any]], Optional[str]]:
    """Resolve a list of courseId or course title keywords against the account's course list."""
    raw_items = [str(x).strip() for x in (refs or []) if str(x).strip()]
    if not raw_items:
        return [], [], "请提供至少一门课程 ID 或课程名称。"

    courses = _get_cached_courses(creds, bridge, course_cache, cache_lock=cache_lock)

    by_id = {str(c.get("courseId")): c for c in courses if c.get("courseId") is not None}
    resolved_ids: List[str] = []
    resolved_meta: List[Dict[str, Any]] = []
    unmatched: List[str] = []

    for ref in raw_items:
        if ref in by_id:
            if ref not in resolved_ids:
                resolved_ids.append(ref)
                resolved_meta.append({"courseId": ref, "title": by_id[ref].get("title", ref)})
            continue
        low = ref.lower()
        exact = [c for c in courses if str(c.get("title") or "").strip().lower() == low]
        matches = exact or [c for c in courses if low in str(c.get("title") or "").lower()]
        if len(matches) == 1:
            cid = str(matches[0]["courseId"])
            if cid not in resolved_ids:
                resolved_ids.append(cid)
                resolved_meta.append({"courseId": cid, "title": matches[0].get("title", cid)})
        elif len(matches) > 1:
            names = "、".join(f"{m.get('title')}({m.get('courseId')})" for m in matches[:5])
            return [], [], f"关键词「{ref}」匹配到多门课程：{names}，请明确指定课程 ID 或完整名称。"
        else:
            unmatched.append(ref)

    if unmatched:
        return [], [], f"未在你的学习通账号中找到课程：{'、'.join(unmatched)}。"
    return resolved_ids, resolved_meta, None


def execute_agent_tool(
    name: str,
    arguments: Dict[str, Any],
    *,
    db: AIDB,
    config_store: AIConfigStore,
    engine: Any = None,
    chaoxing_bridge: Any = None,
    chaoxing_ctx: Optional[Dict[str, Any]] = None,
    course_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    cache_lock: Optional[threading.Lock] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Execute one Agent tool safely. Returns (tool_result_dict, ui_effects_list)."""
    args = arguments if isinstance(arguments, dict) else {}
    effects: List[Dict[str, Any]] = []
    cache = course_cache if course_cache is not None else {}

    try:
        # ---- Chaoxing tools ----
        if name.startswith("chaoxing_"):
            if chaoxing_bridge is None:
                return {"ok": False, "error": "当前环境未挂载学习通控制桥接服务。"}, effects

            if name == "chaoxing_get_settings":
                username = str((chaoxing_ctx or {}).get("username") or "").strip() or None
                web_cfg = chaoxing_bridge.get_web_settings(username)
                ai_cfg = config_store.load()
                return {
                    "ok": True,
                    "loggedIn": bool(username),
                    "username": username,
                    "chaoxingDefaults": web_cfg,
                    "aiEngine": {
                        "enabled": ai_cfg["engine"]["enabled"],
                        "priority": ai_cfg["engine"]["priority"],
                        "autoSaveToBank": ai_cfg["engine"]["autoSaveToBank"],
                        "enableSameQuestionCheck": ai_cfg["engine"]["enableSameQuestionCheck"],
                        "enableVisionForImages": ai_cfg["engine"]["enableVisionForImages"],
                        "searchEnabled": ai_cfg["search"]["enabled"],
                        "searchProvider": ai_cfg["search"]["provider"],
                        "selectedTextModels": ai_cfg["selectedTextModels"],
                        "selectedAgentModel": ai_cfg.get("selectedAgentModel"),
                    },
                }, effects

            if name == "chaoxing_update_settings":
                web_patch: Dict[str, Any] = {}
                if "speed" in args and args["speed"] is not None:
                    spd = _coerce_float(args["speed"])
                    if spd is None or not (1.0 <= spd <= 2.0):
                        return {"ok": False, "error": "倍速必须在 1.0 到 2.0 之间"}, effects
                    web_patch["speed"] = spd
                if "jobs" in args and args["jobs"] is not None:
                    jobs = _coerce_int(args["jobs"])
                    if jobs is None or not (1 <= jobs <= 8):
                        return {"ok": False, "error": "并发数必须在 1 到 8 之间"}, effects
                    web_patch["jobs"] = jobs
                if "notopen_action" in args and args["notopen_action"] in ("retry", "continue"):
                    web_patch["notopen_action"] = args["notopen_action"]

                ai_patch: Dict[str, Any] = {}
                engine_patch: Dict[str, Any] = {}
                ai_enabled = _coerce_bool(args.get("ai_enabled"))
                if ai_enabled is not None:
                    engine_patch["enabled"] = ai_enabled
                if "ai_priority" in args and args["ai_priority"] in VALID_PRIORITIES:
                    engine_patch["priority"] = args["ai_priority"]
                auto_save = _coerce_bool(args.get("auto_save_to_bank"))
                if auto_save is not None:
                    engine_patch["autoSaveToBank"] = auto_save
                if engine_patch:
                    ai_patch["engine"] = engine_patch
                search_enabled = _coerce_bool(args.get("search_enabled"))
                if search_enabled is not None:
                    ai_patch["search"] = {"enabled": search_enabled}

                if not web_patch and not ai_patch:
                    return {"ok": False, "error": "未提供任何可修改的设置参数"}, effects

                updated_web = chaoxing_bridge.update_web_settings(web_patch) if web_patch else None
                updated_ai = config_store.update(ai_patch) if ai_patch else None
                if (
                    updated_ai
                    and hasattr(chaoxing_bridge, "sync_tiku_ai_settings")
                    and ("enabled" in engine_patch or "priority" in engine_patch)
                ):
                    chaoxing_bridge.sync_tiku_ai_settings(
                        ai_enabled=updated_ai["engine"]["enabled"] if "enabled" in engine_patch else None,
                        ai_priority=updated_ai["engine"]["priority"] if "priority" in engine_patch else None,
                    )
                effects.append({"type": "config_changed"})
                return {
                    "ok": True,
                    "updatedChaoxing": web_patch,
                    "updatedAI": ai_patch,
                    "chaoxingDefaults": updated_web,
                    "aiEngine": updated_ai["engine"] if updated_ai else None,
                }, effects

            creds, err = _require_chaoxing_creds(chaoxing_ctx)
            if err:
                return {"ok": False, "error": err}, effects
            assert creds is not None

            if name == "chaoxing_list_courses":
                courses = chaoxing_bridge.list_courses(creds)
                if cache_lock is not None:
                    with cache_lock:
                        cache["courses"] = courses
                else:
                    cache["courses"] = courses
                selected_set = set()
                try:
                    web_cfg = chaoxing_bridge.get_web_settings(creds["username"])
                    selected_set = {str(x) for x in (web_cfg.get("selectedCourseIds") or [])}
                except Exception:
                    pass
                slim = [
                    {
                        "courseId": str(c.get("courseId", "")),
                        "title": str(c.get("title") or ""),
                        "teacher": str(c.get("teacherfactor") or c.get("teacher") or ""),
                        "selected": str(c.get("courseId", "")) in selected_set,
                    }
                    for c in courses
                ]
                return {"ok": True, "count": len(slim), "courses": slim}, effects

            if name == "chaoxing_get_course_chapters":
                ref = str(args.get("course_id") or "").strip()
                resolved_ids, resolved_meta, r_err = _resolve_course_refs(
                    [ref], creds, chaoxing_bridge, cache, cache_lock=cache_lock
                )
                if r_err:
                    return {"ok": False, "error": r_err}, effects
                target_id = resolved_ids[0]
                if cache_lock is not None:
                    with cache_lock:
                        cached_list = list(cache.get("courses") or [])
                else:
                    cached_list = list(cache.get("courses") or [])
                course_obj = next(
                    (c for c in cached_list if str(c.get("courseId")) == target_id),
                    None,
                )
                chapters_data = chaoxing_bridge.get_course_chapters(creds, target_id, course_obj=course_obj)
                return {
                    "ok": True,
                    "courseId": target_id,
                    "courseTitle": resolved_meta[0]["title"],
                    **chapters_data,
                }, effects

            if name == "chaoxing_start_study":
                raw_ids = args.get("course_ids")
                if isinstance(raw_ids, str):
                    raw_ids = [raw_ids]
                if not isinstance(raw_ids, list) or not raw_ids:
                    return {"ok": False, "error": "请指定至少一门要学习的课程"}, effects
                resolved_ids, resolved_meta, r_err = _resolve_course_refs(
                    raw_ids, creds, chaoxing_bridge, cache, cache_lock=cache_lock
                )
                if r_err:
                    return {"ok": False, "error": r_err}, effects
                spd = _coerce_float(args.get("speed"))
                jobs_val = _coerce_int(args.get("jobs"))
                shutdown_val = _coerce_bool(args.get("shutdown_on_complete"))
                started = chaoxing_bridge.start_study(
                    creds,
                    course_ids=resolved_ids,
                    speed=spd,
                    jobs=jobs_val,
                    notopen_action=args.get("notopen_action"),
                    shutdown_on_complete=bool(shutdown_val) if shutdown_val is not None else False,
                )
                effects.append({
                    "type": "task_started",
                    "taskId": started["task_id"],
                    "taskType": "study",
                })
                return {
                    "ok": True,
                    "taskId": started["task_id"],
                    "status": "running",
                    "courseIds": resolved_ids,
                    "courses": resolved_meta,
                    "speed": started.get("speed"),
                    "jobs": started.get("jobs"),
                }, effects

            if name == "chaoxing_start_course_tool":
                task_type = str(args.get("task_type") or "").strip()
                if task_type not in ("visits", "catalog", "read_time"):
                    return {"ok": False, "error": "不支持的课程工具类型，仅支持 visits / catalog / read_time"}, effects
                tool_options: Dict[str, Any] = {}
                resolved_ids: List[str] = []
                if task_type == "visits":
                    raw_ids = args.get("course_ids") or []
                    if isinstance(raw_ids, str):
                        raw_ids = [raw_ids]
                    resolved_ids, _, r_err = _resolve_course_refs(
                        raw_ids, creds, chaoxing_bridge, cache, cache_lock=cache_lock
                    )
                    if r_err:
                        return {"ok": False, "error": r_err}, effects
                    tool_options = {
                        "count": _coerce_int(args.get("count"), 10) or 10,
                        "interval": _coerce_float(args.get("interval"), 30.0) or 30.0,
                    }
                elif task_type == "catalog":
                    raw_ids = args.get("course_ids") or []
                    if isinstance(raw_ids, str):
                        raw_ids = [raw_ids]
                    resolved_ids, _, r_err = _resolve_course_refs(
                        raw_ids, creds, chaoxing_bridge, cache, cache_lock=cache_lock
                    )
                    if r_err:
                        return {"ok": False, "error": r_err}, effects
                    tool_options = {"purpose": str(args.get("purpose") or "download")}
                elif task_type == "read_time":
                    urls = args.get("urls") or []
                    if isinstance(urls, str):
                        urls = [urls]
                    if not isinstance(urls, list) or not urls:
                        return {"ok": False, "error": "read_time 模式请提供至少一条章节链接 urls"}, effects
                    tool_options = {
                        "urls": [str(u).strip() for u in urls if str(u).strip()],
                        "minutes": _coerce_float(args.get("minutes"), 30.0) or 30.0,
                    }

                shutdown_val = _coerce_bool(args.get("shutdown_on_complete"))
                started = chaoxing_bridge.start_course_tool(
                    creds,
                    task_type=task_type,
                    course_ids=resolved_ids,
                    tool_options=tool_options,
                    shutdown_on_complete=bool(shutdown_val) if shutdown_val is not None else False,
                )
                effects.append({
                    "type": "task_started",
                    "taskId": started["task_id"],
                    "taskType": task_type,
                })
                return {
                    "ok": True,
                    "taskId": started["task_id"],
                    "taskType": task_type,
                    "status": "running",
                }, effects

            if name == "chaoxing_get_task_status":
                tid = str(args.get("task_id") or creds.get("activeTaskId") or "").strip() or None
                log_limit = max(1, min(40, _coerce_int(args.get("log_limit"), 15) or 15))
                info = chaoxing_bridge.get_task_status(creds["username"], task_id=tid, log_limit=log_limit)
                return {"ok": True, **info}, effects

            if name == "chaoxing_stop_task":
                tid = str(args.get("task_id") or creds.get("activeTaskId") or "").strip() or None
                stopped = chaoxing_bridge.stop_task(creds["username"], task_id=tid)
                effects.append({
                    "type": "task_stopped",
                    "taskId": stopped["task_id"],
                    "state": stopped["state"],
                })
                return {"ok": True, **stopped}, effects

            if name == "chaoxing_resume_task":
                if not hasattr(chaoxing_bridge, "resume_task"):
                    return {"ok": False, "error": "当前桥接服务不支持任务恢复"}, effects
                tid = str(args.get("task_id") or creds.get("activeTaskId") or "").strip() or None
                resumed = chaoxing_bridge.resume_task(creds, task_id=tid)
                if resumed.get("status") == "running":
                    effects.append({
                        "type": "task_started",
                        "taskId": resumed["task_id"],
                        "taskType": "resume",
                    })
                return {"ok": True, "taskId": resumed["task_id"], "status": resumed.get("status")}, effects

            if name == "chaoxing_cancel_shutdown":
                tid = str(args.get("task_id") or creds.get("activeTaskId") or "").strip() or None
                res = chaoxing_bridge.cancel_shutdown(creds["username"], task_id=tid)
                return {"ok": True, **res}, effects

        # ---- AI local bank / study graph / query / search tools ----
        if name == "ai_search_bank":
            keyword = str(args.get("keyword") or "").strip()
            pending_only = bool(_coerce_bool(args.get("pending_only")))
            folder_id = _coerce_int(args.get("folder_id"))
            question_type = str(args.get("question_type") or "").strip()
            limit = max(1, min(20, _coerce_int(args.get("limit"), 8) or 8))
            res = db.list_questions(
                folder_id=folder_id,
                keyword=keyword,
                question_type=question_type,
                pending_only=pending_only,
                page=1,
                page_size=limit,
            )
            slim_items = [
                {
                    "id": q["id"],
                    "question": q["question"][:180],
                    "options": (q.get("options") or "")[:180],
                    "answer": q["answer"][:140],
                    "explanation": (q.get("explanation") or "")[:120],
                    "questionType": q.get("questionType"),
                    "pendingCorrection": bool(q.get("isPendingCorrection")),
                    "mastery": q.get("mastery", 0),
                    "folderName": q.get("folderName"),
                }
                for q in res.get("items") or []
            ]
            return {"ok": True, "total": res.get("total", 0), "items": slim_items}, effects

        if name == "ai_save_question":
            question_text = str(args.get("question") or "").strip()
            answer_text = str(args.get("answer") or "").strip()
            if not question_text or not answer_text:
                return {"ok": False, "error": "题干和答案不能为空"}, effects
            folder_id = max(0, _coerce_int(args.get("folder_id"), 0) or 0)
            saved = db.save_question({
                "question": question_text,
                "answer": answer_text,
                "options": str(args.get("options") or "").strip(),
                "questionType": str(args.get("question_type") or "short").strip(),
                "explanation": str(args.get("explanation") or "").strip(),
                "isAi": True,
                "folderId": folder_id,
            })
            effects.append({"type": "bank_changed"})
            return {"ok": True, "id": saved["id"], "question": saved["question"][:100], "answer": saved["answer"][:100]}, effects

        if name == "ai_correct_question":
            qid = _coerce_int(args.get("question_id"))
            if qid is None or qid <= 0:
                return {"ok": False, "error": "请提供有效的题目 ID"}, effects
            pending_flag = _coerce_bool(args.get("pending"))
            corrected_answer = args.get("answer")
            explanation = args.get("explanation")
            updated = db.set_pending_correction(
                qid,
                bool(pending_flag) if pending_flag is not None else False,
                corrected_answer=str(corrected_answer).strip() if corrected_answer is not None else None,
                explanation=str(explanation).strip() if explanation is not None else None,
            )
            effects.append({"type": "bank_changed"})
            return {
                "ok": True,
                "id": updated["id"],
                "question": updated["question"][:100],
                "answer": updated["answer"][:120],
                "pendingCorrection": bool(updated.get("isPendingCorrection")),
            }, effects

        if name == "ai_answer_question":
            if engine is None:
                return {"ok": False, "error": "查题引擎未就绪"}, effects
            title = str(args.get("title") or "").strip()
            if not title:
                return {"ok": False, "error": "题目题干不能为空"}, effects
            allow_local = _coerce_bool(args.get("allow_local"))
            ans = engine.answer_question(
                {
                    "title": title,
                    "options": str(args.get("options") or ""),
                    "type": str(args.get("type") or "single"),
                    "images": [],
                },
                allow_local=True if allow_local is None else allow_local,
                allow_ai=True,
                record_log=True,
                request_path="/agent-query",
                request_method="POST",
            )
            effects.append({"type": "bank_changed"})
            return {
                "ok": bool(ans.get("ok")),
                "answer": ans.get("answer", ""),
                "source": ans.get("source", ""),
                "modelName": ans.get("modelName", ""),
                "error": ans.get("error", ""),
            }, effects

        if name == "ai_get_study_overview":
            stats = db.get_stats()
            folders = [
                {"id": f["id"], "name": f["name"], "questionCount": f.get("questionCount", 0)}
                for f in (db.list_folders() or [])[:20]
            ]
            subjects = db.list_subjects() or []
            practice = db.list_practice_history(limit=10)
            now_iso = utc_now_iso()
            due_nodes: List[Dict[str, Any]] = []
            for subj in subjects[:12]:
                try:
                    g = db.get_study_graph(int(subj["id"]))
                    for n in g.get("nodes") or []:
                        ret = float(n.get("retention", 1.0))
                        due_at = n.get("nextReviewDue")
                        is_due = (ret < 0.75) or (bool(due_at) and str(due_at) <= now_iso) or int(n.get("mastery", 0)) == 0
                        if is_due:
                            due_nodes.append({
                                "nodeId": n["id"],
                                "subjectId": subj["id"],
                                "subjectName": subj["name"],
                                "name": n["name"],
                                "mastery": n["mastery"],
                                "retentionPct": int(round(ret * 100)),
                                "nextReviewDue": due_at,
                                "questionCount": len(n.get("questions") or []),
                            })
                except Exception:
                    continue
            due_nodes.sort(key=lambda item: (item["retentionPct"], item["mastery"]))
            return {
                "ok": True,
                "stats": stats,
                "practiceAccuracy": practice.get("accuracy", 0.0),
                "folders": folders,
                "subjects": [
                    {"id": s["id"], "name": s["name"], "nodeCount": s.get("nodeCount", 0)}
                    for s in subjects[:20]
                ],
                "dueNodes": due_nodes[:15],
            }, effects

        if name == "ai_get_knowledge_graph":
            subjects = db.list_subjects() or []
            sid = _coerce_int(args.get("subject_id"))
            sname = str(args.get("subject_name") or "").strip()
            target_id: Optional[int] = sid
            if target_id is None and sname:
                low = sname.lower()
                exact = [s for s in subjects if str(s.get("name") or "").strip().lower() == low]
                matches = exact or [s for s in subjects if low in str(s.get("name") or "").lower()]
                if len(matches) == 1:
                    target_id = int(matches[0]["id"])
                elif len(matches) > 1:
                    names = "、".join(f"{m['name']}(id={m['id']})" for m in matches[:5])
                    return {"ok": False, "error": f"匹配到多个学科：{names}，请指定 subject_id"}, effects
                else:
                    return {"ok": False, "error": f"未找到名为「{sname}」的学科知识图谱"}, effects
            if target_id is None:
                if len(subjects) == 1:
                    target_id = int(subjects[0]["id"])
                else:
                    avail = "、".join(f"{s['name']}(id={s['id']})" for s in subjects[:8]) or "暂无学科"
                    return {"ok": False, "error": f"请提供 subject_id 或 subject_name。当前学科：{avail}"}, effects
            g = db.get_study_graph(target_id)
            nodes_slim = [
                {
                    "id": n["id"],
                    "name": n["name"],
                    "summary": (n.get("summary") or "")[:100],
                    "mastery": n["mastery"],
                    "retentionPct": int(round(float(n.get("retention", 0)) * 100)),
                    "nextReviewDue": n.get("nextReviewDue"),
                    "questionCount": len(n.get("questions") or []),
                }
                for n in (g.get("nodes") or [])[:40]
            ]
            return {
                "ok": True,
                "subject": g["subject"],
                "nodeCount": len(g.get("nodes") or []),
                "edgeCount": len(g.get("edges") or []),
                "nodes": nodes_slim,
            }, effects

        if name == "ai_review_knowledge_node":
            nid = _coerce_int(args.get("node_id"))
            quality = str(args.get("quality") or "good").strip()
            if nid is None or nid <= 0:
                return {"ok": False, "error": "请提供有效的知识点 node_id"}, effects
            res = db.review_graph_node(nid, quality)
            effects.append({"type": "graph_changed"})
            return {"ok": True, **res}, effects

        if name == "ai_practice_stats":
            mode = str(args.get("mode") or "wrong").strip().lower()
            if mode not in ("wrong", "unmastered", "smart"):
                mode = "wrong"
            limit = max(1, min(20, _coerce_int(args.get("limit"), 8) or 8))
            hist = db.list_practice_history(limit=10)
            candidates = db.list_practice_candidates(mode=mode, limit=limit)
            return {
                "ok": True,
                "total": hist.get("total", 0),
                "correct": hist.get("correct", 0),
                "accuracy": hist.get("accuracy", 0.0),
                "mode": mode,
                "weakQuestions": [
                    {
                        "id": q["id"],
                        "question": q["question"][:160],
                        "answer": q["answer"][:100],
                        "questionType": q.get("questionType"),
                        "mastery": q.get("mastery", 0),
                        "wrongCount": q.get("wrongCount", 0),
                        "pendingCorrection": bool(q.get("isPendingCorrection")),
                    }
                    for q in candidates
                ],
            }, effects

        if name == "ai_web_search":
            query = str(args.get("query") or "").strip()
            if not query:
                return {"ok": False, "error": "搜索关键词不能为空"}, effects
            cfg = config_store.load()
            search_cfg = cfg.get("search") or {}
            if not search_cfg.get("enabled"):
                return {"ok": False, "error": "联网搜索尚未启用，请先在「模型与搜题设置」中开启联网搜题并填写对应搜索引擎 API Key。"}, effects
            outcome = perform_web_search(query, search_cfg)
            if outcome.get("status") != "success":
                return {"ok": False, "error": outcome.get("error") or "联网搜索失败"}, effects
            return {
                "ok": True,
                "provider": outcome.get("provider"),
                "query": outcome.get("query"),
                "results": outcome.get("results") or [],
            }, effects

        return {"ok": False, "error": f"未知工具: {name}"}, effects
    except Exception as exc:
        logger.warning("Agent 工具 {} 执行失败: {}", name, redact(exc))
        return {"ok": False, "error": redact(exc)}, effects


_TOOL_CALL_OPEN_RE = re.compile(r"<tool_call>\s*", flags=re.I)
_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>[\s\S]*?</tool_call>", flags=re.I)
_FENCED_TOOL_CALL_RE = re.compile(r"```tool_call\s*([\s\S]*?)\s*```", flags=re.I)


def _extract_react_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Parse fallback ReAct <tool_call>{...}</tool_call> or ```tool_call {...}``` blocks safely."""
    raw = text or ""
    calls: List[Dict[str, Any]] = []
    decoder = json.JSONDecoder()

    # 1) Standard <tool_call>...</tool_call> with nested-brace safe raw_decode
    for match in _TOOL_CALL_OPEN_RE.finditer(raw):
        start = match.end()
        brace_idx = raw.find("{", start)
        if brace_idx < 0:
            continue
        try:
            obj, _ = decoder.raw_decode(raw, brace_idx)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("name"):
            args = obj.get("arguments")
            calls.append({
                "id": f"react_{len(calls) + 1}",
                "name": str(obj["name"]).strip(),
                "arguments": args if isinstance(args, dict) else {},
            })

    # 2) Fallback: ```tool_call {...}``` fenced blocks
    if not calls:
        for match in _FENCED_TOOL_CALL_RE.finditer(raw):
            snippet = match.group(1).strip()
            brace_idx = snippet.find("{")
            if brace_idx < 0:
                continue
            try:
                obj, _ = decoder.raw_decode(snippet, brace_idx)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("name"):
                args = obj.get("arguments")
                calls.append({
                    "id": f"react_{len(calls) + 1}",
                    "name": str(obj["name"]).strip(),
                    "arguments": args if isinstance(args, dict) else {},
                })

    return calls


def _strip_react_tool_tags(text: str) -> str:
    cleaned = _TOOL_CALL_BLOCK_RE.sub("", text or "")
    cleaned = _FENCED_TOOL_CALL_RE.sub("", cleaned)
    return cleaned.strip()


def _react_tools_prompt(tools: List[Dict[str, Any]]) -> str:
    lines = [
        "\n【工具调用格式（备选协议）】",
        "当你需要查询或操控超星学习通/题库/知识图谱时，请输出一个或多个如下标签（不要放在代码块内）：",
        '<tool_call>{"name": "工具名", "arguments": {参数JSON}}</tool_call>',
        "等待工具执行结果返回后，再继续调用或给出最终中文回复。可用工具清单：",
    ]
    for t in tools:
        fn = t.get("function") or {}
        params = fn.get("parameters") or {}
        props = list((params.get("properties") or {}).keys())
        lines.append(f"- {fn.get('name')}({', '.join(props)}): {fn.get('description')}")
    return "\n".join(lines)


def _should_fallback_to_react(exc: ModelCallError) -> bool:
    if exc.status_code in (400, 403, 404, 422, 500, 501):
        msg = str(exc).lower()
        if any(k in msg for k in ("tool", "function", "unsupported", "not support", "schema", "invalid_request")):
            return True
        if exc.status_code in (400, 422) and any(k in msg for k in ("invalid", "400", "422")):
            return True
    return False


def _synthesize_fallback_reply(tool_trace: List[Dict[str, Any]]) -> str:
    if not tool_trace:
        return "已完成处理，但模型未返回文本内容，请重试。"
    lines = ["已完成以下工具操作："]
    for tc in tool_trace:
        icon = "✅" if tc.get("ok") else "❌"
        lines.append(f"- {icon} **{tc.get('label') or tc.get('name')}**：{tc.get('summary') or ''}")
    return "\n".join(lines)


def run_agent_loop(
    platform: Dict[str, Any],
    model: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    db: AIDB,
    config_store: AIConfigStore,
    engine: Any = None,
    chaoxing_bridge: Any = None,
    chaoxing_ctx: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
    llm_caller: Callable[..., ModelCallResult] = call_model,
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Tuple[ModelCallResult, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Run an agentic multi-step tool loop.

    Returns (final_model_result, tool_calls_trace, ui_effects).
    """
    cfg = config_store.load()
    search_enabled = bool((cfg.get("search") or {}).get("enabled", False))
    tools = build_agent_tools(search_enabled=search_enabled)
    working_messages = list(messages)
    tool_trace: List[Dict[str, Any]] = []
    ui_effects: List[Dict[str, Any]] = []
    course_cache: Dict[str, List[Dict[str, Any]]] = {}
    cache_lock = threading.Lock()
    readonly_result_cache: Dict[str, Dict[str, Any]] = {}
    failed_call_counts: Dict[str, int] = {}
    reasoning_parts: List[str] = []
    total_duration_ms = 0

    def _emit(evt: Dict[str, Any]) -> None:
        if on_event is not None:
            try:
                on_event(evt)
            except Exception:
                pass

    def _check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            _emit({"type": "cancelled"})
            raise AgentRunCancelled("已取消本次 Agent 执行")

    runtime_model_low = str(model.get("modelId") or "").lower()
    # Reasoning models that commonly disallow native OpenAI tools
    use_react_fallback = (
        "reasoner" in runtime_model_low
        or "deepseek-r1" in runtime_model_low
        or runtime_model_low.startswith(("o1-", "o1"))
        or runtime_model_low.startswith("qwq")
    )
    react_injected = False

    def _ensure_react_prompt() -> None:
        nonlocal react_injected
        if react_injected:
            return
        react_injected = True
        if working_messages and working_messages[0].get("role") == "system":
            working_messages[0] = {
                **working_messages[0],
                "content": str(working_messages[0].get("content") or "") + _react_tools_prompt(tools),
            }
        else:
            working_messages.insert(0, {"role": "system", "content": _react_tools_prompt(tools)})

    def _run_one_tool(tc: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]:
        t_name = tc["name"]
        t_args = tc.get("arguments") or {}
        call_key = _canonical_call_key(t_name, t_args)

        _emit({
            "type": "tool_start",
            "id": tc["id"],
            "name": t_name,
            "label": TOOL_LABELS.get(t_name, t_name),
            "arguments": _sanitize_args_for_trace(t_args),
        })

        t_started = time.monotonic()
        if t_name in READ_ONLY_TOOLS and call_key in readonly_result_cache:
            t_res = {
                **readonly_result_cache[call_key],
                "_note": "该只读查询本轮已执行过，直接复用缓存结果，请基于已有结果继续推理或回复用户。",
            }
            t_effects: List[Dict[str, Any]] = []
        elif failed_call_counts.get(call_key, 0) >= 2:
            t_res = {
                "ok": False,
                "error": "该工具调用已连续失败多次，请勿重复相同参数重试，直接向用户说明失败原因或改换方案。",
            }
            t_effects = []
        else:
            t_res, t_effects = execute_agent_tool(
                t_name,
                t_args,
                db=db,
                config_store=config_store,
                engine=engine,
                chaoxing_bridge=chaoxing_bridge,
                chaoxing_ctx=chaoxing_ctx,
                course_cache=course_cache,
                cache_lock=cache_lock,
            )
            if t_res.get("ok") and t_name in READ_ONLY_TOOLS:
                readonly_result_cache[call_key] = dict(t_res)
            elif not t_res.get("ok"):
                failed_call_counts[call_key] = failed_call_counts.get(call_key, 0) + 1

        t_ms = int((time.monotonic() - t_started) * 1000)
        serialized = json.dumps(t_res, ensure_ascii=False)
        trace_item = {
            "id": tc["id"],
            "name": t_name,
            "label": TOOL_LABELS.get(t_name, t_name),
            "arguments": _sanitize_args_for_trace(t_args),
            "ok": bool(t_res.get("ok")),
            "summary": _summarize_tool_result(t_name, t_res),
            "durationMs": t_ms,
            "resultPreview": serialized[:1500],
        }
        _emit({"type": "tool_done", "trace": trace_item})
        return t_res, t_effects, trace_item

    last_res: Optional[ModelCallResult] = None

    for step_idx in range(MAX_AGENT_STEPS):
        _check_cancelled()
        _emit({"type": "step_start", "step": step_idx + 1, "maxSteps": MAX_AGENT_STEPS})

        if use_react_fallback:
            _ensure_react_prompt()
            res = llm_caller(
                platform,
                model,
                working_messages,
                timeout=timeout,
                retry_count=1,
            )
            calls = _extract_react_tool_calls(res.content)
        else:
            try:
                res = llm_caller(
                    platform,
                    model,
                    working_messages,
                    timeout=timeout,
                    retry_count=1,
                    tools=tools,
                )
                calls = list(res.tool_calls or [])
                # Hybrid safety net: some proxies return HTTP 200 without native tool_calls but emit <tool_call> tags
                if not calls and ("<tool_call>" in (res.content or "").lower() or "```tool_call" in (res.content or "").lower()):
                    parsed_tags = _extract_react_tool_calls(res.content)
                    if parsed_tags:
                        use_react_fallback = True
                        calls = parsed_tags
            except ModelCallError as exc:
                if _should_fallback_to_react(exc):
                    logger.info("模型 {} 不支持原生 tool calling，自动切换 ReAct 工具模式: {}", model.get("displayName"), exc)
                    use_react_fallback = True
                    _ensure_react_prompt()
                    res = llm_caller(
                        platform,
                        model,
                        working_messages,
                        timeout=timeout,
                        retry_count=1,
                    )
                    calls = _extract_react_tool_calls(res.content)
                else:
                    raise

        _check_cancelled()
        last_res = res
        total_duration_ms += res.duration_ms
        if res.reasoning:
            reasoning_parts.append(res.reasoning)

        if not calls:
            final_content = _strip_react_tool_tags(res.content) if use_react_fallback else res.content
            if not (final_content or "").strip() and tool_trace:
                final_content = _synthesize_fallback_reply(tool_trace)
            return (
                ModelCallResult(
                    content=final_content,
                    reasoning="\n\n".join(reasoning_parts).strip(),
                    model_id=res.model_id,
                    model_name=res.model_name,
                    platform_name=res.platform_name,
                    duration_ms=total_duration_ms,
                    usage=res.usage,
                ),
                tool_trace,
                ui_effects,
            )

        batch = calls[:MAX_TOOLS_PER_STEP]
        can_parallel = len(batch) > 1 and all(tc["name"] in READ_ONLY_TOOLS for tc in batch)
        batch_outputs: List[Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], Dict[str, Any]]] = []

        if can_parallel:
            with ThreadPoolExecutor(max_workers=min(4, len(batch))) as pool:
                futures = [pool.submit(_run_one_tool, tc) for tc in batch]
                for tc, fut in zip(batch, futures):
                    _check_cancelled()
                    t_res, t_effects, trace_item = fut.result()
                    batch_outputs.append((tc, t_res, t_effects, trace_item))
        else:
            for tc in batch:
                _check_cancelled()
                t_res, t_effects, trace_item = _run_one_tool(tc)
                batch_outputs.append((tc, t_res, t_effects, trace_item))

        if use_react_fallback:
            working_messages.append({"role": "assistant", "content": res.content})
            observation_lines: List[str] = []
            for tc, t_res, t_effects, trace_item in batch_outputs:
                ui_effects.extend(t_effects)
                tool_trace.append(trace_item)
                serialized = json.dumps(t_res, ensure_ascii=False)
                observation_lines.append(f"工具 {tc['name']} 返回：\n{serialized[:4500]}")
            working_messages.append({
                "role": "user",
                "content": "【工具执行结果】\n" + "\n\n".join(observation_lines) + "\n请基于以上真实结果继续执行或用清晰中文回复用户。",
            })
        else:
            working_messages.append({
                "role": "assistant",
                "content": res.content,
                "tool_calls": batch,
            })
            for tc, t_res, t_effects, trace_item in batch_outputs:
                ui_effects.extend(t_effects)
                tool_trace.append(trace_item)
                serialized = json.dumps(t_res, ensure_ascii=False)
                working_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["name"],
                    "content": serialized[:6000],
                })

    # Reached step cap: ask model to summarize without tools (avoid consecutive user messages in ReAct mode!)
    _check_cancelled()
    _emit({"type": "step_start", "step": MAX_AGENT_STEPS, "maxSteps": MAX_AGENT_STEPS, "phase": "summarizing"})
    cap_instruction = "已达到单轮工具调用上限，请基于目前已获取到的工具执行结果直接给用户清晰总结。"
    if working_messages and working_messages[-1].get("role") == "user":
        working_messages[-1] = {
            **working_messages[-1],
            "content": str(working_messages[-1].get("content") or "") + "\n\n" + cap_instruction,
        }
    else:
        working_messages.append({"role": "user", "content": cap_instruction})

    final_res = llm_caller(
        platform,
        model,
        working_messages,
        timeout=timeout,
        retry_count=1,
    )
    total_duration_ms += final_res.duration_ms
    if final_res.reasoning:
        reasoning_parts.append(final_res.reasoning)
    final_text = _strip_react_tool_tags(final_res.content) if use_react_fallback else final_res.content
    if not (final_text or "").strip():
        final_text = _synthesize_fallback_reply(tool_trace)
    return (
        ModelCallResult(
            content=final_text,
            reasoning="\n\n".join(reasoning_parts).strip(),
            model_id=final_res.model_id or (last_res.model_id if last_res else ""),
            model_name=final_res.model_name or (last_res.model_name if last_res else ""),
            platform_name=final_res.platform_name or (last_res.platform_name if last_res else ""),
            duration_ms=total_duration_ms,
            usage=final_res.usage,
        ),
        tool_trace,
        ui_effects,
    )
