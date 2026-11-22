"""Multi-protocol LLM client.

Supports:
- openai_chat (/chat/completions)
- openai_responses (/responses)
- anthropic (/messages)
with multimodal images, reasoning/thinking fields, retries, and connection testing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import time
from typing import Any, Dict, List, Optional

import requests

from api.logger import logger
from api.privacy import redact


class ModelCallError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, retryable: bool = True) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class ModelCallResult:
    content: str
    reasoning: str = ""
    model_id: str = ""
    model_name: str = ""
    platform_name: str = ""
    duration_ms: int = 0
    usage: Dict[str, Any] = field(default_factory=dict)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)


def _parse_tool_arguments(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def _strip_code_fence(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[\w-]*\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


def _extract_think_tags(content: str, reasoning: str) -> tuple[str, str]:
    """Some OpenAI-compatible gateways embed <think>...</think> inline."""
    if not content or "<think>" not in content:
        return content, reasoning
    matches = re.findall(r"<think>(.*?)</think>", content, flags=re.S | re.I)
    if not matches:
        return content, reasoning
    extracted = "\n".join(m.strip() for m in matches if m.strip())
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.S | re.I).strip()
    merged_reasoning = (reasoning + "\n" + extracted).strip() if reasoning else extracted
    return cleaned, merged_reasoning


def _resolve_protocol(platform: Dict[str, Any], model: Dict[str, Any]) -> str:
    model_proto = (model.get("apiProtocol") or "").strip()
    if model_proto in ("openai_chat", "openai_responses", "anthropic"):
        return model_proto
    plat_proto = (platform.get("apiProtocol") or "").strip()
    if plat_proto in ("openai_chat", "openai_responses", "anthropic"):
        return plat_proto
    base_url = (platform.get("baseUrl") or "").lower()
    if "anthropic" in base_url:
        return "anthropic"
    return "openai_chat"


def _normalize_endpoint(base_url: str, suffix: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise ModelCallError("未配置平台 Base URL", retryable=False)
    if url.endswith(suffix):
        return url
    return f"{url}{suffix}"


def _join_text_parts(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: List[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item.get("content"), str):
                parts.append(item["content"])
    return "".join(parts)


def _format_http_error(resp: requests.Response) -> ModelCallError:
    status = resp.status_code
    detail = ""
    try:
        body = resp.json()
        err = body.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or err)
        elif err:
            detail = str(err)
        elif body.get("message"):
            detail = str(body["message"])
    except Exception:
        detail = (resp.text or "").strip()[:300]
    retryable = status in (408, 409, 429) or status >= 500
    msg = f"HTTP {status}" + (f": {detail}" if detail else "")
    return ModelCallError(msg, status_code=status, retryable=retryable)


def _to_openai_chat_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        images = msg.get("images") or []
        tool_calls = msg.get("tool_calls") or []
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": str(msg.get("tool_call_id") or ""),
                "content": str(content or ""),
            })
        elif role == "assistant" and tool_calls:
            formatted_calls = []
            for idx, tc in enumerate(tool_calls):
                args = tc.get("arguments")
                args_str = args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False)
                formatted_calls.append({
                    "id": str(tc.get("id") or f"call_{idx + 1}"),
                    "type": "function",
                    "function": {
                        "name": str(tc.get("name") or ""),
                        "arguments": args_str,
                    },
                })
            entry: Dict[str, Any] = {"role": "assistant", "tool_calls": formatted_calls}
            if content:
                entry["content"] = content
            else:
                entry["content"] = None
            out.append(entry)
        elif images and isinstance(content, str):
            parts: List[Dict[str, Any]] = [{"type": "text", "text": content}]
            for img in images:
                if isinstance(img, str) and img.strip():
                    parts.append({"type": "image_url", "image_url": {"url": img.strip()}})
            out.append({"role": role, "content": parts})
        else:
            out.append({"role": role, "content": content})
    return out


def _to_openai_responses_input(messages: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
    instructions: List[str] = []
    items: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        images = msg.get("images") or []
        tool_calls = msg.get("tool_calls") or []
        if role == "system":
            if isinstance(content, str) and content.strip():
                instructions.append(content.strip())
            continue
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": str(msg.get("tool_call_id") or ""),
                "output": str(content or ""),
            })
            continue
        parts: List[Dict[str, Any]] = []
        if isinstance(content, str) and content:
            parts.append({"type": "input_text" if role == "user" else "output_text", "text": content})
        elif isinstance(content, list):
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append({"type": "input_text", "text": str(p.get("text") or "")})
                elif isinstance(p, dict) and p.get("type") == "image_url":
                    url = (p.get("image_url") or {}).get("url")
                    if url:
                        parts.append({"type": "input_image", "image_url": url})
        for img in images:
            if isinstance(img, str) and img.strip():
                parts.append({"type": "input_image", "image_url": img.strip()})
        if parts or not tool_calls:
            items.append({"role": role, "content": parts})
        if role == "assistant" and tool_calls:
            for idx, tc in enumerate(tool_calls):
                args = tc.get("arguments")
                args_str = args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False)
                items.append({
                    "type": "function_call",
                    "call_id": str(tc.get("id") or f"call_{idx + 1}"),
                    "name": str(tc.get("name") or ""),
                    "arguments": args_str,
                })
    return "\n\n".join(instructions), items


def _data_url_to_anthropic_image(url: str) -> Optional[Dict[str, Any]]:
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", url.strip(), flags=re.S)
    if match:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": match.group(1),
                "data": match.group(2),
            },
        }
    if url.startswith(("http://", "https://")):
        return {
            "type": "image",
            "source": {"type": "url", "url": url.strip()},
        }
    return None


def _to_anthropic_payload(messages: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
    system_parts: List[str] = []
    out: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        images = msg.get("images") or []
        tool_calls = msg.get("tool_calls") or []
        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append(content.strip())
            continue
        if role == "tool":
            tool_result_block = {
                "type": "tool_result",
                "tool_use_id": str(msg.get("tool_call_id") or ""),
                "content": str(content or ""),
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(tool_result_block)
            else:
                out.append({"role": "user", "content": [tool_result_block]})
            continue
        blocks: List[Dict[str, Any]] = []
        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    blocks.append({"type": "text", "text": str(part.get("text") or "")})
                elif isinstance(part, dict) and part.get("type") == "image_url":
                    img_block = _data_url_to_anthropic_image(str((part.get("image_url") or {}).get("url") or ""))
                    if img_block:
                        blocks.append(img_block)
        for img in images:
            if isinstance(img, str) and img.strip():
                img_block = _data_url_to_anthropic_image(img)
                if img_block:
                    blocks.append(img_block)
        if role == "assistant" and tool_calls:
            for idx, tc in enumerate(tool_calls):
                blocks.append({
                    "type": "tool_use",
                    "id": str(tc.get("id") or f"call_{idx + 1}"),
                    "name": str(tc.get("name") or ""),
                    "input": _parse_tool_arguments(tc.get("arguments")),
                })
        if not blocks:
            blocks.append({"type": "text", "text": ""})
        mapped_role = "assistant" if role == "assistant" else "user"
        if out and out[-1]["role"] == mapped_role and isinstance(out[-1]["content"], list):
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": mapped_role, "content": blocks})
    return "\n\n".join(system_parts), out


def _to_anthropic_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted: List[Dict[str, Any]] = []
    for item in tools:
        fn = item.get("function") if isinstance(item, dict) else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        converted.append({
            "name": str(fn["name"]),
            "description": str(fn.get("description") or ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted


def _to_responses_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted: List[Dict[str, Any]] = []
    for item in tools:
        fn = item.get("function") if isinstance(item, dict) else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        converted.append({
            "type": "function",
            "name": str(fn["name"]),
            "description": str(fn.get("description") or ""),
            "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted


def _call_once(
    platform: Dict[str, Any],
    model: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    timeout: int = 45,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    session: Optional[requests.Session] = None,
) -> ModelCallResult:
    protocol = _resolve_protocol(platform, model)
    runtime_model = str(model.get("modelId") or model.get("id") or "").strip()
    if not runtime_model:
        raise ModelCallError("模型 ID 为空", retryable=False)
    api_key = str(platform.get("apiKey") or "").strip()
    base_url = str(platform.get("baseUrl") or "").strip()
    if not base_url:
        raise ModelCallError(f"平台 {platform.get('displayName', '')} 未配置接口地址", retryable=False)

    temp = model.get("temperature", 0.2) if temperature is None else temperature
    limit = int(model.get("maxTokens", 4096) if max_tokens is None else max_tokens)
    enable_thinking = bool(model.get("enableThinking", False))
    thinking_effort = str(model.get("thinkingEffort") or "medium").strip().lower()

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if isinstance(platform.get("customHeaders"), dict):
        for k, v in platform["customHeaders"].items():
            if k and v is not None:
                headers[str(k)] = str(v)

    started = time.monotonic()
    http = session or requests
    tool_calls: List[Dict[str, Any]] = []

    if protocol == "anthropic":
        endpoint = _normalize_endpoint(base_url, "/messages")
        if api_key:
            headers["x-api-key"] = api_key
        headers.setdefault("anthropic-version", "2023-06-01")
        system_prompt, anthropic_msgs = _to_anthropic_payload(messages)
        payload: Dict[str, Any] = {
            "model": runtime_model,
            "max_tokens": limit,
            "messages": anthropic_msgs,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            anth_tools = _to_anthropic_tools(tools)
            if anth_tools:
                payload["tools"] = anth_tools
        if enable_thinking:
            budget = max(1024, min(limit - 256, 4096))
            if limit > 1280:
                payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        else:
            payload["temperature"] = temp
        resp = http.post(endpoint, headers=headers, json=payload, timeout=timeout)
        if not resp.ok:
            raise _format_http_error(resp)
        data = resp.json()
        text_parts: List[str] = []
        reasoning_parts: List[str] = []
        for idx, block in enumerate(data.get("content", [])):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(str(block.get("text") or ""))
            elif btype == "thinking":
                reasoning_parts.append(str(block.get("thinking") or ""))
            elif btype == "tool_use" and block.get("name"):
                tool_calls.append({
                    "id": str(block.get("id") or f"call_{idx + 1}"),
                    "name": str(block["name"]),
                    "arguments": _parse_tool_arguments(block.get("input")),
                })
        content = "".join(text_parts).strip()
        reasoning = "\n".join(reasoning_parts).strip()
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

    elif protocol == "openai_responses":
        endpoint = _normalize_endpoint(base_url, "/responses")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        instructions, input_items = _to_openai_responses_input(messages)
        payload = {
            "model": runtime_model,
            "input": input_items,
            "max_output_tokens": limit,
        }
        if instructions:
            payload["instructions"] = instructions
        if tools:
            resp_tools = _to_responses_tools(tools)
            if resp_tools:
                payload["tools"] = resp_tools
        if enable_thinking:
            payload["reasoning"] = {"effort": thinking_effort}
        else:
            payload["temperature"] = temp
        resp = http.post(endpoint, headers=headers, json=payload, timeout=timeout)
        if not resp.ok:
            raise _format_http_error(resp)
        data = resp.json()
        if data.get("error"):
            raise ModelCallError(f"模型响应错误: {data['error']}", retryable=False)
        content = str(data.get("output_text") or "").strip()
        reasoning_parts = []
        if isinstance(data.get("output"), list):
            parts = []
            for idx, item in enumerate(data["output"]):
                if not isinstance(item, dict):
                    continue
                itype = item.get("type")
                if itype == "reasoning":
                    for s in item.get("summary") or []:
                        if isinstance(s, dict) and s.get("text"):
                            reasoning_parts.append(str(s["text"]))
                elif itype == "message":
                    parts.append(_join_text_parts(item.get("content")))
                elif itype == "function_call" and item.get("name"):
                    tool_calls.append({
                        "id": str(item.get("call_id") or item.get("id") or f"call_{idx + 1}"),
                        "name": str(item["name"]),
                        "arguments": _parse_tool_arguments(item.get("arguments")),
                    })
            if not content:
                content = "".join(parts).strip()
        reasoning = "\n".join(reasoning_parts).strip()
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

    else:
        endpoint = _normalize_endpoint(base_url, "/chat/completions")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": runtime_model,
            "messages": _to_openai_chat_messages(messages),
            "stream": False,
            "max_tokens": limit,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        lower_model = runtime_model.lower()
        is_o_series = lower_model.startswith(("o1", "o3", "o4")) or "reasoner" in lower_model
        if enable_thinking:
            if not is_o_series:
                payload["reasoning_effort"] = thinking_effort
        else:
            payload["temperature"] = temp
        resp = http.post(endpoint, headers=headers, json=payload, timeout=timeout)
        if not resp.ok:
            raise _format_http_error(resp)
        data = resp.json()
        if data.get("error"):
            raise ModelCallError(f"模型响应错误: {data['error']}", retryable=False)
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            raise ModelCallError("模型未返回有效 choices", retryable=True)
        message = choices[0].get("message") or {}
        content = _join_text_parts(message.get("content")).strip()
        reasoning = str(
            message.get("reasoning_content")
            or message.get("reasoning")
            or ""
        ).strip()
        for idx, tc in enumerate(message.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            name = str(fn.get("name") or tc.get("name") or "").strip()
            if name:
                tool_calls.append({
                    "id": str(tc.get("id") or f"call_{idx + 1}"),
                    "name": name,
                    "arguments": _parse_tool_arguments(fn.get("arguments")),
                })
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}

    content, reasoning = _extract_think_tags(content, reasoning)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return ModelCallResult(
        content=content,
        reasoning=reasoning,
        model_id=str(model.get("id") or runtime_model),
        model_name=str(model.get("displayName") or runtime_model),
        platform_name=str(platform.get("displayName") or platform.get("name") or ""),
        duration_ms=elapsed_ms,
        usage=usage,
        tool_calls=tool_calls,
    )


def call_model(
    platform: Dict[str, Any],
    model: Dict[str, Any],
    messages: List[Dict[str, Any]],
    *,
    timeout: int = 45,
    retry_count: int = 2,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    session: Optional[requests.Session] = None,
) -> ModelCallResult:
    max_attempts = max(1, int(retry_count) + 1)
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _call_once(
                platform,
                model,
                messages,
                timeout=timeout,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                session=session,
            )
        except ModelCallError as exc:
            last_exc = exc
            if not exc.retryable or attempt >= max_attempts:
                raise
            logger.warning("AI 模型重试 ({}/{}) {}: {}", attempt, max_attempts, model.get("displayName"), redact(exc))
            time.sleep(min(2.0, 0.4 * attempt))
        except requests.Timeout as exc:
            last_exc = ModelCallError(f"请求超时（{timeout}秒）", retryable=True)
            if attempt >= max_attempts:
                raise last_exc from exc
            time.sleep(min(2.0, 0.4 * attempt))
        except requests.RequestException as exc:
            last_exc = ModelCallError(f"网络请求失败: {redact(exc)}", retryable=True)
            if attempt >= max_attempts:
                raise last_exc from exc
            time.sleep(min(2.0, 0.4 * attempt))
    assert last_exc is not None
    raise last_exc


def test_model_connection(
    platform: Dict[str, Any],
    model: Dict[str, Any],
    *,
    timeout: int = 20,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        res = call_model(
            platform,
            model,
            [{"role": "user", "content": "请回复 OK"}],
            timeout=timeout,
            retry_count=0,
            max_tokens=64,
            session=session,
        )
        return {
            "ok": True,
            "latencyMs": res.duration_ms,
            "reply": res.content[:120],
            "modelName": res.model_name,
            "platformName": res.platform_name,
        }
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "latencyMs": elapsed,
            "error": redact(exc),
            "modelName": str(model.get("displayName") or model.get("modelId") or ""),
            "platformName": str(platform.get("displayName") or platform.get("name") or ""),
        }
