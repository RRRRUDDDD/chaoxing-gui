"""AI platform, model, search, and engine configuration store."""
from __future__ import annotations

import copy
import json
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

from api.logger import logger
from api.paths import data_dir
from api.privacy import register_config, register_secret

MAX_ANSWER_MODELS = 5

VALID_PROTOCOLS = ("openai_chat", "openai_responses", "anthropic")
VALID_PRIORITIES = ("local_ocs_ai", "local_ai_ocs", "local_ai_only", "ocs_only")
VALID_SEARCH_PROVIDERS = ("tavily", "brave", "exa", "zhipu", "you", "deepseek")

SEARCH_PROVIDERS_META: List[Dict[str, str]] = [
    {"id": "tavily", "name": "Tavily", "keyField": "tavilyApiKey", "website": "https://www.tavily.com/", "toolName": "tavily_search"},
    {"id": "brave", "name": "Brave Search", "keyField": "braveApiKey", "website": "https://brave.com/search/api/", "toolName": "brave_search"},
    {"id": "exa", "name": "Exa", "keyField": "exaApiKey", "website": "https://exa.ai/", "toolName": "web_search_exa"},
    {"id": "zhipu", "name": "智谱联网搜索", "keyField": "zhipuApiKey", "website": "https://open.bigmodel.cn/", "toolName": "zhipu_search"},
    {"id": "you", "name": "You.com", "keyField": "youApiKey", "website": "https://you.com/", "toolName": "you_search"},
    {"id": "deepseek", "name": "DeepSeek", "keyField": "deepseekApiKey", "website": "https://api-docs.deepseek.com/", "toolName": "deepseek_search"},
]


def default_platforms() -> List[Dict[str, Any]]:
    return [
        {
            "id": "deepseek",
            "name": "DeepSeek",
            "displayName": "DeepSeek",
            "baseUrl": "https://api.deepseek.com/v1",
            "apiProtocol": "openai_chat",
            "apiKey": "",
            "enabled": True,
            "customHeaders": {},
            "isPreset": True,
            "models": [
                {
                    "id": "deepseek/deepseek-chat",
                    "modelId": "deepseek-chat",
                    "displayName": "DeepSeek Chat (V3)",
                    "platformId": "deepseek",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
                {
                    "id": "deepseek/deepseek-reasoner",
                    "modelId": "deepseek-reasoner",
                    "displayName": "DeepSeek Reasoner (R1)",
                    "platformId": "deepseek",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": True,
                    "thinkingEffort": "high",
                    "temperature": 0.2,
                    "maxTokens": 8192,
                },
            ],
        },
        {
            "id": "openai",
            "name": "OpenAI",
            "displayName": "OpenAI",
            "baseUrl": "https://api.openai.com/v1",
            "apiProtocol": "openai_chat",
            "apiKey": "",
            "enabled": True,
            "customHeaders": {},
            "isPreset": True,
            "models": [
                {
                    "id": "openai/gpt-4o",
                    "modelId": "gpt-4o",
                    "displayName": "GPT-4o (视觉)",
                    "platformId": "openai",
                    "enabled": True,
                    "hasVision": True,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
                {
                    "id": "openai/gpt-4o-mini",
                    "modelId": "gpt-4o-mini",
                    "displayName": "GPT-4o mini (视觉)",
                    "platformId": "openai",
                    "enabled": True,
                    "hasVision": True,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
                {
                    "id": "openai/o3-mini",
                    "modelId": "o3-mini",
                    "displayName": "o3-mini (推理)",
                    "platformId": "openai",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": True,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 8192,
                },
            ],
        },
        {
            "id": "anthropic",
            "name": "Anthropic",
            "displayName": "Anthropic Claude",
            "baseUrl": "https://api.anthropic.com/v1",
            "apiProtocol": "anthropic",
            "apiKey": "",
            "enabled": True,
            "customHeaders": {},
            "isPreset": True,
            "models": [
                {
                    "id": "anthropic/claude-3-7-sonnet-latest",
                    "modelId": "claude-3-7-sonnet-latest",
                    "displayName": "Claude 3.7 Sonnet (视觉)",
                    "platformId": "anthropic",
                    "enabled": True,
                    "hasVision": True,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
                {
                    "id": "anthropic/claude-3-5-haiku-latest",
                    "modelId": "claude-3-5-haiku-latest",
                    "displayName": "Claude 3.5 Haiku",
                    "platformId": "anthropic",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
            ],
        },
        {
            "id": "gemini",
            "name": "Gemini",
            "displayName": "Google Gemini",
            "baseUrl": "https://generativelanguage.googleapis.com/v1beta/openai",
            "apiProtocol": "openai_chat",
            "apiKey": "",
            "enabled": True,
            "customHeaders": {},
            "isPreset": True,
            "models": [
                {
                    "id": "gemini/gemini-2.5-flash",
                    "modelId": "gemini-2.5-flash",
                    "displayName": "Gemini 2.5 Flash (视觉)",
                    "platformId": "gemini",
                    "enabled": True,
                    "hasVision": True,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
                {
                    "id": "gemini/gemini-2.5-pro",
                    "modelId": "gemini-2.5-pro",
                    "displayName": "Gemini 2.5 Pro (视觉+推理)",
                    "platformId": "gemini",
                    "enabled": True,
                    "hasVision": True,
                    "enableThinking": True,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 8192,
                },
            ],
        },
        {
            "id": "siliconflow",
            "name": "SiliconFlow",
            "displayName": "硅基流动 SiliconFlow",
            "baseUrl": "https://api.siliconflow.cn/v1",
            "apiProtocol": "openai_chat",
            "apiKey": "",
            "enabled": True,
            "customHeaders": {},
            "isPreset": True,
            "models": [
                {
                    "id": "siliconflow/Qwen/Qwen2.5-72B-Instruct",
                    "modelId": "Qwen/Qwen2.5-72B-Instruct",
                    "displayName": "Qwen2.5-72B-Instruct",
                    "platformId": "siliconflow",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
                {
                    "id": "siliconflow/Qwen/Qwen2.5-VL-72B-Instruct",
                    "modelId": "Qwen/Qwen2.5-VL-72B-Instruct",
                    "displayName": "Qwen2.5-VL-72B (视觉)",
                    "platformId": "siliconflow",
                    "enabled": True,
                    "hasVision": True,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
                {
                    "id": "siliconflow/deepseek-ai/DeepSeek-R1",
                    "modelId": "deepseek-ai/DeepSeek-R1",
                    "displayName": "DeepSeek-R1 (推理)",
                    "platformId": "siliconflow",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": True,
                    "thinkingEffort": "high",
                    "temperature": 0.2,
                    "maxTokens": 8192,
                },
            ],
        },
        {
            "id": "dashscope",
            "name": "DashScope",
            "displayName": "阿里云百炼 DashScope",
            "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "apiProtocol": "openai_chat",
            "apiKey": "",
            "enabled": True,
            "customHeaders": {},
            "isPreset": True,
            "models": [
                {
                    "id": "dashscope/qwen-plus",
                    "modelId": "qwen-plus",
                    "displayName": "Qwen Plus",
                    "platformId": "dashscope",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
                {
                    "id": "dashscope/qwen-vl-max",
                    "modelId": "qwen-vl-max",
                    "displayName": "Qwen-VL-Max (视觉)",
                    "platformId": "dashscope",
                    "enabled": True,
                    "hasVision": True,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
            ],
        },
        {
            "id": "zhipu",
            "name": "ZhipuAI",
            "displayName": "智谱 AI BigModel",
            "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
            "apiProtocol": "openai_chat",
            "apiKey": "",
            "enabled": True,
            "customHeaders": {},
            "isPreset": True,
            "models": [
                {
                    "id": "zhipu/glm-4-flash",
                    "modelId": "glm-4-flash",
                    "displayName": "GLM-4-Flash",
                    "platformId": "zhipu",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
                {
                    "id": "zhipu/glm-4v-plus",
                    "modelId": "glm-4v-plus",
                    "displayName": "GLM-4V-Plus (视觉)",
                    "platformId": "zhipu",
                    "enabled": True,
                    "hasVision": True,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
            ],
        },
        {
            "id": "moonshot",
            "name": "Moonshot",
            "displayName": "Moonshot Kimi",
            "baseUrl": "https://api.moonshot.cn/v1",
            "apiProtocol": "openai_chat",
            "apiKey": "",
            "enabled": True,
            "customHeaders": {},
            "isPreset": True,
            "models": [
                {
                    "id": "moonshot/moonshot-v1-8k",
                    "modelId": "moonshot-v1-8k",
                    "displayName": "Moonshot v1 8K",
                    "platformId": "moonshot",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
            ],
        },
        {
            "id": "ollama",
            "name": "Ollama",
            "displayName": "Ollama 本地模型",
            "baseUrl": "http://127.0.0.1:11434/v1",
            "apiProtocol": "openai_chat",
            "apiKey": "ollama",
            "enabled": False,
            "customHeaders": {},
            "isPreset": True,
            "models": [
                {
                    "id": "ollama/qwen2.5:7b",
                    "modelId": "qwen2.5:7b",
                    "displayName": "Qwen2.5 7B (本地)",
                    "platformId": "ollama",
                    "enabled": True,
                    "hasVision": False,
                    "enableThinking": False,
                    "thinkingEffort": "medium",
                    "temperature": 0.2,
                    "maxTokens": 4096,
                },
            ],
        },
    ]


def default_config() -> Dict[str, Any]:
    return {
        "platforms": default_platforms(),
        "selectedTextModels": [],
        "selectedSummaryModels": [],
        "selectedVisionModel": None,
        "selectedAgentModel": None,
        "search": {
            "enabled": False,
            "provider": "tavily",
            "tavilyApiKey": "",
            "braveApiKey": "",
            "exaApiKey": "",
            "zhipuApiKey": "",
            "youApiKey": "",
            "deepseekApiKey": "",
        },
        "engine": {
            "enabled": False,
            "priority": "local_ocs_ai",
            "autoSaveToBank": True,
            "defaultFolderId": 0,
            "enableSameQuestionCheck": True,
            "enableVisionForImages": True,
            "modelTimeout": 45,
            "modelRetryCount": 2,
            "localServerEnabled": False,
            "localServerPort": 3005,
            "localServerToken": "",
        },
    }


def _normalize_model(raw: Any, platform_id: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    raw_id = str(raw.get("id") or "").strip()
    model_id = str(raw.get("modelId") or raw.get("model_id") or "").strip()
    if not model_id and raw_id:
        model_id = raw_id.split("/", 1)[1] if "/" in raw_id else raw_id
    if not raw_id and model_id:
        raw_id = f"{platform_id}/{model_id}"
    if not raw_id or not model_id:
        return None
    display_name = str(raw.get("displayName") or raw.get("name") or model_id).strip() or model_id
    protocol = str(raw.get("apiProtocol") or "").strip()
    if protocol not in VALID_PROTOCOLS:
        protocol = ""
    try:
        temperature = float(raw.get("temperature", 0.2))
    except (TypeError, ValueError):
        temperature = 0.2
    temperature = max(0.0, min(2.0, temperature))
    try:
        max_tokens = int(raw.get("maxTokens", 4096))
    except (TypeError, ValueError):
        max_tokens = 4096
    max_tokens = max(128, min(128000, max_tokens))
    effort = str(raw.get("thinkingEffort") or "medium").strip().lower()
    if effort not in ("minimal", "low", "medium", "high"):
        effort = "medium"
    return {
        "id": raw_id,
        "modelId": model_id,
        "displayName": display_name,
        "platformId": platform_id,
        "apiProtocol": protocol,
        "enabled": raw.get("enabled", True) is not False,
        "hasVision": bool(raw.get("hasVision", False)),
        "enableThinking": bool(raw.get("enableThinking", False)),
        "thinkingEffort": effort,
        "temperature": temperature,
        "maxTokens": max_tokens,
    }


def _normalize_platform(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("id") or "").strip()
    if not pid:
        return None
    name = str(raw.get("name") or raw.get("displayName") or pid).strip() or pid
    display_name = str(raw.get("displayName") or name).strip() or name
    base_url = str(raw.get("baseUrl") or "").strip().rstrip("/")
    protocol = str(raw.get("apiProtocol") or "openai_chat").strip()
    if protocol not in VALID_PROTOCOLS:
        protocol = "openai_chat"
    headers_raw = raw.get("customHeaders")
    headers: Dict[str, str] = {}
    if isinstance(headers_raw, dict):
        for k, v in headers_raw.items():
            ks = str(k).strip()
            if ks and isinstance(v, (str, int, float)):
                headers[ks] = str(v)
    models_out: List[Dict[str, Any]] = []
    seen_models = set()
    for item in (raw.get("models") if isinstance(raw.get("models"), list) else []):
        norm = _normalize_model(item, pid)
        if norm and norm["id"] not in seen_models:
            seen_models.add(norm["id"])
            models_out.append(norm)
    return {
        "id": pid,
        "name": name,
        "displayName": display_name,
        "baseUrl": base_url,
        "apiProtocol": protocol,
        "apiKey": str(raw.get("apiKey") or "").strip(),
        "enabled": raw.get("enabled", True) is not False,
        "customHeaders": headers,
        "isPreset": bool(raw.get("isPreset", False)),
        "models": models_out,
    }


def normalize_config(raw: Any) -> Dict[str, Any]:
    base = default_config()
    if not isinstance(raw, dict):
        return base

    platforms: List[Dict[str, Any]] = []
    seen_pids = set()
    if isinstance(raw.get("platforms"), list) and len(raw.get("platforms")) > 0:
        for item in raw["platforms"]:
            norm = _normalize_platform(item)
            if norm and norm["id"] not in seen_pids:
                seen_pids.add(norm["id"])
                platforms.append(norm)
    else:
        platforms = base["platforms"]

    enabled_model_ids = {
        m["id"]
        for p in platforms
        if p.get("enabled", True)
        for m in p.get("models", [])
        if m.get("enabled", True)
    }

    def _filter_ids(ids: Any, limit: int) -> List[str]:
        if not isinstance(ids, list):
            return []
        out: List[str] = []
        for item in ids:
            sid = str(item or "").strip()
            if sid and sid in enabled_model_ids and sid not in out:
                out.append(sid)
                if len(out) >= limit:
                    break
        return out

    selected_text = _filter_ids(raw.get("selectedTextModels"), MAX_ANSWER_MODELS)
    selected_summary = _filter_ids(raw.get("selectedSummaryModels"), 1)
    vision_id = str(raw.get("selectedVisionModel") or "").strip() or None
    if vision_id not in enabled_model_ids:
        vision_id = None
    agent_id = str(raw.get("selectedAgentModel") or "").strip() or None
    if agent_id not in enabled_model_ids:
        agent_id = None

    search_raw = raw.get("search") if isinstance(raw.get("search"), dict) else {}
    provider = str(search_raw.get("provider") or "tavily").strip().lower()
    if provider not in VALID_SEARCH_PROVIDERS:
        provider = "tavily"
    search = {
        "enabled": bool(search_raw.get("enabled", False)),
        "provider": provider,
        "tavilyApiKey": str(search_raw.get("tavilyApiKey") or "").strip(),
        "braveApiKey": str(search_raw.get("braveApiKey") or "").strip(),
        "exaApiKey": str(search_raw.get("exaApiKey") or "").strip(),
        "zhipuApiKey": str(search_raw.get("zhipuApiKey") or "").strip(),
        "youApiKey": str(search_raw.get("youApiKey") or "").strip(),
        "deepseekApiKey": str(search_raw.get("deepseekApiKey") or "").strip(),
    }

    engine_raw = raw.get("engine") if isinstance(raw.get("engine"), dict) else {}
    priority = str(engine_raw.get("priority") or "local_ocs_ai").strip()
    if priority not in VALID_PRIORITIES:
        priority = "local_ocs_ai"
    try:
        default_folder_id = max(0, int(engine_raw.get("defaultFolderId", 0)))
    except (TypeError, ValueError):
        default_folder_id = 0
    try:
        model_timeout = max(5, min(300, int(engine_raw.get("modelTimeout", 45))))
    except (TypeError, ValueError):
        model_timeout = 45
    try:
        retry_count = max(0, min(5, int(engine_raw.get("modelRetryCount", 2))))
    except (TypeError, ValueError):
        retry_count = 2
    try:
        server_port = max(1024, min(65535, int(engine_raw.get("localServerPort", 3005))))
    except (TypeError, ValueError):
        server_port = 3005

    engine = {
        "enabled": bool(engine_raw.get("enabled", False)),
        "priority": priority,
        "autoSaveToBank": engine_raw.get("autoSaveToBank", True) is not False,
        "defaultFolderId": default_folder_id,
        "enableSameQuestionCheck": engine_raw.get("enableSameQuestionCheck", True) is not False,
        "enableVisionForImages": engine_raw.get("enableVisionForImages", True) is not False,
        "modelTimeout": model_timeout,
        "modelRetryCount": retry_count,
        "localServerEnabled": bool(engine_raw.get("localServerEnabled", False)),
        "localServerPort": server_port,
        "localServerToken": str(engine_raw.get("localServerToken") or "").strip(),
    }

    return {
        "platforms": platforms,
        "selectedTextModels": selected_text,
        "selectedSummaryModels": selected_summary,
        "selectedVisionModel": vision_id,
        "selectedAgentModel": agent_id,
        "search": search,
        "engine": engine,
    }


def _register_secrets(cfg: Dict[str, Any]) -> None:
    register_config(cfg)
    for platform in cfg.get("platforms", []):
        key = platform.get("apiKey")
        if key:
            register_secret(key)
    search = cfg.get("search", {})
    for meta in SEARCH_PROVIDERS_META:
        key = search.get(meta["keyField"])
        if key:
            register_secret(key)
    token = cfg.get("engine", {}).get("localServerToken")
    if token:
        register_secret(token)


class AIConfigStore:
    def __init__(self, config_path: Optional[str] = None) -> None:
        self.config_path = config_path or os.path.join(data_dir(), "ai_config.json")
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, Any]] = None

    def load(self) -> Dict[str, Any]:
        with self._lock:
            if self._cache is not None:
                return copy.deepcopy(self._cache)
            raw = None
            try:
                if os.path.isfile(self.config_path):
                    with open(self.config_path, "r", encoding="utf-8") as fp:
                        raw = json.load(fp)
            except Exception as exc:
                logger.warning("读取 AI 配置失败，使用默认配置: {}", exc)
                raw = None
            cfg = normalize_config(raw)
            _register_secrets(cfg)
            self._cache = cfg
            return copy.deepcopy(cfg)

    def save(self, data: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            normalized = normalize_config(data)
            parent = os.path.dirname(self.config_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp_path = self.config_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as fp:
                json.dump(normalized, fp, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.config_path)
            _register_secrets(normalized)
            self._cache = normalized
            return copy.deepcopy(normalized)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            current = self.load()
            for key, value in patch.items():
                if key in ("search", "engine") and isinstance(value, dict) and isinstance(current.get(key), dict):
                    merged = dict(current[key])
                    merged.update(value)
                    current[key] = merged
                else:
                    current[key] = value
            return self.save(current)

    def find_model(self, model_id: Optional[str], cfg: Optional[Dict[str, Any]] = None) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        if not model_id:
            return None
        source = cfg if cfg is not None else self.load()
        for platform in source.get("platforms", []):
            if not platform.get("enabled", True):
                continue
            for model in platform.get("models", []):
                if model.get("id") == model_id and model.get("enabled", True):
                    return platform, model
        return None

    def resolve_text_models(self, cfg: Optional[Dict[str, Any]] = None) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        source = cfg if cfg is not None else self.load()
        resolved: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for mid in source.get("selectedTextModels", []):
            pair = self.find_model(mid, source)
            if pair:
                resolved.append(pair)
        return resolved

    def resolve_summary_models(self, cfg: Optional[Dict[str, Any]] = None) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        source = cfg if cfg is not None else self.load()
        resolved: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for mid in source.get("selectedSummaryModels", []):
            pair = self.find_model(mid, source)
            if pair:
                resolved.append(pair)
        return resolved

    def resolve_vision_model(self, cfg: Optional[Dict[str, Any]] = None) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        source = cfg if cfg is not None else self.load()
        explicit = self.find_model(source.get("selectedVisionModel"), source)
        if explicit:
            return explicit
        for pair in self.resolve_text_models(source):
            if pair[1].get("hasVision"):
                return pair
        for platform in source.get("platforms", []):
            if not platform.get("enabled", True) or not platform.get("apiKey"):
                continue
            for model in platform.get("models", []):
                if model.get("enabled", True) and model.get("hasVision"):
                    return platform, model
        return None

    def resolve_agent_model(self, cfg: Optional[Dict[str, Any]] = None) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        source = cfg if cfg is not None else self.load()
        explicit = self.find_model(source.get("selectedAgentModel"), source)
        if explicit:
            return explicit
        text_models = self.resolve_text_models(source)
        if text_models:
            return text_models[0]
        summary_models = self.resolve_summary_models(source)
        if summary_models:
            return summary_models[0]
        return None

    def summary_stats(self) -> Dict[str, Any]:
        cfg = self.load()
        text_models = self.resolve_text_models(cfg)
        summary_models = self.resolve_summary_models(cfg)
        vision_model = self.resolve_vision_model(cfg)
        agent_model = self.resolve_agent_model(cfg)
        configured_platforms = sum(1 for p in cfg["platforms"] if p.get("enabled", True) and p.get("apiKey"))
        return {
            "enabled": cfg["engine"]["enabled"],
            "priority": cfg["engine"]["priority"],
            "configuredPlatforms": configured_platforms,
            "selectedTextModelCount": len(text_models),
            "selectedTextModelNames": [m["displayName"] for _, m in text_models],
            "selectedSummaryModelNames": [m["displayName"] for _, m in summary_models],
            "visionModelName": vision_model[1]["displayName"] if vision_model else None,
            "agentModelName": agent_model[1]["displayName"] if agent_model else None,
            "searchEnabled": cfg["search"]["enabled"],
            "searchProvider": cfg["search"]["provider"],
            "localServerEnabled": cfg["engine"]["localServerEnabled"],
            "localServerPort": cfg["engine"]["localServerPort"],
        }
