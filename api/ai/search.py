"""Web search enhancement for AI question answering."""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from api.logger import logger
from api.privacy import redact

MAX_RESULTS = 5
SEARCH_TIMEOUT_S = 20
CACHE_TTL_S = 300.0
CACHE_MAX_ENTRIES = 50


class _SearchCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: Dict[str, tuple[float, Dict[str, Any]]] = {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            ts, value = item
            if now - ts > CACHE_TTL_S:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Dict[str, Any]) -> None:
        now = time.monotonic()
        with self._lock:
            self._store[key] = (now, value)
            if len(self._store) > CACHE_MAX_ENTRIES:
                oldest = min(self._store.items(), key=lambda kv: kv[1][0])[0]
                self._store.pop(oldest, None)


_cache = _SearchCache()


def _clean_query(raw: str) -> str:
    text = re.sub(r"https?://\S+", " ", str(raw or ""), flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:400]


def _search_tavily(query: str, api_key: str, session: Optional[requests.Session]) -> List[Dict[str, str]]:
    if not api_key:
        raise ValueError("未配置 Tavily API Key")
    http = session or requests
    resp = http.post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key,
            "query": query,
            "max_results": MAX_RESULTS,
            "search_depth": "basic",
            "include_answer": True,
        },
        timeout=SEARCH_TIMEOUT_S,
    )
    if not resp.ok:
        raise RuntimeError(f"Tavily HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    results: List[Dict[str, str]] = []
    if data.get("answer"):
        results.append({"title": "Tavily 摘要", "url": "", "snippet": str(data["answer"])[:600]})
    for item in data.get("results", [])[:MAX_RESULTS]:
        results.append(
            {
                "title": str(item.get("title") or "")[:200],
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("content") or "")[:600],
            }
        )
    return results


def _search_brave(query: str, api_key: str, session: Optional[requests.Session]) -> List[Dict[str, str]]:
    if not api_key:
        raise ValueError("未配置 Brave Search API Key")
    http = session or requests
    resp = http.get(
        "https://api.search.brave.com/res/v1/web/search",
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        params={"q": query, "count": MAX_RESULTS},
        timeout=SEARCH_TIMEOUT_S,
    )
    if not resp.ok:
        raise RuntimeError(f"Brave HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    items = (data.get("web") or {}).get("results") or []
    return [
        {
            "title": str(item.get("title") or "")[:200],
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("description") or "")[:600],
        }
        for item in items[:MAX_RESULTS]
    ]


def _search_exa(query: str, api_key: str, session: Optional[requests.Session]) -> List[Dict[str, str]]:
    if not api_key:
        raise ValueError("未配置 Exa API Key")
    http = session or requests
    resp = http.post(
        "https://api.exa.ai/search",
        headers={"Content-Type": "application/json", "x-api-key": api_key},
        json={"query": query, "numResults": MAX_RESULTS, "contents": {"text": {"maxCharacters": 600}}},
        timeout=SEARCH_TIMEOUT_S,
    )
    if not resp.ok:
        raise RuntimeError(f"Exa HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return [
        {
            "title": str(item.get("title") or "")[:200],
            "url": str(item.get("url") or ""),
            "snippet": str(item.get("text") or item.get("summary") or "")[:600],
        }
        for item in data.get("results", [])[:MAX_RESULTS]
    ]


def _search_zhipu(query: str, api_key: str, session: Optional[requests.Session]) -> List[Dict[str, str]]:
    if not api_key:
        raise ValueError("未配置智谱 API Key")
    http = session or requests
    resp = http.post(
        "https://open.bigmodel.cn/api/paas/v4/web_search",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={"search_query": query, "search_engine": "search_std", "count": MAX_RESULTS},
        timeout=SEARCH_TIMEOUT_S,
    )
    if not resp.ok:
        raise RuntimeError(f"智谱搜索 HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    items = data.get("search_result") or data.get("results") or []
    return [
        {
            "title": str(item.get("title") or "")[:200],
            "url": str(item.get("link") or item.get("url") or ""),
            "snippet": str(item.get("content") or item.get("snippet") or "")[:600],
        }
        for item in items[:MAX_RESULTS]
    ]


def _search_you(query: str, api_key: str, session: Optional[requests.Session]) -> List[Dict[str, str]]:
    if not api_key:
        raise ValueError("未配置 You.com API Key")
    http = session or requests
    resp = http.get(
        "https://api.ydc-index.io/search",
        headers={"X-API-Key": api_key},
        params={"query": query, "count": MAX_RESULTS},
        timeout=SEARCH_TIMEOUT_S,
    )
    if not resp.ok:
        raise RuntimeError(f"You.com HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    hits = data.get("hits") or []
    return [
        {
            "title": str(item.get("title") or "")[:200],
            "url": str(item.get("url") or ""),
            "snippet": " ".join(str(s) for s in (item.get("snippets") or [])[:3])[:600],
        }
        for item in hits[:MAX_RESULTS]
    ]


def _search_deepseek(query: str, api_key: str, session: Optional[requests.Session]) -> List[Dict[str, str]]:
    if not api_key:
        raise ValueError("未配置 DeepSeek API Key")
    http = session or requests
    resp = http.post(
        "https://api.deepseek.com/v1/chat/completions",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system",
                    "content": "请简明提供与下列题目最相关的权威知识点、定义与关键结论（200字以内），用于辅助核对答案。",
                },
                {"role": "user", "content": query},
            ],
            "max_tokens": 512,
            "temperature": 0.1,
        },
        timeout=SEARCH_TIMEOUT_S,
    )
    if not resp.ok:
        raise RuntimeError(f"DeepSeek 检索 HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not text:
        return []
    return [{"title": "DeepSeek 知识检索", "url": "", "snippet": text[:600]}]


def perform_web_search(
    raw_query: str,
    search_cfg: Dict[str, Any],
    *,
    session: Optional[requests.Session] = None,
) -> Dict[str, Any]:
    query = _clean_query(raw_query)
    provider = str(search_cfg.get("provider") or "tavily").strip().lower()
    if not query:
        return {"status": "skipped", "provider": provider, "query": "", "results": [], "context": "", "durationMs": 0}

    cache_key = f"{provider}:{query}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return dict(cached)

    started = time.monotonic()
    try:
        if provider == "tavily":
            results = _search_tavily(query, str(search_cfg.get("tavilyApiKey") or ""), session)
        elif provider == "brave":
            results = _search_brave(query, str(search_cfg.get("braveApiKey") or ""), session)
        elif provider == "exa":
            results = _search_exa(query, str(search_cfg.get("exaApiKey") or ""), session)
        elif provider == "zhipu":
            results = _search_zhipu(query, str(search_cfg.get("zhipuApiKey") or ""), session)
        elif provider == "you":
            results = _search_you(query, str(search_cfg.get("youApiKey") or ""), session)
        elif provider == "deepseek":
            results = _search_deepseek(query, str(search_cfg.get("deepseekApiKey") or ""), session)
        else:
            raise ValueError(f"未知搜索供应商: {provider}")

        duration_ms = int((time.monotonic() - started) * 1000)
        context = format_search_context(results)
        outcome = {
            "status": "success",
            "provider": provider,
            "query": query,
            "results": results,
            "context": context,
            "durationMs": duration_ms,
        }
        _cache.set(cache_key, outcome)
        return outcome
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        err = redact(exc)
        logger.warning("AI 联网搜题失败 ({}): {}", provider, err)
        return {
            "status": "error",
            "provider": provider,
            "query": query,
            "results": [],
            "context": "",
            "error": err,
            "durationMs": duration_ms,
        }


def format_search_context(results: List[Dict[str, str]]) -> str:
    if not results:
        return ""
    lines = ["【联网搜索参考资料（仅供辅助核对，请结合题目严谨判断）】"]
    for idx, item in enumerate(results[:MAX_RESULTS], start=1):
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        if title or snippet:
            lines.append(f"{idx}. {title}: {snippet}" if title else f"{idx}. {snippet}")
    return "\n".join(lines)
