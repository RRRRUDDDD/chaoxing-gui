import configparser
import hashlib
import json
import os
import random
import re
import shutil
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from re import sub
from typing import Optional

from api.answer_check import check_answer, match_answer
from api.logger import logger
from api.decode import _ocr_image_to_text


def _prepare_option_lines(options) -> list[str]:
    if not options:
        return []
    if isinstance(options, str):
        raw = options.splitlines()
    elif isinstance(options, (list, tuple, set)):
        raw = options
    else:
        raw = [str(options)]
    cleaned = []
    for item in raw:
        item_str = str(item).strip()
        if item_str:
            cleaned.append(item_str)
    return cleaned


__all__ = ["CacheDAO", "Tiku"]


_IMG_TAG_PATTERN = re.compile(r'<img[^>]*src=["\'](.*?)["\'][^>]*>', re.IGNORECASE)


def _apply_ocr_to_title_if_needed(q_info: dict) -> None:
    """按当前任务 OCR 配置用识别文本替换题目图片。

    仅处理作业题目的标题字符串，不影响其他阅读类内容；
    当 OCR 不可用或识别失败时，不修改原始标题。
    """
    title = q_info.get("title")
    if not isinstance(title, str) or "<img" not in title:
        return

    def _repl(match: re.Match) -> str:
        src = match.group(1) or ""
        # 只处理超星题目图片的域名，避免误伤其他内容
        if "p.ananas.chaoxing.com" not in src:
            return match.group(0)

        text = ""
        try:
            text = _ocr_image_to_text(src) or ""
        except Exception as exc:
            logger.debug(f"题目图片 OCR 调用异常: {exc}")

        if text:
            return f"[公式: {text}]"
        # 保留图片身份和重试机会，不能把不同公式合并成同一个缓存键。
        return match.group(0)

    new_title = _IMG_TAG_PATTERN.sub(_repl, title)
    if new_title != title:
        logger.debug(f"题目图片 OCR 处理后标题：{new_title}")
        q_info["title"] = new_title

class CacheDAO:
    """
    @Author: SocialSisterYi
    @Reference: https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy

    默认每 32 次实际更新或 Tiku.close() 时原子落盘。磁盘正常时，崩溃最多
    丢失最后一批未完成 flush 的 32 次更新；写盘失败保留脏数据重试，此时不保证该上限。
    锁和批量写入只协调本进程，多个进程不可同时写同一缓存文件。
    """
    DEFAULT_CACHE_FILE = "cache.json"
    FLUSH_EVERY = 32
    KEY_VERSION = 2
    KEY_PREFIX = "question:v2:"
    _shared_instance: Optional["CacheDAO"] = None
    _shared_lock = threading.Lock()

    def __init__(self, file: str = DEFAULT_CACHE_FILE, *, flush_every: int = FLUSH_EVERY):
        if isinstance(flush_every, bool) or not isinstance(flush_every, int) or flush_every < 1:
            raise ValueError("flush_every must be a positive integer")
        self.cache_file = Path(file)
        self._lock = threading.RLock()
        self._memory_cache: Optional[dict] = None
        self._pending_writes = 0
        self._flush_every = flush_every

    @classmethod
    def question_key(cls, q_info: dict) -> str:
        """版本、规范题干、题型和有序选项共同确定答案；不读取旧题干键。"""
        def normalize(value) -> str:
            return " ".join(unicodedata.normalize("NFC", str(value or "")).split())

        payload = {
            "version": cls.KEY_VERSION,
            "title": normalize(q_info.get("title")),
            "type": normalize(q_info.get("type")),
            "options": [normalize(option) for option in _prepare_option_lines(q_info.get("options"))],
        }
        if q_info.get("_image_context", {}).get("urls"):
            payload["image_urls"] = q_info["_image_context"]["urls"]
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf8")
        return cls.KEY_PREFIX + hashlib.sha256(encoded).hexdigest()

    @classmethod
    def get_shared(cls, file: str = DEFAULT_CACHE_FILE) -> "CacheDAO":
        """获取进程级单例, 保证多线程共享同一把锁与内存缓存"""
        with cls._shared_lock:
            if cls._shared_instance is None:
                cls._shared_instance = cls(file)
            return cls._shared_instance

    def _read_cache(self) -> dict:
        # 检查、冷读和首次发布必须位于同一临界区，不能在解锁后发布旧快照。
        with self._lock:
            if self._memory_cache is not None:
                return self._memory_cache
            data = {}
            try:
                if not self.cache_file.is_file():
                    self._memory_cache = {}
                    return self._memory_cache
                try:
                    with self.cache_file.open("r", encoding="utf8") as fp:
                        data = json.load(fp)
                    if not isinstance(data, dict):
                        raise ValueError("cache root must be an object")
                except (ValueError, UnicodeDecodeError) as e:
                    logger.error(f"缓存文件读取失败: {e}, 尝试恢复...")
                    data = None
                    # 尝试从原始二进制中以 utf-8 忽略错误地恢复有效 JSON 段
                    try:
                        raw = self.cache_file.read_bytes()
                        text = raw.decode("utf-8", errors="ignore")
                        start = text.find('{')
                        end = text.rfind('}')
                        if start != -1 and end != -1 and start < end:
                            try:
                                data = json.loads(text[start:end+1])
                            except Exception:
                                data = None
                    except Exception:
                        pass
                    # 若无法恢复，备份损坏文件并返回空缓存
                    if not isinstance(data, dict):
                        try:
                            bak_name = f"{self.cache_file.name}.bak.{int(time.time())}"
                            bak_path = self.cache_file.with_name(bak_name)
                            shutil.copy2(self.cache_file, bak_path)
                            logger.error(f"缓存文件已损坏，已备份为: {bak_path}，将使用空缓存继续运行")
                        except Exception as ex:
                            logger.error(f"备份损坏缓存失败: {ex}")
                        data = {}
            except Exception as e:
                logger.error(f"读取缓存异常: {e}")
                data = {}
            self._memory_cache = data
            return self._memory_cache

    def _write_cache(self, data: dict) -> bool:
        """锁内写入临时文件并原子替换，失败不影响已有文件及待写内存。"""
        with self._lock:
            tmp_path = None
            try:
                parent = self.cache_file.parent
                parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(prefix=self.cache_file.name, dir=str(parent))
                with os.fdopen(fd, "w", encoding="utf8") as fp:
                    json.dump(data, fp, ensure_ascii=False, indent=4)
                    fp.flush()
                    os.fsync(fp.fileno())
                os.replace(tmp_path, str(self.cache_file))
                return True
            except Exception as exc:
                logger.error(f"缓存原子写入失败，保留待写数据: {exc}")
                return False
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

    def flush(self) -> bool:
        """同步写入所有待保存答案；失败返回 False，下次 flush 继续重试。"""
        with self._lock:
            if not self._pending_writes:
                return True
            if not self._write_cache(self._read_cache()):
                return False
            self._pending_writes = 0
            return True

    def get_cache(self, question: str) -> Optional[str]:
        with self._lock:
            answer = self._read_cache().get(question)
            return answer if isinstance(answer, str) else None

    def add_cache(self, question: str, answer: str) -> None:
        # 在同一把锁下更新内存，达到批次大小才全量落盘。
        with self._lock:
            data = self._read_cache()
            if data.get(question) == answer:
                return
            data[question] = answer
            self._pending_writes += 1
            if self._pending_writes >= self._flush_every:
                self.flush()


# TODO: 重构此部分代码，将此类改为抽象类，加载题库方法改为静态方法，禁止直接初始化此类

def _has_ocs_tiku_config(conf) -> bool:
    if not isinstance(conf, dict):
        return False
    if isinstance(conf.get("wrappers"), list) and conf.get("wrappers"):
        return True
    for key in ("subscription", "config"):
        value = conf.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


class Tiku:
    CONFIG_PATH = os.path.join(os.getcwd(), "config.ini")  # TODO: 从运行参数中获取config路径
    DISABLE = False     # 停用标志
    SUBMIT = False      # 提交标志
    COVER_RATE = 0.8    # 覆盖率
    true_list = []
    false_list = []
    def __init__(self) -> None:
        self.query_diagnostics = []
        self._name = None
        self._conf = None
        self._cache_dao: Optional[CacheDAO] = None
        self._close_lock = threading.Lock()

    def close(self) -> bool:
        """任务线程退出后调用：flush 答案并关闭本题库拥有的网络资源。"""
        with self._close_lock:
            try:
                cache = self._cache_dao if self._cache_dao is not None else CacheDAO.get_shared()
                flushed = cache.flush()
            except Exception as exc:
                logger.warning(f"关闭题库时缓存 flush 失败: {exc}")
                flushed = False
            closed = set()
            for name in ("_session",):
                resource = getattr(self, name, None)
                if resource is None:
                    continue
                setattr(self, name, None)
                if id(resource) in closed:
                    continue
                closed.add(id(resource))
                try:
                    resource.close()
                except Exception as exc:
                    logger.warning(f"关闭题库 {name} 失败: {exc}")
            return flushed

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    def init_tiku(self):
        # 仅用于题库初始化, 应该在题库载入后作初始化调用, 随后才可以使用题库
        # 尝试根据配置文件设置提交模式
        if not self._conf:
            self.config_set(self._get_conf())

        # 如果仍然没有配置或已经被标记为禁用, 直接关闭题库功能
        if not self._conf or self.DISABLE:
            self.DISABLE = True
            return

        conf = self._conf

        # 设置提交模式（缺省为不直接提交）
        submit_val = str(conf.get('submit', 'false')).strip().lower()
        self.SUBMIT = submit_val == 'true'

        # 设置覆盖率（缺省为 0.8）
        cover_raw = conf.get('cover_rate', '0.8')
        try:
            self.COVER_RATE = float(cover_raw)
        except (TypeError, ValueError):
            self.COVER_RATE = 0.8

        # 判断题映射表，支持缺省配置
        true_raw = conf.get('true_list')
        false_raw = conf.get('false_list')

        if true_raw and false_raw:
            self.true_list = [s for s in true_raw.split(',') if s]
            self.false_list = [s for s in false_raw.split(',') if s]
        else:
            # 如果未配置，使用一组通用的默认值，避免 KeyError
            self.true_list = ['正确', '对', 'T', 'True', 'true']
            self.false_list = ['错误', '错', 'F', 'False', 'false']

        # 调用自定义题库初始化
        self._init_tiku()

    def _init_tiku(self):
        # 仅用于题库初始化, 例如配置token, 交由自定义题库完成
        pass

    def config_set(self,config):
        self._conf = config

    def _get_conf(self):
        """
        从默认配置文件查询配置, 如果未能查到, 停用题库
        """
        try:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_PATH, encoding="utf8")
            return config['tiku']
        except (KeyError, FileNotFoundError):
            logger.info("未找到tiku配置, 已忽略题库功能")
            self.DISABLE = True
            return None
        
    def query(self,q_info:dict) -> Optional[str]:
        self.query_diagnostics = []
        if self.DISABLE:
            return None

        # 兼容查询预处理，但不修改调用方题目，也不丢弃缓存身份中的有效数字。
        q_info = dict(q_info)

        # 预处理, 去除【单选题】这样与标题无关的字段
        logger.debug(f"原始标题：{q_info['title']}")

        # 检测并处理题目中的图片链接：使用本地 OCR 将公式图片转为文本
        _apply_ocr_to_title_if_needed(q_info)

        cache_key = CacheDAO.question_key(q_info)

        q_info['title'] = sub(r'^\d+', '', q_info['title'])
        q_info['title'] = sub(r'（\d+\.\d+分）$', '', q_info['title'])
        logger.debug(f"处理后标题：{q_info['title']}")

        # 先过缓存
        cache_dao = CacheDAO.get_shared()
        self._cache_dao = cache_dao
        answer = cache_dao.get_cache(cache_key)
        if answer and q_info.get('options') and q_info.get('type') in ('single', 'multiple'):
            if match_answer(answer, q_info).answer is None:
                answer = None
        if answer:
            self.query_diagnostics = [{"source": "cache", "status": "selected"}]
            logger.info(f"从缓存中获取答案：{q_info['title']} -> {answer}")
            return answer.strip()
        else:
            answer = self._query(q_info)
            if answer and answer.strip():
                answer = answer.strip()
                logger.info(f"从{self.name}获取答案：{q_info['title']} -> {answer}")

                valid = (match_answer(answer, q_info, self.true_list, self.false_list).answer is not None
                         if q_info.get('options') or q_info['type'] == 'completion'
                         else check_answer(answer, q_info['type'], self))
                if valid:
                    cache_dao.add_cache(cache_key, answer)
                    return answer
                else:
                    logger.info(f"从{self.name}获取到的答案类型与题目类型不符，已舍弃")
                    return None

            logger.error(f"从{self.name}获取答案失败：{q_info['title']}")
        return None



    def _query(self, q_info:dict) -> Optional[str]:
        """
        查询接口, 交由自定义题库实现
        """
        pass


    def get_tiku_from_config(self):
        """
        从配置文件加载题库, 这个配置可以是用户提供, 可以是默认配置文件
        """
        if not self._conf:
            # 尝试从默认配置文件加载
            self.config_set(self._get_conf())
        if self.DISABLE:
            return self
        if not _has_ocs_tiku_config(self._conf):
            self.DISABLE = True
            logger.error("未找到题库配置, 已忽略题库功能")
            return self
        from api.ocs_tiku import TikuOcs
        new_cls = TikuOcs()
        new_cls.config_set(self._conf)
        return new_cls

    def judgement_select(self, answer: str) -> bool:
        """
        这是一个专用的方法, 要求配置维护两个选项列表, 一份用于正确选项, 一份用于错误选项, 以应对题库对判断题答案响应的各种可能的情况
        它的作用是将获取到的答案answer与可能的选项列对比并返回对应的布尔值
        """
        if self.DISABLE:
            return False
        # 对响应的答案作处理
        answer = answer.strip()
        if answer in self.true_list:
            return True
        elif answer in self.false_list:
            return False
        else:
            # 无法判断, 随机选择
            logger.error(f'无法判断答案 -> {answer} 对应的是正确还是错误, 请自行判断并加入配置文件重启脚本, 本次将会随机选择选项')
            return random.choice([True,False])

    def get_submit_params(self):
        """
        这是一个专用方法, 用于根据当前设置的提交模式, 响应对应的答题提交API中的pyFlag值
        """
        # 留空直接提交, 1保存但不提交
        if self.SUBMIT:
            return ""
        else:
            return "1"

# 按照以下模板实现更多题库
