# -*- coding: utf-8 -*-
"""
超星学习通数据解析模块

该模块负责解析超星学习通平台的课程、章节、任务点等各种数据，
并转换为程序内部使用的结构化数据格式。
"""
import hashlib
import json
import re
import os
import sys
import tempfile
import threading
import time
import io
from collections import OrderedDict
from collections.abc import Mapping
from typing import List, Dict, Tuple, Any, Optional, Union

from bs4 import BeautifulSoup, NavigableString

from api.font_decoder import FontDecoder
from api.logger import logger
from api.config import GlobalConst as gc
from api.session import get_current_session
from api.vision_ocr import (
    OCRResult,
    get_ocr_config,
    is_vision_ocr_enabled,
    ocr_config_fingerprint,
    vision_ocr_result,
)
import requests

try:
    from PIL import Image, ImageEnhance, ImageFilter
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

ENABLE_LOCAL_OCR = os.environ.get("CHAOXING_ENABLE_OCR", "0").strip().lower() in {"1", "true", "yes", "y", "on"}
_PADDLE_OCR_ENGINE = None
_PADDLE_OCR_INITIALIZED = False
_PADDLE_OCR_DEVICE = None  # 记录当前 OCR 引擎运行的设备（gpu / cpu）
_PADDLE_OCR_LOCK = threading.RLock()
_PADDLE_OCR_RETRY_AT = 0.0
_PADDLE_OCR_RETRY_SECONDS = 60.0

# URL 缓存按借用会话隔离，内容缓存按图片哈希和完整引擎配置复用。
# 有效文本缓存一小时，确认空白缓存一分钟，临时失败只缓存五秒。
_OCR_CACHE_MAXSIZE = 256
_OCR_TEXT_TTL = 3600.0
_OCR_EMPTY_TTL = 60.0
_OCR_FAILURE_TTL = 5.0
_OCR_URL_CACHE = OrderedDict()
_OCR_CONTENT_CACHE = OrderedDict()
_OCR_CACHE_LOCK = threading.Lock()
# 有界分片锁抑制相同 URL/内容的并发重复请求；不在全局缓存锁内联网。
_OCR_URL_LOCKS = [threading.Lock() for _ in range(32)]
_OCR_CONTENT_LOCKS = [threading.Lock() for _ in range(32)]


def _get_ocr_cache(cache, key) -> Optional[OCRResult]:
    with _OCR_CACHE_LOCK:
        entry = cache.get(key)
        if entry is not None:
            expires_at, result = entry
            if expires_at > time.monotonic():
                cache.move_to_end(key)
                return result
            del cache[key]
    return None


def _put_ocr_cache(cache, key, result: OCRResult) -> None:
    ttl = _OCR_TEXT_TTL if result.text and result.success else (
        _OCR_EMPTY_TTL if result.success else _OCR_FAILURE_TTL
    )
    with _OCR_CACHE_LOCK:
        cache[key] = (time.monotonic() + ttl, result)
        cache.move_to_end(key)
        while len(cache) > _OCR_CACHE_MAXSIZE:
            cache.popitem(last=False)


def _import_paddle_ocr_class():
    """导入 PaddleOCR，优先使用环境中安装的版本。

    便携版会携带一个 PaddleOCR 源码副本作为兜底，但不应覆盖用户安装的
    最新版本，否则 PaddleOCR 与 PaddleX 的版本可能不匹配。
    """
    try:
        from paddleocr import PaddleOCR  # type: ignore

        return PaddleOCR
    except (ImportError, ModuleNotFoundError) as installed_exc:
        project_root = os.path.dirname(os.path.dirname(__file__))
        paddle_root = os.path.join(project_root, "PaddleOCR")
        if not os.path.isdir(paddle_root):
            raise installed_exc
        if paddle_root not in sys.path:
            sys.path.insert(0, paddle_root)
        try:
            from paddleocr import PaddleOCR  # type: ignore

            return PaddleOCR
        except (ImportError, ModuleNotFoundError):
            raise installed_exc


def _parse_paddle_ocr_result(ocr_result: Any) -> List[str]:
    """从 PaddleOCR 2.x/3.x 返回值中提取识别文本。

    PaddleOCR 3.x 返回 ``OCRResult``（可按字典访问 ``rec_texts``），而 2.x
    返回嵌套列表。结果对象在不同 PaddleX 版本中可能只实现 ``__getitem__``
    或属性访问，因此这里按这三种方式依次读取。
    """
    if ocr_result is None:
        return []

    def _get_field(value: Any, name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)
        try:
            return value[name]
        except (KeyError, IndexError, TypeError, AttributeError):
            return getattr(value, name, None)

    def _append_texts(value: Any, output: List[str]) -> None:
        if isinstance(value, str):
            if value.strip():
                output.append(value.strip())
            return
        if value is None:
            return
        try:
            iterator = iter(value)
        except TypeError:
            return
        for item in iterator:
            if isinstance(item, str) and item.strip():
                output.append(item.strip())

    parsed_texts: List[str] = []
    pages: List[Any]
    if isinstance(ocr_result, Mapping) or not isinstance(ocr_result, (list, tuple)):
        pages = [ocr_result]
    else:
        pages = list(ocr_result)

    # 3.x: one result object per input image, with a rec_texts field.
    for page in pages:
        rec_texts = _get_field(page, "rec_texts")
        if rec_texts is not None:
            _append_texts(rec_texts, parsed_texts)

    if parsed_texts:
        return parsed_texts

    # 2.x: [[box, (text, score)], ...], optionally wrapped by a page list.
    def _walk_legacy(value: Any) -> None:
        if isinstance(value, (str, bytes)):
            return
        try:
            items = list(value)
        except (TypeError, ValueError):
            return
        if len(items) >= 2 and isinstance(items[1], (list, tuple)):
            text = items[1][0] if items[1] else None
            if isinstance(text, str) and text.strip():
                parsed_texts.append(text.strip())
                return
        for item in items:
            if isinstance(item, (list, tuple)):
                _walk_legacy(item)

    _walk_legacy(ocr_result)
    return parsed_texts


def _init_paddle_ocr(preferred_device: Optional[str] = None):
    """延迟初始化 PaddleOCR 引擎。

    - 优先使用环境中安装的 PaddleOCR 3.x（与 PaddleX 版本保持一致）；
    - 未安装时回退到项目根目录下的源码副本；
    - 初始化失败后 60 秒内不重复导入或构建引擎，不影响主流程。
    """
    global _PADDLE_OCR_ENGINE, _PADDLE_OCR_INITIALIZED, _PADDLE_OCR_DEVICE
    global _PADDLE_OCR_RETRY_AT

    with _PADDLE_OCR_LOCK:
        if time.monotonic() < _PADDLE_OCR_RETRY_AT:
            return None
        if preferred_device and preferred_device != _PADDLE_OCR_DEVICE:
            # 强制切换设备时需要重新初始化
            _PADDLE_OCR_INITIALIZED = False
            _PADDLE_OCR_ENGINE = None

        if _PADDLE_OCR_INITIALIZED and _PADDLE_OCR_ENGINE is not None:
            return _PADDLE_OCR_ENGINE

        _PADDLE_OCR_INITIALIZED = True
        try:
            PaddleOCR = _import_paddle_ocr_class()

            devices_to_try: List[str] = []
            if preferred_device:
                devices_to_try.append(preferred_device)
                if preferred_device != "cpu":
                    devices_to_try.append("cpu")
            else:
                devices_to_try = ["gpu", "cpu"]

            last_exc: Optional[Exception] = None
            for device in devices_to_try:
                try:
                    # 使用 PaddleOCR 3.x 参数，关闭不需要的文档预处理以降低开销。
                    modern_kwargs = {
                        "lang": "ch",
                        "device": device,
                        "text_det_thresh": 0.2,
                        "text_det_box_thresh": 0.4,
                        "text_det_unclip_ratio": 1.8,
                        "use_textline_orientation": True,
                        "use_doc_orientation_classify": False,
                        "use_doc_unwarping": False,
                    }
                    try:
                        engine = PaddleOCR(**modern_kwargs)
                    except TypeError as modern_exc:
                        # 仅兼容仍在使用 PaddleOCR 2.x 的环境；3.x 不会走此分支。
                        try:
                            engine = PaddleOCR(
                                lang="ch",
                                use_gpu=device.lower().startswith("gpu"),
                                det_db_thresh=0.2,
                                det_db_box_thresh=0.4,
                                det_db_unclip_ratio=1.8,
                                use_angle_cls=True,
                            )
                        except TypeError:
                            raise modern_exc
                    _PADDLE_OCR_ENGINE = engine
                    _PADDLE_OCR_DEVICE = device
                    _PADDLE_OCR_RETRY_AT = 0.0
                    logger.info(f"PaddleOCR 初始化成功 ({device.upper()})，将用于题目图片 OCR")
                    return _PADDLE_OCR_ENGINE
                except Exception as exc_device:
                    last_exc = exc_device
                    logger.warning(f"PaddleOCR {device.upper()} 初始化失败: {exc_device}")

            if last_exc:
                raise last_exc
        except Exception as exc:
            cause = getattr(exc, "__cause__", None)
            if cause is not None:
                logger.warning(
                    f"PaddleOCR 初始化失败，将不使用本地 OCR: {exc} (底层依赖错误: {cause})"
                )
            else:
                logger.warning(f"PaddleOCR 初始化失败，将不使用本地 OCR: {exc}")
            _PADDLE_OCR_ENGINE = None
            _PADDLE_OCR_DEVICE = None
            _PADDLE_OCR_RETRY_AT = time.monotonic() + _PADDLE_OCR_RETRY_SECONDS

        return _PADDLE_OCR_ENGINE


def _preprocess_image_for_ocr(image_bytes: bytes, enhance_mode: int = 0) -> bytes:
    """预处理图片以提高 OCR 识别率。
    
    enhance_mode:
        0 - 标准预处理：调整大小、增强对比度
        1 - 高对比度模式：更强的对比度增强 + 锐化
        2 - 二值化模式：转灰度后进行阈值处理
    
    返回处理后的 PNG 图片字节数据。
    """
    if not PIL_AVAILABLE:
        return image_bytes
    
    try:
        img = Image.open(io.BytesIO(image_bytes))
        
        # 转换为 RGB（处理 RGBA 或其他模式）
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # 如果图片太小，放大以提高识别率
        min_dimension = 100
        width, height = img.size
        if width < min_dimension or height < min_dimension:
            scale = max(min_dimension / width, min_dimension / height, 2.0)
            new_size = (int(width * scale), int(height * scale))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        # 如果图片太大，缩小以加快处理速度
        max_dimension = 2000
        width, height = img.size
        if width > max_dimension or height > max_dimension:
            scale = min(max_dimension / width, max_dimension / height)
            new_size = (int(width * scale), int(height * scale))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
        
        if enhance_mode == 0:
            # 标准模式：轻微增强对比度和锐度
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.3)
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.2)
        elif enhance_mode == 1:
            # 高对比度模式
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.8)
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(1.5)
            # 轻微去噪
            img = img.filter(ImageFilter.MedianFilter(size=3))
        elif enhance_mode == 2:
            # 二值化模式：转灰度，增强对比度，然后用阈值处理
            img = img.convert('L')
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(2.0)
            # 简单阈值二值化
            threshold = 180
            img = img.point(lambda p: 255 if p > threshold else 0)
            img = img.convert('RGB')
        
        # 输出为 PNG
        output = io.BytesIO()
        img.save(output, format='PNG')
        return output.getvalue()
    except Exception as exc:
        logger.debug(f"图片预处理失败: {exc}")
        return image_bytes


def _call_http_ocr(ocr_endpoint: str, image_bytes: bytes, img_url: str) -> OCRResult:
    """调用 HTTP OCR 服务"""
    ocr_resp = None
    try:
        files = {"file": ("question.png", image_bytes, "image/png")}
        ocr_resp = requests.post(ocr_endpoint, files=files, timeout=20)
        if ocr_resp.status_code != 200:
            logger.debug(f"HTTP OCR 服务返回异常状态码: {ocr_resp.status_code}")
            return OCRResult()
        data = ocr_resp.json()
        # 显式空字段表示成功但未识别到文字，缺失字段属于无效响应。
        for key in ("latex", "text", "result", "data"):
            value = data.get(key)
            if isinstance(value, str):
                if value.strip():
                    logger.debug(f"HTTP OCR 识别成功: {value[:100]}... 来自 {img_url}")
                    return OCRResult(value.strip(), success=True)
        return OCRResult(success=any(isinstance(data.get(key), str) for key in ("latex", "text", "result", "data")))
    except Exception as exc:
        logger.debug(f"调用 HTTP OCR 服务失败: {exc}")
        return OCRResult()
    finally:
        if ocr_resp is not None:
            ocr_resp.close()


def _download_ocr_image(img_url: str, session) -> Optional[bytes]:
    """借用账号线程会话；独立调用只用无账号 cookies 的临时会话。"""
    owned_session = session is None
    resp = None
    try:
        if owned_session:
            session = requests.Session()
            session.headers.update(gc.HEADERS)

        # 对超星图片域名补充一个简单 Referer，进一步降低 403 概率
        extra_headers = {}
        if "p.ananas.chaoxing.com" in img_url:
            extra_headers["Referer"] = "https://mooc1.chaoxing.com/"

        resp = session.get(img_url, headers=extra_headers or None, timeout=8)
        if resp.status_code != 200:
            logger.debug(f"下载题目图片失败: {img_url} -> {resp.status_code}")
            return None
        return resp.content or None
    except Exception as exc:
        logger.debug(f"下载题目图片异常: {exc}")
        return None
    finally:
        try:
            if resp is not None:
                resp.close()
        finally:
            if owned_session and session is not None:
                session.close()


def _local_ocr_result(image_bytes: bytes, img_url: str) -> OCRResult:
    engine = _init_paddle_ocr()
    if engine is not None:
        tmp_path = None
        had_error = False
        try:
            # 尝试多种预处理模式，直到获得有效文本
            # 模式 0: 标准预处理（对比度+锐化）
            # 模式 1: 高对比度模式
            # 模式 2: 二值化模式
            preprocessing_modes = [0, 1, 2]
            
            final_texts: List[str] = []
            for preprocess_mode in preprocessing_modes:
                # 预处理图片
                processed_bytes = _preprocess_image_for_ocr(image_bytes, enhance_mode=preprocess_mode)
                
                # 写入临时文件
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                fd, tmp_path = tempfile.mkstemp(suffix=".png")
                with os.fdopen(fd, "wb") as f:
                    f.write(processed_bytes)
                
                # 执行 OCR
                for device_attempt in range(2):
                    try:
                        with _PADDLE_OCR_LOCK:
                            predict = getattr(engine, "predict", None)
                            if callable(predict):
                                ocr_result = predict(tmp_path)
                            else:
                                # PaddleOCR 2.x fallback; 3.x exposes predict().
                                ocr_result = engine.ocr(tmp_path)
                            final_texts = _parse_paddle_ocr_result(ocr_result)
                        break
                    except Exception as exc:
                        global _PADDLE_OCR_DEVICE
                        if (
                            device_attempt == 0
                            and isinstance(_PADDLE_OCR_DEVICE, str)
                            and _PADDLE_OCR_DEVICE.lower().startswith("gpu")
                        ):
                            logger.debug(f"PaddleOCR GPU 推理失败，切换到 CPU: {exc}")
                            engine = _init_paddle_ocr(preferred_device="cpu")
                            if engine is None:
                                had_error = True
                                break
                            continue
                        logger.debug(f"PaddleOCR 识别失败 (模式{preprocess_mode}): {exc}")
                        had_error = True
                        break
                
                if final_texts:
                    logger.debug(
                        f"PaddleOCR 提取文本成功 (预处理模式{preprocess_mode}): {' '.join(final_texts)} 来自 {img_url}"
                    )
                    break
                elif engine is None:
                    break
                else:
                    logger.debug(f"PaddleOCR 预处理模式{preprocess_mode}未识别出文本，尝试下一模式")
            
            if not final_texts:
                logger.debug(f"PaddleOCR 所有预处理模式均未识别出文本 来自 {img_url}")
            
            if final_texts:
                # 将多行结果合并为一行，交给大模型进一步理解
                return OCRResult(" ".join(final_texts), success=True)
            return OCRResult(success=not had_error)
        except Exception as exc:
            logger.debug(f"PaddleOCR 识别失败: {exc}")
            return OCRResult()
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    return OCRResult()


def _recognize_ocr_image(image_bytes: bytes, img_url: str, config: dict) -> OCRResult:
    # 保留原有优先级：外部视觉 > 本地（未配置外部时）> HTTP fallback。
    has_primary = is_vision_ocr_enabled(config) or config["enable_local"]
    result = OCRResult()
    if is_vision_ocr_enabled(config):
        result = vision_ocr_result(image_bytes, config)
    elif config["enable_local"]:
        result = _local_ocr_result(image_bytes, img_url)
    if result.text:
        return result

    if config["http_endpoint"]:
        fallback = _call_http_ocr(config["http_endpoint"], image_bytes, img_url)
        if fallback.text or not has_primary:
            return fallback
        # 任一引擎暂时失败时使用短重试窗口，不能让空白兜底掩盖故障。
        return OCRResult(success=result.success and fallback.success)
    return result


def _ocr_image_to_text(img_url: str) -> str:
    """按任务配置识别题目图片，缓存有界且临时网络失败可短期重试。"""
    if not img_url:
        return ""
    config = get_ocr_config()
    if not (is_vision_ocr_enabled(config) or config["enable_local"] or config["http_endpoint"]):
        return ""
    fingerprint = ocr_config_fingerprint(config)
    session = get_current_session()
    # 保留会话对象本身而不是 id，避免会话释放后 id 复用命中其他账号。
    url_key = (session, img_url, fingerprint)
    with _OCR_URL_LOCKS[hash(url_key) % len(_OCR_URL_LOCKS)]:
        cached = _get_ocr_cache(_OCR_URL_CACHE, url_key)
        if cached is not None:
            return cached.text

        result = OCRResult()
        try:
            image_bytes = _download_ocr_image(img_url, session)
            if image_bytes is not None:
                content_key = (hashlib.sha256(image_bytes).hexdigest(), fingerprint)
                with _OCR_CONTENT_LOCKS[hash(content_key) % len(_OCR_CONTENT_LOCKS)]:
                    result = _get_ocr_cache(_OCR_CONTENT_CACHE, content_key)
                    if result is None:
                        result = _recognize_ocr_image(image_bytes, img_url, config)
                        _put_ocr_cache(_OCR_CONTENT_CACHE, content_key, result)
        except Exception as exc:
            logger.debug(f"题目图片 OCR 失败: {exc}")
            result = OCRResult()
        _put_ocr_cache(_OCR_URL_CACHE, url_key, result)
        return result.text


def _normalize_bool(value: Union[str, bool, int, float]) -> bool:
    """统一转换布尔值，避免字符串 'false' 被当成 True 等问题"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "passed"}
    return False


def decode_course_list(html_text: str) -> List[Dict[str, str]]:
    """
    解析课程列表页面，提取课程信息
    
    Args:
        html_text: 课程列表页面的HTML内容
        
    Returns:
        课程信息列表，每个课程包含id、title、teacher等信息
    """
    logger.trace("开始解码课程列表...")
    soup = BeautifulSoup(html_text, "lxml")
    raw_courses = soup.select("div.course")
    course_list = []
    
    for course in raw_courses:
        # 跳过未开放课程
        if course.select_one("a.not-open-tip") or course.select_one("div.not-open-tip"):
            continue
        
        course_detail = {
            "id": course.attrs["id"],
            "info": course.attrs["info"],
            "roleid": course.attrs["roleid"],
            "clazzId": course.select_one("input.clazzId").attrs["value"],
            "courseId": course.select_one("input.courseId").attrs["value"],
            "cpi": re.findall(r"cpi=(.*?)&", course.select_one("a").attrs["href"])[0],
            "title": course.select_one("span.course-name").attrs["title"],
            "desc": course.select_one("p.margint10").attrs["title"] if course.select_one("p.margint10") else "",
            "teacher": course.select_one("p.color3").attrs["title"]
        }
        course_list.append(course_detail)
    
    return course_list


def decode_course_folder(html_text: str) -> List[Dict[str, str]]:
    """
    解析二级课程列表页面，提取文件夹信息
    
    Args:
        html_text: 二级课程列表页面的HTML内容
        
    Returns:
        课程文件夹信息列表
    """
    logger.trace("开始解码二级课程列表...")
    soup = BeautifulSoup(html_text, "lxml")
    raw_courses = soup.select("ul.file-list>li")
    course_folder_list = []
    
    for course in raw_courses:
        if not course.attrs.get("fileid"):
            continue
            
        course_folder_detail = {
            "id": course.attrs["fileid"],
            "rename": course.select_one("input.rename-input").attrs["value"]
        }
        course_folder_list.append(course_folder_detail)
    
    return course_folder_list


def decode_course_point(html_text: str) -> Dict[str, Any]:
    """
    解析章节列表页面，提取章节点信息
    
    Args:
        html_text: 章节列表页面的HTML内容
        
    Returns:
        章节信息字典，包含是否锁定状态和章节点列表
    """
    logger.trace("开始解码章节列表...")
    soup = BeautifulSoup(html_text, "lxml")
    course_point = {
        "hasLocked": False,  # 用于判断该课程任务是否是需要解锁
        "points": [],
    }

    for chapter_unit in soup.find_all("div", class_="chapter_unit"):
        points = _extract_points_from_chapter(chapter_unit)
        # 检查是否有锁定内容
        for point in points:
            if point.get("need_unlock", False):
                course_point["hasLocked"] = True
                
        course_point["points"].extend(points)
    
    return course_point


def _extract_points_from_chapter(chapter_unit) -> List[Dict[str, Any]]:
    """
    从章节单元中提取章节点信息
    
    Args:
        chapter_unit: BeautifulSoup对象，表示一个章节单元
        
    Returns:
        章节点信息列表
    """
    point_list = []
    raw_points = chapter_unit.find_all("li")
    
    for raw_point in raw_points:
        point = raw_point.div
        if "id" not in point.attrs:
            continue
            
        point_id = re.findall(r"^cur(\d{1,20})$", point.attrs["id"])[0]
        point_title = point.select_one("a.clicktitle").text.replace("\n", "").strip()
        
        # 提取任务数量
        job_count = 1  # 默认为1
        need_unlock = False
        if point.select_one("input.knowledgeJobCount"):
            job_count = point.select_one("input.knowledgeJobCount").attrs["value"]
        elif point.select_one("span.bntHoverTips") and "解锁" in point.select_one("span.bntHoverTips").text:
            need_unlock = True
            
        # 判断是否已完成
        is_finished = False
        if point.select_one("span.bntHoverTips") and "已完成" in point.select_one("span.bntHoverTips").text:
            is_finished = True
            
        point_detail = {
            "id": point_id,
            "title": point_title,
            "jobCount": job_count,
            "has_finished": is_finished,
            "need_unlock": need_unlock
        }
        point_list.append(point_detail)
        
    return point_list


def decode_course_card(html_text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    解析任务点列表页面，提取任务点信息
    
    Args:
        html_text: 任务点列表页面的HTML内容
        
    Returns:
        待办任务点列表和任务信息的元组；passed_jobs 保留明确已完成的任务身份
    """
    logger.trace("开始解码任务点列表...")
    job_info = {"passed_jobs": []}
    
    # 检查章节是否未开放
    if "章节未开放" in html_text:
        job_info["notOpen"] = True
        return [], job_info

    # 提取mArg参数
    temp = re.findall(r"mArg=\{(.*?)\};", html_text.replace(" ", ""))
    if not temp:
        return [], job_info

    # 解析JSON数据
    cards_data = json.loads("{" + temp[0] + "}")

    if not cards_data:
        return [], job_info

    # 提取任务信息
    job_info.update(_extract_job_info(cards_data))

    # 处理所有附件任务
    cards = cards_data.get("attachments", [])
    job_info["passed_jobs"] = _extract_passed_jobs(cards)
    job_list = _process_attachment_cards(cards)

    return job_list, job_info


def _extract_passed_jobs(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """保留明确已完成任务的身份，允许完成后缺少 job 和播放参数。"""
    passed_jobs = []
    for card in cards:
        card_type = card.get("type", "")
        property_data = card.get("property", {})
        if not isinstance(card_type, str) or not isinstance(property_data, dict):
            continue
        is_read = card_type == "read"
        if not (_normalize_bool(card.get("isPassed", False)) or (
            is_read and _normalize_bool(property_data.get("read", False))
        )):
            continue

        # 与待办解析保持一致：无 job 的阅读任务优先，其余按直播特征识别。
        if is_read and card.get("job") is None:
            job_type = "read"
        else:
            type_fields = (card_type, property_data.get("type", ""),
                           property_data.get("resourceType", ""))
            is_live = any(isinstance(value, str) and "live" in value.lower()
                          for value in type_fields) or any(
                property_data.get(key) is not None for key in ("liveId", "streamName", "vdoid")
            )
            job_type = "live" if is_live else card_type.lower()
        if job_type not in {"video", "document", "workid", "read", "live"}:
            continue

        # 使用各任务解析器已有的 ID 来源，避免重试时同一任务产生不同的键。
        identity = {"jobid": card.get("jobid", "")}
        if job_type in {"video", "live"}:
            identity["objectid"] = card.get("objectId", "")
        elif job_type == "document":
            identity["objectid"] = property_data.get("objectid", "")
        elif job_type == "read":
            identity["id"] = property_data.get("id", "")
        if job_type == "live" and "jobid" not in card:
            live_id = card.get("id")
            if isinstance(live_id, (str, int)) and not isinstance(live_id, bool):
                identity["jobid"] = str(live_id)
        identity = {
            key: value for key, value in identity.items()
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip()
        }
        identifier = identity.get("jobid") or identity.get("objectid") or identity.get("id")
        if identifier is not None:
            passed_jobs.append({"type": job_type, **identity})
    return passed_jobs


def _extract_job_info(cards_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    从卡片数据中提取任务基本信息
    
    Args:
        cards_data: 卡片数据字典
        
    Returns:
        任务基本信息字典
    """
    defaults = cards_data.get("defaults", {})
    if not defaults:
        return {}
        
    return {
        "ktoken": defaults.get("ktoken", ""),
        "mtEnc": defaults.get("mtEnc", ""),
        "reportTimeInterval": defaults.get("reportTimeInterval", 60),
        "defenc": defaults.get("defenc", ""),
        "cardid": defaults.get("cardid", ""),
        "cpi": defaults.get("cpi", ""),
        "qnenc": defaults.get("qnenc", ""),
        "knowledgeid": defaults.get("knowledgeid", "")
    }


def _process_attachment_cards(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    处理所有附件任务卡片，强化直播任务识别逻辑
    
    Args:
        cards: 附件任务卡片列表
        
    Returns:
        处理后的任务列表
    """
    job_list = []
    
    for index, card in enumerate(cards):
        # 跳过已通过的任务（字符串/数字也能正确识别）
        card_passed = _normalize_bool(card.get("isPassed", False))
        card["isPassed"] = card_passed
        if card_passed:
            continue

        # 处理无job字段的特殊任务
        if card.get("job") is None:
            # 尝试识别阅读任务
            read_job = _process_read_task(card)
            if read_job:
                job_list.append(read_job)
            continue

        # 一开始就把超星api的屎山处理掉，不要用一个屎山行为掩盖另一个屎山 (指根据otherInfo中是否有courseId决定url拼接方式😂)
        # 清理otherInfo字段中的无效参数，这里优化了一下(保留了作者原来的注释TAT）
        if "otherInfo" in card:
            logger.trace("Fixing other info...")
            card["otherInfo"] = card["otherInfo"].split("&")[0]
            logger.trace(f"New info: {card['otherInfo']}")

        # 多维度判断是否为直播任务
        card_type = card.get("type", "").lower()
        property_data = card.get("property", {})
        prop_type = property_data.get("type", "").lower()
        resource_type = property_data.get("resourceType", "").lower()
        
        # 直播任务特征：包含liveId、streamName等字段，
        # 或类型标识包含live（因为live和video有点类似，怕超星又搞出什么幺蛾子就加了一些关键字识别）
        is_live = (
            "live" in card_type 
            or "live" in prop_type
            or "live" in resource_type
            or "livestream" in card_type
            or property_data.get("liveId") is not None
            or property_data.get("streamName") is not None
            or property_data.get("vdoid") is not None
        )

        # 根据任务类型处理
        if is_live:
            live_job = _process_live_task(card)
            if live_job:
                job_list.append(live_job)
        elif card_type == "video":
            video_job = _process_video_task(card)
            if video_job:
                job_list.append(video_job)
        elif card_type == "document":
            doc_job = _process_document_task(card)
            if doc_job:
                job_list.append(doc_job)
        elif card_type == "workid":
            work_job = _process_work_task(card)
            if work_job:
                job_list.append(work_job)
        else:
            logger.warning(f"Unknown card type: {card_type}")
            logger.warning(card)

    return job_list


def _process_live_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理直播类型任务，提取所有必要参数"""
    try:
        property_data = card.get("property", {})
        return {
            "type": "live",
            "jobid": card.get("jobid", str(card.get("id", ""))),  # 兼容不同格式的任务ID
            "name": property_data.get("title", property_data.get("name", "未知直播")),
            "otherinfo": card.get("otherInfo", ""),
            "property": property_data,  # 保留完整属性用于后续处理
            "mid": card.get("mid", ""),
            "objectid": card.get("objectId", ""),
            "aid": card.get("aid", ""),
            # 补充直播特有标识
            "liveId": property_data.get("liveId"),
            "streamName": property_data.get("streamName")
        }
    except Exception as e:
        logger.error(f"解析直播任务失败: {str(e)}, 任务数据: {str(card)[:200]}")
        return None
def _process_read_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理阅读类型任务"""
    read_flag = _normalize_bool(card.get("property", {}).get("read", False))
    if not (card.get("type") == "read" and not read_flag):
        return None
        
    return {
        "title": card.get("property", {}).get("title", ""),
        "type": "read",
        "id": card.get("property", {}).get("id", ""),
        "jobid": card.get("jobid", ""),
        "jtoken": card.get("jtoken", ""),
        "mid": card.get("mid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", "")
    }


def _process_video_task(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """处理视频类型任务"""
    try:
        return {
            "type": "video",
            "jobid": card.get("jobid", ""),
            "name": card.get("property", {}).get("name", ""),
            "otherinfo": card.get("otherInfo", ""),
            "mid": card["mid"],  # 必须字段，如果不存在会抛出异常
            "objectid": card.get("objectId", ""),
            "aid": card.get("aid", ""),
            "playTime": card.get("playTime", 0),
            "rt": card.get("property", {}).get("rt", ""),
            "attDuration": card.get("attDuration", ""),
            "attDurationEnc": card.get("attDurationEnc", ""),
            "videoFaceCaptureEnc": card.get("videoFaceCaptureEnc", ""),
        }
    except KeyError:
        logger.warning("出现转码失败视频，已跳过...")
        return None


def _process_document_task(card: Dict[str, Any]) -> Dict[str, Any]:
    """处理文档类型任务"""
    return {
        "type": "document",
        "jobid": card.get("jobid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "jtoken": card.get("jtoken", ""),
        "mid": card.get("mid", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", ""),
        "objectid": card.get("property", {}).get("objectid", "")
    }


def _process_work_task(card: Dict[str, Any]) -> Dict[str, Any]:
    """处理作业类型任务"""
    return {
        "type": "workid",
        "jobid": card.get("jobid", ""),
        "otherinfo": card.get("otherInfo", ""),
        "mid": card.get("mid", ""),
        "enc": card.get("enc", ""),
        "aid": card.get("aid", "")
    }


def decode_questions_info(html_content: str) -> Dict[str, Any]:
    """
    解析题目信息，提取表单数据和问题列表
    
    Args:
        html_content: 题目页面HTML内容
        
    Returns:
        包含表单数据和问题列表的字典
    """
    soup = BeautifulSoup(html_content, "lxml")
    form_data = _extract_form_data(soup)
    
    # 检查是否存在字体加密
    has_font_encryption = bool(soup.find("style", id="cxSecretStyle"))
    font_decoder = None
    
    if has_font_encryption:
        font_decoder = FontDecoder(html_content)
    else:
        logger.warning("未找到字体文件，可能是未加密的题目不进行解密")
    
    # 处理所有问题
    questions = []
    for div_tag in soup.find("form").find_all("div", class_="singleQuesId"):
        question = _process_question(div_tag, font_decoder)
        if question:
            questions.append(question)
    
    # 更新表单数据
    form_data["questions"] = questions
    form_data["answerwqbid"] = ",".join([q["id"] for q in questions]) + ","
    
    return form_data


def _extract_form_data(soup: BeautifulSoup) -> Dict[str, Any]:
    """从BeautifulSoup对象中提取表单数据"""
    form_data = {}
    form_tag = soup.find("form")
    
    if not form_tag:
        return form_data
    
    # 提取所有非答案字段的input
    for input_tag in form_tag.find_all("input"):
        if "name" not in input_tag.attrs or "answer" in input_tag.attrs["name"]:
            continue
        form_data[input_tag.attrs["name"]] = input_tag.attrs.get("value", "")
    
    return form_data


def _process_question(div_tag, font_decoder=None) -> Dict[str, Any]:
    """处理单个问题"""
    # 提取问题ID和题目类型
    question_id = div_tag.attrs.get("data", "")
    q_type_code = div_tag.find("div", class_="TiMu").attrs.get("data", "")
    q_type = _get_question_type(q_type_code)
    
    # 提取题目内容和选项
    title_div = div_tag.find("div", class_="Zy_TItle")
    options_list = div_tag.find("ul").find_all("li") if div_tag.find("ul") else []
    
    # 解析题目和选项
    q_title = _extract_title(title_div, font_decoder)
    q_options = []
    for li in options_list:
        q_options.append(_extract_choices(li, font_decoder))
    # 排序选项
    q_options.sort()
    q_options = '\n'.join(q_options)
    
    # 初始化答题字段：至少包含 answer{id} 和 answertype{id}
    answer_field: Dict[str, Any] = {
        f"answer{question_id}": "",
        f"answertype{question_id}": q_type_code,
    }

    # 兼容填空题等可能存在的多个 answer* 字段（例如 answer{id}_0 等）：
    # 收集当前题目 div 下所有 name 中包含 "answer" 且与本题相关的 input 字段名，
    # 以便后续按照原始字段名回填答案。
    for input_tag in div_tag.find_all("input"):
        name = input_tag.attrs.get("name", "")
        if not name or "answer" not in name:
            continue
        # 仅保留与当前题目 ID 相关的字段，避免污染其他题目的字段
        if question_id and question_id not in name:
            continue
        if name not in answer_field:
            answer_field[name] = input_tag.attrs.get("value", "")

    return {
        "id": question_id,
        "title": q_title,
        "options": q_options,
        "type": q_type,
        "answerField": answer_field,
    }


def _get_question_type(type_code: str) -> str:
    """根据题型代码返回题型名称"""
    type_map = {
        "0": "single",      # 单选题
        "1": "multiple",    # 多选题
        "2": "completion",  # 填空题
        "3": "judgement",   # 判断题
        "4": "shortanswer", # 简答题
    }
    
    if type_code in type_map:
        return type_map[type_code]
    
    logger.info(f"未知题型代码 -> {type_code}")
    return "unknown"


def _extract_title(element, font_decoder=None) -> str:
    """提取标题内容，支持解码加密字体"""
    if not element:
        return ""
        
    # 收集元素中的所有文本和图片
    content = []
    for item in element.descendants:
        if isinstance(item, NavigableString):
            content.append(item.string or "")
        elif item.name == "img":
            img_url = item.get("src", "")
            # 如果启用了本地 OCR，则尝试将图片转换为接近 LaTeX 的文本表达
            ocr_text = _ocr_image_to_text(img_url)
            if ocr_text:
                content.append(f"[公式: {ocr_text}]")
            else:
                content.append(f'<img src="{img_url}">')

    raw_content = "".join(content)
    cleaned_content = raw_content.replace("\r", "").replace("\t", "").replace("\n", "")
    
    # 如果有字体解码器，进行解码
    if font_decoder:
        return font_decoder.decode(cleaned_content)
    
    return cleaned_content

def _extract_choices(element, font_decoder=None) -> str:
    """提取选项内容，支持解码加密字体"""
    if not element:
        return ""
        
    # 提取aria-label属性值作为选项，解决#474
    choice = element.get("aria-label") or element.get_text()
    if not choice:
        return ""

    cleaned_content = re.sub(r"[\r\t\n]", "", choice)

    if font_decoder:
        cleaned_content = font_decoder.decode(cleaned_content)

    cleaned_content = cleaned_content.strip()
    if cleaned_content.endswith("选择"):
        cleaned_content = cleaned_content[:-2].rstrip()

    return cleaned_content
