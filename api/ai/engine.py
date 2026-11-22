"""Core AI question-answering pipeline.

Combines:
1. Local SQLite exact match
2. AI same-question deduplication check
3. Web search enhancement
4. Vision multimodal direct answering for image questions (api/question_images.py)
5. Multi-model concurrent base answering with structured few-shot prompts
6. Summary/review tier model synthesis + majority-vote fallback
7. Auto-caching into SQLite + RequestLogs recording
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from api.logger import logger
from api.privacy import redact
from api.question_images import download_image, png_data_url, validate_images
from api.ai.config import AIConfigStore
from api.ai.db import AIDB, normalize_question_text
from api.ai.llm import ModelCallResult, call_model
from api.ai.search import perform_web_search

_IMG_SRC_RE = re.compile(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>', re.IGNORECASE)

_SYSTEM_RULES = (
    '你是专业答题助手。最终只输出一行 JSON：{"answer":"答案正文"}。\n'
    '选择题 answer 必须是选项正文（与选项原文去掉 A./B. 后一致），禁止写 A/B/C/D，禁止写「B. 传动角」这种带字母前缀的形式。\n'
    '判断题 answer 只能是「正确」或「错误」。\n'
    '多选题多个答案用 ### 连接；填空题多空也用 ### 连接。'
)

_FEW_SHOTS: Dict[str, List[Tuple[str, str]]] = {
    "single": [
        (
            "【题目类型：单选题】\n【题目】\n凸轮机构中，从动件运动规律取决于（ ）。\n"
            "【选项】\nA. 压力角\nB. 传动角\nC. 极力夹角\n\n请作答，并在最后输出答案 JSON：",
            '{"answer":"传动角"}',
        ),
        (
            '【题目类型：单选题】\n【题目】\nThe gerund of the verb "meet" is ____.\n'
            "【选项】\nA. meet\nB. meeted\nC. to meet\nD. meeting\n\n请作答，并在最后输出答案 JSON：",
            '{"answer":"meeting"}',
        ),
    ],
    "multiple": [
        (
            "【题目类型：多选题】\n【题目】\n下列属于输入设备的有（ ）。\n"
            "【选项】\nA. 键盘\nB. 显示器\nC. 鼠标\nD. 打印机\n\n请作答，并在最后输出答案 JSON：",
            '{"answer":"键盘###鼠标"}',
        ),
    ],
    "judgement": [
        (
            "【题目类型：判断题】\n【题目】\n地球绕太阳公转一周约为 365 天。\n\n请作答，并在最后输出答案 JSON：",
            '{"answer":"正确"}',
        ),
        (
            "【题目类型：判断题】\n【题目】\n纯净水的 pH 值一定等于 7。\n\n请作答，并在最后输出答案 JSON：",
            '{"answer":"错误"}',
        ),
    ],
    "completion": [
        (
            "【题目类型：填空题】\n【题目】\n中国的首都是____，最大的城市是____。\n\n请作答，并在最后输出答案 JSON：",
            '{"answer":"北京###上海"}',
        ),
    ],
}

_TYPE_LABELS = {
    "single": "单选题",
    "multiple": "多选题",
    "judgement": "判断题",
    "completion": "填空题",
    "short": "简答题",
}


def strip_markdown_code_block(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```[\w-]*\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


def extract_answer_from_output(raw: str) -> str:
    text = strip_markdown_code_block(raw)
    if not text:
        return ""
    # Try JSON object extraction (prefer last JSON block in case model echoed reasoning)
    for match in reversed(list(re.finditer(r"\{[^{}]*\"answer\"[^{}]*\}", text, flags=re.S))):
        try:
            parsed = json.loads(match.group(0))
            ans = parsed.get("answer")
            if isinstance(ans, list):
                joined = "###".join(str(x).strip() for x in ans if str(x).strip())
                if joined:
                    return joined
            elif ans is not None and str(ans).strip():
                return str(ans).strip()
        except Exception:
            continue
    start = text.find("{")
    end = text.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(text[start : end + 1])
            ans = parsed.get("answer")
            if isinstance(ans, list):
                joined = "###".join(str(x).strip() for x in ans if str(x).strip())
                if joined:
                    return joined
            elif ans is not None and str(ans).strip():
                return str(ans).strip()
        except Exception:
            pass
    # Fallback: clean plain text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else text


def normalize_question_type(raw_type: Any) -> str:
    val = str(raw_type or "").strip().lower()
    if val in ("single", "单选题", "单选", "0"):
        return "single"
    if val in ("multiple", "多选题", "多选", "1"):
        return "multiple"
    if val in ("judgement", "judgment", "判断题", "判断", "3"):
        return "judgement"
    if val in ("completion", "填空题", "填空", "2"):
        return "completion"
    if val in ("short", "简答题", "简答", "论述题"):
        return "short"
    return val or "single"


def format_options_block(options: Any) -> str:
    if not options:
        return ""
    if isinstance(options, list):
        lines = [str(item).strip() for item in options if str(item).strip()]
    else:
        lines = [ln.strip() for ln in str(options).splitlines() if ln.strip()]
    return "\n".join(lines)


def build_question_prompt(title: str, options_text: str, qtype: str, *, has_images: bool = False) -> str:
    label = _TYPE_LABELS.get(qtype, "通用题")
    parts = [f"【题目类型：{label}】", "【题目】", title.strip()]
    if options_text.strip():
        parts.extend(["【选项】", options_text.strip()])
    if has_images:
        parts.append("（注意：本题包含图片，请仔细识别附带图片中的公式、图表或文字后再作答。）")
    parts.append('\n请作答，并在最后输出答案 JSON：{"answer":"..."}')
    return "\n".join(parts)


def build_answer_messages(
    title: str,
    options_text: str,
    qtype: str,
    *,
    search_context: str = "",
    images: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    shots = _FEW_SHOTS.get(qtype) or [_FEW_SHOTS["single"][0], _FEW_SHOTS["judgement"][0]]
    messages: List[Dict[str, Any]] = [{"role": "system", "content": _SYSTEM_RULES}]
    for user_ex, asst_ex in shots:
        messages.append({"role": "user", "content": user_ex})
        messages.append({"role": "assistant", "content": asst_ex})
    prompt = build_question_prompt(title, options_text, qtype, has_images=bool(images))
    if search_context:
        prompt = f"{search_context}\n\n{prompt}"
    user_msg: Dict[str, Any] = {"role": "user", "content": prompt}
    if images:
        user_msg["images"] = images
    messages.append(user_msg)
    return messages


def get_most_frequent_answer(answers: List[str]) -> str:
    stats: Dict[str, Tuple[int, int, str]] = {}
    for idx, ans in enumerate(answers):
        orig = str(ans or "").strip()
        key = re.sub(r"\s+", " ", orig).lower()
        if not key:
            continue
        if key in stats:
            cnt, first_idx, sample = stats[key]
            stats[key] = (cnt + 1, first_idx, sample)
        else:
            stats[key] = (1, idx, orig)
    if not stats:
        return ""
    best = max(stats.values(), key=lambda item: (item[0], -item[1]))
    return best[2]


def _collect_question_images(q_info: Dict[str, Any]) -> List[str]:
    """Collect validated data-URL images from caller-provided images or Chaoxing img tags."""
    images: List[str] = []
    raw_images = q_info.get("images")
    if isinstance(raw_images, list) and raw_images:
        try:
            images.extend(validate_images([x for x in raw_images if isinstance(x, str) and x.startswith("data:image/")]))
        except ValueError as exc:
            logger.debug("AI 忽略无效图片列表: {}", exc)

    # Also check Chaoxing _image_context urls or <img> tags in title/options
    urls: List[str] = []
    ctx = q_info.get("_image_context")
    if isinstance(ctx, dict) and isinstance(ctx.get("urls"), list):
        urls.extend(str(u) for u in ctx["urls"] if isinstance(u, str))
    if not urls:
        for field_val in (q_info.get("title"), format_options_block(q_info.get("options"))):
            if isinstance(field_val, str) and "<img" in field_val.lower():
                urls.extend(_IMG_SRC_RE.findall(field_val))

    seen = set()
    for url in urls:
        u = url.strip()
        if not u or u in seen or len(images) >= 6:
            continue
        seen.add(u)
        try:
            raw_bytes = download_image(u)
            images.append(png_data_url(raw_bytes))
        except Exception as exc:
            logger.debug("AI 下载题目图片跳过 ({}): {}", redact(u), exc)
    return images


def _build_same_question_prompt(title: str, options_text: str, candidates: List[Dict[str, Any]]) -> str:
    cand_lines = []
    for idx, c in enumerate(candidates, start=1):
        opt = f"\n选项：{c['options']}" if c.get("options") else ""
        cand_lines.append(f"候选{idx}（id={c['id']}）：\n题干：{c['question']}{opt}")
    opt_block = f"\n【新题选项】\n{options_text}\n" if options_text else ""
    return "\n".join(
        [
            "你是题目判重助手。请判断「新题目」是否与下列候选中的某一道是同一道题（允许措辞略有差异，但题意与考点必须一致）。",
            "题型、否定词、关键数字、条件、公式、选项含义或已标明的填空位置不同，判为不同题。选项顺序变化可以忽略，但选项内容必须一致。",
            "只输出一个 JSON 对象，不要代码块，不要其他文字。",
            '若是同一题：{"same":true,"matched_id":<候选id>}',
            '若都不是：{"same":false}',
            "",
            "【新题目】",
            title,
            opt_block,
            "【候选题目】",
            "\n\n".join(cand_lines),
        ]
    )


def _parse_same_question_result(raw: str) -> Tuple[bool, Optional[int]]:
    text = strip_markdown_code_block(raw)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return False, None
    try:
        parsed = json.loads(text[start : end + 1])
        if parsed.get("same") is True and parsed.get("matched_id") is not None:
            return True, int(parsed["matched_id"])
    except Exception:
        pass
    return False, None


class AIEngine:
    def __init__(self, db: AIDB, config_store: AIConfigStore) -> None:
        self.db = db
        self.config_store = config_store

    def lookup_local(
        self,
        q_info: Dict[str, Any],
        *,
        cfg: Optional[Dict[str, Any]] = None,
        allow_same_check: bool = True,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """Lookup local SQLite DB (exact match first, then optional AI same-question check)."""
        title = normalize_question_text(str(q_info.get("title") or q_info.get("question") or ""))
        options_text = format_options_block(q_info.get("options"))
        qtype = normalize_question_type(q_info.get("type") or q_info.get("questionType"))
        if not title:
            return None, "miss"

        exact = self.db.find_exact_local_match(title, options_text, qtype)
        if exact:
            return exact, "hit"

        config = cfg if cfg is not None else self.config_store.load()
        if not allow_same_check or not config["engine"].get("enableSameQuestionCheck", True):
            return None, "miss"

        candidates = self.db.find_similar_candidates(title, options_text, qtype, limit=5, min_ratio=0.58)
        if not candidates:
            return None, "miss"

        text_models = self.config_store.resolve_text_models(config)
        if not text_models:
            return None, "miss"

        platform, model = text_models[0]
        prompt = _build_same_question_prompt(title, options_text, candidates)
        try:
            res = call_model(
                platform,
                model,
                [{"role": "user", "content": prompt}],
                timeout=min(30, int(config["engine"].get("modelTimeout", 45))),
                retry_count=0,
                max_tokens=256,
            )
            same, matched_id = _parse_same_question_result(res.content)
            if same and matched_id is not None:
                for cand in candidates:
                    if int(cand["id"]) == matched_id:
                        return cand, "same_hit"
        except Exception as exc:
            logger.debug("AI 同题判重跳过: {}", redact(exc))
        return None, "miss"

    def answer_question(
        self,
        q_info: Dict[str, Any],
        *,
        allow_local: bool = True,
        allow_ai: bool = True,
        record_log: bool = True,
        request_path: str = "/query",
        request_method: str = "POST",
    ) -> Dict[str, Any]:
        started = time.monotonic()
        request_id = uuid.uuid4().hex[:12]
        cfg = self.config_store.load()
        engine_cfg = cfg["engine"]
        title_raw = str(q_info.get("title") or q_info.get("question") or "").strip()
        title = normalize_question_text(title_raw)
        options_text = format_options_block(q_info.get("options"))
        qtype = normalize_question_type(q_info.get("type") or q_info.get("questionType"))

        req_snapshot = {
            "title": title,
            "options": options_text,
            "type": qtype,
        }

        if not title:
            return {
                "ok": False,
                "requestId": request_id,
                "answer": "",
                "source": "error",
                "error": "题目内容为空",
                "durationMs": 0,
            }

        # 1. Local SQLite lookup (exact + optional AI same-question check)
        local_status = "miss"
        if allow_local:
            matched, local_status = self.lookup_local(
                {"title": title, "options": options_text, "type": qtype},
                cfg=cfg,
                allow_same_check=allow_ai,
            )
            if matched and matched.get("answer"):
                elapsed = int((time.monotonic() - started) * 1000)
                result = {
                    "ok": True,
                    "requestId": request_id,
                    "answer": matched["answer"],
                    "source": "local_same" if local_status == "same_hit" else "local",
                    "localSearch": local_status,
                    "matchedQuestionId": matched["id"],
                    "modelName": matched.get("modelName") or "本地题库",
                    "reasoning": "",
                    "modelResponses": [],
                    "searchTraces": [],
                    "durationMs": elapsed,
                }
                if record_log:
                    self.db.record_request_log(
                        request_id=request_id,
                        method=request_method,
                        path=request_path,
                        status=200,
                        response_time_ms=elapsed,
                        request_body=req_snapshot,
                        response_body={"answer": result["answer"], "source": result["source"]},
                        local_search=local_status,
                        model_name=result["modelName"],
                    )
                return result

        if not allow_ai:
            elapsed = int((time.monotonic() - started) * 1000)
            return {
                "ok": False,
                "requestId": request_id,
                "answer": "",
                "source": "miss",
                "localSearch": local_status,
                "error": "本地题库未命中",
                "durationMs": elapsed,
            }

        # 2. Collect question images if present
        images: List[str] = []
        if engine_cfg.get("enableVisionForImages", True):
            images = _collect_question_images(q_info)

        # 3. Optional web search enhancement
        search_traces: List[Dict[str, Any]] = []
        search_context = ""
        if cfg["search"].get("enabled"):
            search_query = f"{title} {options_text[:160]}".strip()
            trace = perform_web_search(search_query, cfg["search"])
            search_traces.append(trace)
            if trace.get("status") == "success":
                search_context = trace.get("context") or ""

        timeout = int(engine_cfg.get("modelTimeout", 45))
        retry_count = int(engine_cfg.get("modelRetryCount", 2))

        # 4. Vision direct answering if images exist and a vision model is available
        if images:
            vision_pair = self.config_store.resolve_vision_model(cfg)
            if vision_pair:
                v_plat, v_model = vision_pair
                messages = build_answer_messages(
                    title, options_text, qtype, search_context=search_context, images=images
                )
                try:
                    vres = call_model(
                        v_plat,
                        v_model,
                        messages,
                        timeout=timeout,
                        retry_count=retry_count,
                    )
                    answer = extract_answer_from_output(vres.content)
                    if answer:
                        elapsed = int((time.monotonic() - started) * 1000)
                        if engine_cfg.get("autoSaveToBank", True):
                            self.db.upsert_cached_answer(
                                question=title,
                                options=options_text,
                                question_type=qtype,
                                answer=answer,
                                model_name=vres.model_name,
                                folder_id=int(engine_cfg.get("defaultFolderId", 0)),
                                is_ai=True,
                            )
                        model_resp = [
                            {
                                "modelId": vres.model_id,
                                "modelName": f"{vres.model_name} (视觉)",
                                "platformName": vres.platform_name,
                                "raw": vres.content,
                                "answer": answer,
                                "reasoning": vres.reasoning,
                                "durationMs": vres.duration_ms,
                                "success": True,
                            }
                        ]
                        result = {
                            "ok": True,
                            "requestId": request_id,
                            "answer": answer,
                            "source": "ai_vision",
                            "localSearch": local_status,
                            "modelName": f"{vres.model_name} (视觉)",
                            "reasoning": vres.reasoning,
                            "modelResponses": model_resp,
                            "searchTraces": search_traces,
                            "durationMs": elapsed,
                        }
                        if record_log:
                            self.db.record_request_log(
                                request_id=request_id,
                                method=request_method,
                                path=request_path,
                                status=200,
                                response_time_ms=elapsed,
                                request_body=req_snapshot,
                                response_body={"answer": answer, "source": "ai_vision", "models": model_resp},
                                local_search=local_status,
                                model_name=result["modelName"],
                                reasoning=vres.reasoning,
                                search_traces=search_traces,
                            )
                        return result
                except Exception as exc:
                    logger.warning("AI 视觉模型直答失败，回退文本模型: {}", redact(exc))

        # 5. Concurrent base text models
        base_models = self.config_store.resolve_text_models(cfg)
        if not base_models:
            elapsed = int((time.monotonic() - started) * 1000)
            err = "未选择任何答题模型，请先在 AI 模型设置中配置 API Key 并勾选模型"
            if record_log:
                self.db.record_request_log(
                    request_id=request_id,
                    method=request_method,
                    path=request_path,
                    status=400,
                    response_time_ms=elapsed,
                    request_body=req_snapshot,
                    response_body={"error": err},
                    stage="error",
                    local_search=local_status,
                    search_traces=search_traces,
                )
            return {
                "ok": False,
                "requestId": request_id,
                "answer": "",
                "source": "error",
                "localSearch": local_status,
                "error": err,
                "modelResponses": [],
                "searchTraces": search_traces,
                "durationMs": elapsed,
            }

        base_messages = build_answer_messages(title, options_text, qtype, search_context=search_context)
        per_model_retry = retry_count if len(base_models) == 1 else min(1, retry_count)

        def _invoke_one(pair: Tuple[Dict[str, Any], Dict[str, Any]]) -> Dict[str, Any]:
            plat, mod = pair
            try:
                res: ModelCallResult = call_model(
                    plat, mod, base_messages, timeout=timeout, retry_count=per_model_retry
                )
                ans = extract_answer_from_output(res.content)
                return {
                    "modelId": res.model_id,
                    "modelName": res.model_name,
                    "platformName": res.platform_name,
                    "raw": res.content,
                    "answer": ans,
                    "reasoning": res.reasoning,
                    "durationMs": res.duration_ms,
                    "success": bool(ans),
                    "error": "" if ans else "模型返回空答案",
                }
            except Exception as exc:
                return {
                    "modelId": mod["id"],
                    "modelName": mod["displayName"],
                    "platformName": plat["displayName"],
                    "raw": "",
                    "answer": "",
                    "reasoning": "",
                    "durationMs": 0,
                    "success": False,
                    "error": redact(exc),
                }

        model_responses: List[Dict[str, Any]] = []
        if len(base_models) == 1:
            model_responses.append(_invoke_one(base_models[0]))
        else:
            with ThreadPoolExecutor(max_workers=min(5, len(base_models))) as pool:
                futures = [pool.submit(_invoke_one, pair) for pair in base_models]
                for fut in as_completed(futures):
                    model_responses.append(fut.result())

        successful = [m for m in model_responses if m["success"] and m["answer"]]
        if not successful:
            elapsed = int((time.monotonic() - started) * 1000)
            err = "; ".join(f"{m['modelName']}: {m['error']}" for m in model_responses) or "模型均未返回有效答案"
            if record_log:
                self.db.record_request_log(
                    request_id=request_id,
                    method=request_method,
                    path=request_path,
                    status=502,
                    response_time_ms=elapsed,
                    request_body=req_snapshot,
                    response_body={"error": err, "models": model_responses},
                    stage="error",
                    local_search=local_status,
                    search_traces=search_traces,
                )
            return {
                "ok": False,
                "requestId": request_id,
                "answer": "",
                "source": "error",
                "localSearch": local_status,
                "error": err,
                "modelResponses": model_responses,
                "searchTraces": search_traces,
                "durationMs": elapsed,
            }

        # 6. Summary / review tier model synthesis (if configured)
        summary_models = self.config_store.resolve_summary_models(cfg)
        final_answer = ""
        final_model_name = ""
        final_reasoning = ""

        if summary_models:
            s_plat, s_model = summary_models[0]
            combined = "\n\n".join(f"[{m['modelName']}]\n{m['answer']}" for m in successful)
            summary_prompt = (
                "你是一个严谨的答题总结复核专家。下面是原始题目以及多个基础 AI 模型的回答。\n"
                "请仔细核对题意、选项与逻辑，综合得出最准确的一条最终答案。\n"
                '只输出一行 JSON：{"answer":"最终答案正文"}。\n'
                "选择题 answer 必须是选项正文（不要带 A./B. 前缀），多选或填空多空用 ### 连接，判断题只能是 正确 或 错误。\n\n"
                f"{build_question_prompt(title, options_text, qtype)}\n"
                + (f"\n{search_context}\n" if search_context else "")
                + f"\n基础模型回答：\n{combined}\n\n请输出最终复核答案 JSON："
            )
            try:
                sres = call_model(
                    s_plat,
                    s_model,
                    [{"role": "system", "content": _SYSTEM_RULES}, {"role": "user", "content": summary_prompt}],
                    timeout=timeout,
                    retry_count=1,
                )
                s_ans = extract_answer_from_output(sres.content)
                model_responses.append(
                    {
                        "modelId": f"summary:{sres.model_id}",
                        "modelName": f"总结复核: {sres.model_name}",
                        "platformName": sres.platform_name,
                        "raw": sres.content,
                        "answer": s_ans,
                        "reasoning": sres.reasoning,
                        "durationMs": sres.duration_ms,
                        "success": bool(s_ans),
                        "isSummary": True,
                    }
                )
                if s_ans:
                    final_answer = s_ans
                    final_model_name = f"{sres.model_name} (总结复核)"
                    final_reasoning = sres.reasoning
            except Exception as exc:
                logger.warning("AI 总结复核模型失败，回退基础模型多数投票: {}", redact(exc))
                model_responses.append(
                    {
                        "modelId": f"summary:{s_model['id']}",
                        "modelName": f"总结复核: {s_model['displayName']}",
                        "platformName": s_plat["displayName"],
                        "raw": "",
                        "answer": "",
                        "reasoning": "",
                        "durationMs": 0,
                        "success": False,
                        "isSummary": True,
                        "error": redact(exc),
                    }
                )

        if not final_answer:
            if len(successful) == 1:
                final_answer = successful[0]["answer"]
                final_model_name = successful[0]["modelName"]
                final_reasoning = successful[0]["reasoning"]
            else:
                final_answer = get_most_frequent_answer([m["answer"] for m in successful])
                final_model_name = " / ".join(m["modelName"] for m in successful)
                final_reasoning = "\n\n".join(
                    f"[{m['modelName']}]\n{m['reasoning']}" for m in successful if m.get("reasoning")
                )

        # 7. Auto-save answer into local SQLite question bank
        if final_answer and engine_cfg.get("autoSaveToBank", True):
            try:
                self.db.upsert_cached_answer(
                    question=title,
                    options=options_text,
                    question_type=qtype,
                    answer=final_answer,
                    model_name=final_model_name,
                    folder_id=int(engine_cfg.get("defaultFolderId", 0)),
                    is_ai=True,
                )
            except Exception as exc:
                logger.warning("AI 自动写入本地题库失败: {}", exc)

        elapsed = int((time.monotonic() - started) * 1000)
        result = {
            "ok": True,
            "requestId": request_id,
            "answer": final_answer,
            "source": "ai",
            "localSearch": local_status,
            "modelName": final_model_name,
            "reasoning": final_reasoning,
            "modelResponses": model_responses,
            "searchTraces": search_traces,
            "durationMs": elapsed,
        }
        if record_log:
            self.db.record_request_log(
                request_id=request_id,
                method=request_method,
                path=request_path,
                status=200,
                response_time_ms=elapsed,
                request_body=req_snapshot,
                response_body={"answer": final_answer, "source": "ai", "models": model_responses},
                local_search=local_status,
                model_name=final_model_name,
                reasoning=final_reasoning,
                search_traces=search_traces,
            )
        return result
