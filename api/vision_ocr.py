# -*- coding: utf-8 -*-
"""
外部 AI 视觉模型 OCR 模块

支持多种 AI 视觉模型进行图片文字/公式识别：
- OpenAI GPT-4o / GPT-4-vision
- Claude 3 Vision (Anthropic)
- 通义千问 VL (Qwen-VL)
- 硅基流动 (SiliconFlow) 视觉模型
- 其他 OpenAI 兼容 API

CLI 默认配置从 config.ini 的 [ocr]（或 [vision_ocr]）读取，环境变量优先。
Web 通过 ocr_context(config) 绑定任务快照，{} 明确禁用远端 OCR，
None 使用 CLI 默认配置；不修改进程环境。配置环境变量：
- CHAOXING_VISION_OCR_PROVIDER: 提供商类型 (openai, claude, qwen, siliconflow, openai_compatible)
- CHAOXING_VISION_OCR_ENDPOINT: API 端点 (可选，各提供商有默认值)
- CHAOXING_VISION_OCR_KEY: API 密钥
- CHAOXING_VISION_OCR_MODEL: 模型名称 (可选，各提供商有默认值)
- CHAOXING_VISION_OCR_PROMPT: 自定义 OCR 提示词 (可选)
"""

import base64
import configparser
import hashlib
import json
import os
from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional, Dict, Any

import requests

from api.logger import logger

# ============== 配置常量 ==============

# 默认 OCR 提示词 - 严格模式，专为题目识别优化
DEFAULT_OCR_PROMPT = """你是一个专业的 OCR 文字识别引擎。请严格按照以下规则识别图片中的内容：

【核心规则】
1. 只输出图片中的原始文字内容，禁止添加任何解释、描述、评论或额外文字
2. 禁止输出"图片中包含..."、"这是..."、"识别结果..."等引导语
3. 禁止对内容进行翻译、改写或补充

【数学公式处理】
- 数学公式必须用 LaTeX 格式输出
- 行内公式用单个 $ 包裹，如：$x^2 + y^2 = r^2$
- 独立公式用 $$ 包裹，如：$$\\int_a^b f(x)dx$$
- 分数用 \\frac{分子}{分母}
- 根号用 \\sqrt{} 或 \\sqrt[n]{}
- 希腊字母用对应命令：α→\\alpha, β→\\beta, π→\\pi 等
- 求和用 \\sum，积分用 \\int，极限用 \\lim

【输出格式】
- 保持原文的阅读顺序（从上到下、从左到右）
- 多行内容用换行符分隔
- 如果图片为空白或无法识别任何文字，仅输出：[空]

现在请识别图片内容："""

# 提供商默认配置
PROVIDER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "openai": {
        "endpoint": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o",
    },
    "claude": {
        "endpoint": "https://api.anthropic.com/v1/messages",
        "model": "claude-3-5-sonnet-20241022",
    },
    "qwen": {
        "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-vl-plus",
    },
    "siliconflow": {
        "endpoint": "https://api.siliconflow.cn/v1/chat/completions",
        "model": "Qwen/Qwen2-VL-72B-Instruct",
    },
    "openai_compatible": {
        "endpoint": "",  # 必须用户指定
        "model": "gpt-4o",
    },
}

# ============== 任务配置与识别结果 ==============

OCR_CONFIG_PATH = "config.ini"
OCR_PIPELINE_VERSION = 2  # 调整提示词、预处理或本地引擎参数时升级缓存版本。
_OCR_CONTEXT: ContextVar[Optional[Dict[str, Any]]] = ContextVar("chaoxing_ocr_config", default=None)


@dataclass(frozen=True)
class OCRResult:
    """success 区分识别成功但无文字，与网络/引擎暂时失败。"""

    text: str = ""
    success: bool = False


def _as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _read_cli_ocr_config() -> Dict[str, Any]:
    source: Dict[str, Any] = {}
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(OCR_CONFIG_PATH, encoding="utf8")
        for section in ("ocr", "vision_ocr"):
            if parser.has_section(section):
                source.update(parser[section])
                break
    except (OSError, configparser.Error, UnicodeError):
        logger.warning("OCR 默认配置文件无法读取，将使用环境配置")

    source["key"] = source.pop("api_key", source.get("key", ""))
    environment_keys = {
        "provider": "CHAOXING_VISION_OCR_PROVIDER",
        "key": "CHAOXING_VISION_OCR_KEY",
        "endpoint": "CHAOXING_VISION_OCR_ENDPOINT",
        "model": "CHAOXING_VISION_OCR_MODEL",
        "prompt": "CHAOXING_VISION_OCR_PROMPT",
        "enable_local": "CHAOXING_ENABLE_OCR",
        "http_endpoint": "CHAOXING_OCR_ENDPOINT",
    }
    for field, variable in environment_keys.items():
        if variable in os.environ:
            source[field] = os.environ[variable]
    return source


def _normalize_ocr_config(source: Mapping) -> Dict[str, Any]:
    def text(field: str) -> str:
        return str(source.get(field) or "").strip()

    provider = text("provider").lower()
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai_compatible"])
    return {
        "provider": provider,
        "api_key": text("key") or text("api_key"),
        "endpoint": text("endpoint") or defaults["endpoint"],
        "model": text("model") or defaults["model"],
        "prompt": text("prompt") or DEFAULT_OCR_PROMPT,
        "enable_local": _as_bool(source.get("enable_local", os.environ.get("CHAOXING_ENABLE_OCR", "0"))),
        "http_endpoint": text("http_endpoint"),
    }


@contextmanager
def ocr_context(config: Optional[Mapping]):
    """绑定独立配置快照；新线程需使用 copy_context().run 传播。

    None 读取 CLI 环境/文件；{} 禁用远端视觉及 HTTP fallback。
    enable_local 可覆盖本机 CHAOXING_ENABLE_OCR，http_endpoint 可设置 HTTP fallback。
    调用方修改原字典不会影响正在运行的任务。
    """
    if config is not None and not isinstance(config, Mapping):
        raise TypeError("OCR config must be a mapping or None")
    normalized = _normalize_ocr_config(_read_cli_ocr_config() if config is None else config)
    token = _OCR_CONTEXT.set(normalized)
    try:
        yield
    finally:
        _OCR_CONTEXT.reset(token)


def get_ocr_config() -> Dict[str, Any]:
    """返回当前任务配置副本，禁止修改其他任务的配置。"""
    config = _OCR_CONTEXT.get()
    return dict(config) if config is not None else _normalize_ocr_config(_read_cli_ocr_config())


def ocr_config_fingerprint(config: Dict[str, Any]) -> str:
    """缓存身份覆盖模型、端点、凭据、提示词、本地及 HTTP 配置，不暴露密钥。"""
    encoded = json.dumps([OCR_PIPELINE_VERSION, config], sort_keys=True, ensure_ascii=False).encode("utf8")
    return hashlib.sha256(encoded).hexdigest()


def _load_vision_ocr_config(config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, str]]:
    current = get_ocr_config() if config is None else config
    if not current.get("provider") or not current.get("api_key") or not current.get("endpoint"):
        return None
    return {key: current[key] for key in ("provider", "endpoint", "api_key", "model", "prompt")}


def _recognized_text(content: Any) -> OCRResult:
    if not isinstance(content, str):
        return OCRResult()
    text = content.strip()
    if text in ("[空]", "无文字内容", "[空白]"):
        text = ""
    return OCRResult(text, success=True)


def _image_to_base64(image_bytes: bytes) -> str:
    """将图片字节转换为 base64 字符串"""
    return base64.b64encode(image_bytes).decode("utf-8")


def _detect_image_type(image_bytes: bytes) -> str:
    """检测图片类型"""
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    elif image_bytes[:2] == b'\xff\xd8':
        return "image/jpeg"
    elif image_bytes[:6] in (b'GIF87a', b'GIF89a'):
        return "image/gif"
    elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp"
    else:
        return "image/png"  # 默认


def _call_openai_compatible(config: Dict[str, str], image_bytes: bytes) -> OCRResult:
    """调用 OpenAI 兼容 API（包括 OpenAI、硅基流动、通义千问等）"""
    image_base64 = _image_to_base64(image_bytes)
    image_type = _detect_image_type(image_bytes)

    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": config["prompt"]},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_type};base64,{image_base64}"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.1,
    }

    resp = None
    try:
        resp = requests.post(
            config["endpoint"],
            headers=headers,
            json=payload,
            timeout=30
        )
        if resp.status_code != 200:
            logger.debug(f"OpenAI 兼容 API 返回异常: {resp.status_code} - {resp.text[:200]}")
            return OCRResult()

        data = resp.json()
        # 解析响应
        choices = data.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            return _recognized_text(message.get("content"))
        return OCRResult()
    except Exception as exc:
        logger.debug(f"OpenAI 兼容 API 调用失败: {exc}")
        return OCRResult()
    finally:
        if resp is not None:
            resp.close()


def _call_claude(config: Dict[str, str], image_bytes: bytes) -> OCRResult:
    """调用 Claude API (Anthropic)"""
    image_base64 = _image_to_base64(image_bytes)
    image_type = _detect_image_type(image_bytes)

    headers = {
        "x-api-key": config["api_key"],
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": config["model"],
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": image_type,
                            "data": image_base64,
                        }
                    },
                    {"type": "text", "text": config["prompt"]}
                ]
            }
        ]
    }

    resp = None
    try:
        resp = requests.post(
            config["endpoint"],
            headers=headers,
            json=payload,
            timeout=30
        )
        if resp.status_code != 200:
            logger.debug(f"Claude API 返回异常: {resp.status_code} - {resp.text[:200]}")
            return OCRResult()

        data = resp.json()
        # 解析 Claude 响应格式
        content_blocks = data.get("content", [])
        for block in content_blocks:
            if block.get("type") == "text":
                return _recognized_text(block.get("text"))
        return OCRResult()
    except Exception as exc:
        logger.debug(f"Claude API 调用失败: {exc}")
        return OCRResult()
    finally:
        if resp is not None:
            resp.close()


def vision_ocr_result(image_bytes: bytes, config: Optional[Dict[str, Any]] = None) -> OCRResult:
    current = _load_vision_ocr_config(config)
    if not current:
        return OCRResult()
    if current["provider"] == "claude":
        return _call_claude(current, image_bytes)
    return _call_openai_compatible(current, image_bytes)


def vision_ocr(image_bytes: bytes) -> str:
    """使用外部 AI 视觉模型进行 OCR 识别

    Args:
        image_bytes: 图片的二进制数据

    Returns:
        识别出的文字内容，失败时返回空字符串
    """
    return vision_ocr_result(image_bytes).text


def is_vision_ocr_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """检查外部 AI 视觉 OCR 是否已启用"""
    return _load_vision_ocr_config(config) is not None


def reset_vision_ocr_config():
    """兼容旧调用方。CLI 默认值按需读取，显式任务快照无需全局重置。"""
