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

import httpx
import requests
from openai import OpenAI
from urllib3 import disable_warnings, exceptions

from api.answer_check import *
from api.logger import logger
from api.decode import _ocr_image_to_text


def _strip_json_block(md_str: str) -> str:
    """去掉Markdown代码块包裹，返回纯JSON字符串"""
    if not isinstance(md_str, str):
        return ""
    pattern = r'^\s*```(?:json)?\s*(.*?)\s*```\s*$'
    match = re.search(pattern, md_str, re.DOTALL)
    return match.group(1).strip() if match else md_str.strip()


def _ensure_answer_list(value) -> list[str]:
    """确保返回答案列表，兼容字符串、列表等多种格式"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


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


def _clean_option_prefix(option: str) -> str:
    return re.sub(r"^[A-Za-z]\.?,?、?\s*", "", option).strip()

# 关闭警告
disable_warnings(exceptions.InsecureRequestWarning)

__all__ = ["CacheDAO", "Tiku", "TikuYanxi", "TikuLike", "TikuAdapter", "AI", "SiliconFlow"]


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

    def _read_cache_locked(self) -> dict:
        """兼容旧调用方；读取和发布仍由同一把可重入锁保护。"""
        return self._read_cache()

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
class Tiku:
    CONFIG_PATH = os.path.join(os.getcwd(), "config.ini")  # TODO: 从运行参数中获取config路径
    DISABLE = False     # 停用标志
    SUBMIT = False      # 提交标志
    COVER_RATE = 0.8    # 覆盖率
    true_list = []
    false_list = []
    def __init__(self) -> None:
        self._name = None
        self._api = None
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
            for name in ("client", "_httpx_client", "_session"):
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

    @property
    def api(self):
        return self._api

    @api.setter
    def api(self, value):
        self._api = value

    @property
    def token(self):
        return self._token

    @token.setter
    def token(self, value):
        self._token = value

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
        if answer:
            logger.info(f"从缓存中获取答案：{q_info['title']} -> {answer}")
            return answer.strip()
        else:
            answer = self._query(q_info)
            if answer and answer.strip():
                answer = answer.strip()
                logger.info(f"从{self.name}获取答案：{q_info['title']} -> {answer}")

                # 对 AI / 硅基流动等大模型题库更宽松：只要有非空答案就直接使用并写入缓存，
                # 不再依赖 check_answer 的严格类型判断，避免丢弃诸如“输入/输出”、“Babbage machine”这种正常答案
                from api.answer import AI, SiliconFlow  # type: ignore
                if isinstance(self, (AI, SiliconFlow)):
                    cache_dao.add_cache(cache_key, answer)
                    return answer

                if check_answer(answer, q_info['type'], self):
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
        try:
            cls_name = self._conf['provider']
            if not cls_name:
                raise KeyError
        except KeyError:
            self.DISABLE = True
            logger.error("未找到题库配置, 已忽略题库功能")
            return self
        # FIXME: Implement using StrEnum instead. This is not only buggy but also not safe
        new_cls = globals()[cls_name]()
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

class TikuYanxi(Tiku):
    # 言溪题库实现
    def __init__(self) -> None:
        super().__init__()
        self.name = '言溪题库'
        self.api = 'https://tk.enncy.cn/query'
        self._token = None
        self._token_index = 0   # token队列计数器
        self._times = 100   # 查询次数剩余, 初始化为100, 查询后校对修正

    def _query(self,q_info:dict):
        res = requests.get(
            self.api,
            params={
                'question':q_info['title'],
                'token': self._token,
                # 'type':q_info['type'], #修复478题目类型与答案类型不符（不想写后处理了）
                # 没用，就算有type和options，言溪题库还是可能返回类型不符，问了客服，type仅用于收集
            },
            verify=False,
            timeout=30
        )
        if res.status_code == 200:
            res_json = res.json()
            if not res_json['code']:
                # 如果是因为TOKEN次数到期, 则更换token
                if self._times == 0 or '次数不足' in res_json['data']['answer']:
                    logger.info(f'TOKEN查询次数不足, 将会更换并重新搜题')
                    self._token_index += 1
                    self.load_token()
                    # 重新查询
                    return self._query(q_info)
                logger.error(f'{self.name}查询失败:\n\t剩余查询数{res_json["data"].get("times",f"{self._times}(仅参考)")}:\n\t消息:{res_json["message"]}')
                return None
            self._times = res_json["data"].get("times",self._times)
            return res_json['data']['answer'].strip()
        else:
            logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def load_token(self):
        token_list = self._conf['tokens'].split(',')
        if self._token_index == len(token_list):
            # TOKEN 用完
            logger.error('TOKEN用完, 请自行更换再重启脚本')
            raise PermissionError(f'{self.name} TOKEN 已用完, 请更换')
        self._token = token_list[self._token_index]

    def _init_tiku(self):
        self.load_token()

class TikuLike(Tiku):
    # LIKE知识库实现 参考 https://www.datam.site/
    def __init__(self) -> None:
        super().__init__()
        self.name = 'LIKE知识库'
        self.ver = '2.0.0' #对应官网API版本
        self.query_api = 'https://app.datam.site/api/v1/query'
        self.models_api = 'https://app.datam.site/api/v1/query/models'
        self.balance_api = 'https://app.datam.site/api/v1/balance'
        self.homepage = 'https://www.datam.site'
        self._model = None
        self._timeout = 300
        self._retry = True
        self._retry_times = 3
        self._tokens = []
        self._balance = {}
        self._search = False
        self._vision = True
        self._count = 0
        self._headers = {"Content-Type": "application/json"}

    def _query(self, q_info:dict = None):
        if not q_info:
            logger.error("当前无题目信息，请检查")
            return ""
        
        q_info_map = {"single": "【单选题】", "multiple": "【多选题】", "completion": "【填空题】", "judgement": "【判断题】"}
        q_info_prefix = q_info_map.get(q_info['type'], "【其他类型题目】")
        options = ', '.join(q_info['options']) if isinstance(q_info['options'], list) else q_info['options']
        question = f"{q_info_prefix}{q_info['title']}\n"

        if q_info['type'] in ['single', 'multiple']:
            question += f"选项为: {options}\n"

        # 随机选择一个token进行查询
        token = random.choice(self._tokens)
        
        # 检查该token是否有余额
        if self._balance.get(token, 0) <= 0:
            logger.error(f'{self.name}当前Token查询次数不足: ...{token[-5:]}')
            # 尝试选择其他有余额的token
            available_tokens = [t for t in self._tokens if self._balance.get(t, 0) > 0]
            if available_tokens:
                token = random.choice(available_tokens)
            else:
                logger.error(f'{self.name}所有Token查询次数都不足')
                return None

        ans = None
        try_times = 0
        
        # 尝试查询，直到成功或达到重试次数
        while not ans and self._retry and try_times < self._retry_times:
            ans = self._query_single(token, question)
            try_times += 1
            if ans:  # 如果查询成功，减少余额
                self._balance[token] -= 1
                logger.info(f'使用Token ...{token[-5:]} 查询成功，剩余次数: {self._balance[token]}')
                break
            elif try_times < self._retry_times:
                logger.warning(f'使用Token ...{token[-5:]} 查询失败，进行第 {try_times + 1} 次重试...')
        
        # 10次查询后更新余额
        self._count = (self._count + 1) % 10
        if self._count == 0:
            self.update_times()

        return ans
    
    def _query_single(self, token: str = "", query: str = "") -> str:
        """
        查询单个问题的答案
        
        Args:
            token: API访问令牌
            query: 查询的问题内容
            
        Returns:
            查询到的答案，如果失败则返回None
        """
        # 验证输入参数
        if not token:
            logger.error(f'{self.name}查询失败: 未提供有效的token')
            return None
        
        if not query:
            logger.error(f'{self.name}查询失败: 查询内容为空')
            return None
            
        # 设置请求头
        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'
        
        # 准备请求数据
        request_data = {
            'query': query,
            'model': self._model if self._model else '',
            'search': self._search,
            'vision': self._vision
        }
        
        # 发送API请求
        try:
            res = requests.post(
                self.query_api,
                json=request_data,
                headers=temp_headers,
                verify=False,
                timeout=self._timeout  # 添加超时设置
            )
        except requests.exceptions.Timeout:
            logger.error(f'{self.name}查询超时: 请求超过300秒')
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name}网络连接错误: 无法连接到API服务器')
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f'{self.name}查询异常: \n{e}')
            return None
        except Exception as e:
            logger.error(f'{self.name}查询发生未知错误: \n{e}')
            return None

        # 处理HTTP响应
        if res.status_code == 200:
            return self._parse_response(res)
        elif res.status_code == 401:
            logger.error(f'{self.name}认证失败: 请检查Token是否正确或已过期')
        elif res.status_code == 429:
            logger.error(f'{self.name}请求过于频繁: 已达到API速率限制')
        elif res.status_code == 500:
            logger.error(f'{self.name}服务器内部错误: API服务暂时不可用')
        elif res.status_code == 400:
            logger.error(f'{self.name}请求参数错误: 请检查查询内容格式')
        elif res.status_code == 403:
            logger.error(f'{self.name}访问被拒绝: 可能是Token权限不足')
        else:
            logger.error(f'{self.name}查询失败: 状态码 {res.status_code}, 响应内容: \n{res.text}')
        
        return None
    
    def _parse_response(self, response):
        """
        解析API响应
        
        Args:
            response: HTTP响应对象
            
        Returns:
            解析后的答案，如果解析失败则返回None
        """
        try:
            res_json = response.json()
        except json.JSONDecodeError:
            logger.error(f'{self.name}响应解析失败: 响应不是有效的JSON格式')
            return None
        except Exception as e:
            logger.error(f'{self.name}响应解析异常: {e}')
            return None
        
        # 记录响应消息
        msg = res_json.get('message', '')
        if msg:
            logger.info(f'{self.name}响应消息: {msg}')
        
        # 检查API返回的code字段，判断请求是否成功
        code = res_json.get('code', 1)
        if code != 1:
            error_msg = res_json.get('message', '未知错误')
            logger.error(f'{self.name}API返回错误: {error_msg}')
            return None
        
        results = res_json.get('results', {})
        if not results or not isinstance(results, dict):
            logger.error(f'{self.name}查询结果格式错误: API返回结果中results字段格式不正确')
            return None
            
        output = results.get('output', None)
        if output is None or not isinstance(output, dict):
            logger.error(f'{self.name}查询结果中output字段格式错误或不存在')
            return None
            
        q_type = output.get('questionType', None)
        if q_type is None:
            logger.error(f'{self.name}查询结果中questionType字段不存在')
            return None
            
        answer = output.get('answer', None)
        if answer is None:
            logger.error(f'{self.name}查询结果中answer字段不存在')
            return None
            
        # 根据题目类型提取答案
        return self._extract_answer_by_type(q_type, answer)
    
    def _extract_answer_by_type(self, q_type: str, answer: dict) -> str:
        """
        根据题目类型提取答案
        
        Args:
            q_type: 题目类型
            answer: 答案字典
            
        Returns:
            提取的答案文本
        """
        if not isinstance(answer, dict):
            logger.error(f'{self.name}答案格式错误: 不是有效的字典格式')
            return None
            
        if q_type == "CHOICE":
            selected_options = answer.get('selectedOptions', None)
            if selected_options is not None:
                if isinstance(selected_options, list) and selected_options:
                    # 过滤掉None和空字符串
                    valid_options = [opt for opt in selected_options if opt is not None and str(opt).strip()]
                    if valid_options:
                        return '\n'.join(str(opt) for opt in valid_options)
                    else:
                        logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
                else:
                    logger.error(f'{self.name}CHOICE类型题目没有有效的选项内容')
            else:
                logger.error(f'{self.name}CHOICE类型题目缺少selectedOptions字段')
        elif q_type == "FILL_IN_BLANK":
            blanks = answer.get('blanks', None)
            if blanks is not None:
                if isinstance(blanks, list) and blanks:
                    # 过滤掉None和空字符串
                    valid_blanks = [blank for blank in blanks if blank is not None and str(blank).strip()]
                    if valid_blanks:
                        return "\n".join(str(blank) for blank in valid_blanks)
                    else:
                        logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
                else:
                    logger.error(f'{self.name}FILL_IN_BLANK类型题目没有有效的填空内容')
            else:
                logger.error(f'{self.name}FILL_IN_BLANK类型题目缺少blanks字段')
        elif q_type == "JUDGMENT":
            is_correct = answer.get('isCorrect', None)
            if is_correct is not None:
                return "正确" if is_correct else "错误"
            else:
                logger.error(f'{self.name}JUDGMENT类型题目缺少isCorrect字段')
        else:
            otherText = answer.get('otherText', None)
            if otherText is not None:
                return str(otherText)
            else:
                logger.error(f'{self.name}未知题目类型{q_type}且缺少otherText字段')
        
        return None
    
    def get_api_balance(self, token:str = ""):
        if not token:
            logger.error(f'{self.name}获取余额失败: 未提供有效的token')
            return 0
            
        temp_headers = self._headers.copy()
        temp_headers['Authorization'] = f'Bearer {token}'
        try:
            res = requests.post(
                self.balance_api,
                headers=temp_headers,
                verify=False,
                timeout=self._timeout
            )
            if res.status_code == 200:
                res_json = res.json()
                code = res_json.get('code', 0)
                if code == 1:
                    return int(res_json.get("balance", 0))
                else:
                    error_msg = res_json.get('message', '未知错误')
                    logger.error(f'{self.name}获取余额失败: {error_msg}')
                    return 0
            else:
                logger.error(f'{self.name}请求余额接口失败，状态码: {res.status_code}')
                return 0
        except requests.exceptions.Timeout:
            logger.error(f'{self.name}获取余额超时: 请求超过30秒')
            return 0
        except requests.exceptions.ConnectionError:
            logger.error(f'{self.name}网络连接错误: 无法连接到余额查询API服务器')
            return 0
        except ValueError:  # json解析错误或int转换错误
            logger.error(f'{self.name}余额响应解析失败: 响应格式不正确')
            return 0
        except Exception as e:
            logger.error(f'{self.name}Token余额查询过程中出现错误: {e}')
            return 0

    def update_times(self) -> None:
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何Token, 无法更新余额')
            return
        for token in self._tokens:
            balance = self.get_api_balance(token)
            self._balance[token] = balance
            logger.info(f"当前LIKE知识库Token: ...{token[-5:]} 的剩余查询次数为: {balance} (仅供参考, 实际次数以查询结果为准)")

    def load_tokens(self) -> None:
        tokens_str = self._conf.get('tokens')
        if not tokens_str:
            logger.error(f'{self.name}配置中未找到tokens')
            self._tokens = []
            return
        if ',' in tokens_str:
            tokens = [token.strip() for token in tokens_str.split(',') if token.strip()]
        else:
            tokens = [tokens_str.strip()] if tokens_str.strip() else []
        self._tokens = tokens
        if not self._tokens:
            logger.warning(f'{self.name}未加载任何有效的Token')

    def load_config(self) -> None:
        # 从配置中获取参数，提供默认值(config.ini 读到的均为字符串, 需做类型转换)
        self._search = str(self._conf.get('likeapi_search', False)).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
        self._model = self._conf.get('likeapi_model') or None
        self._vision = str(self._conf.get('likeapi_vision', True)).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
        self._retry = str(self._conf.get("likeapi_retry", True)).strip().lower() in {'1', 'true', 'yes', 'y', 'on'}
        try:
            self._retry_times = int(self._conf.get("likeapi_retry_times", 3))
        except (TypeError, ValueError):
            self._retry_times = 3

    def _init_tiku(self) -> None:
        self.load_config()
        self.load_tokens()
        if self._tokens:
            self.update_times()
        else:
            logger.error(f'{self.name}初始化失败: 未加载任何有效的Token')
            self.DISABLE = True

class TikuAdapter(Tiku):
    # TikuAdapter题库实现 https://github.com/DokiDoki1103/tikuAdapter
    def __init__(self) -> None:
        super().__init__()
        self.name = 'TikuAdapter题库'
        self.api = ''

    def _query(self, q_info: dict):
        # 判断题目类型
        if q_info['type'] == "single":
            type = 0
        elif q_info['type'] == 'multiple':
            type = 1
        elif q_info['type'] == 'completion':
            type = 2
        elif q_info['type'] == 'judgement':
            type = 3
        else:
            type = 4

        options = q_info['options']
        res = requests.post(
            self.api,
            json={
                'question': q_info['title'],
                'options': [sub(r'^[A-Za-z]\.?、?\s?', '', option) for option in options.split('\n')],
                'type': type
            },
            verify=False,
            timeout=30
        )
        if res.status_code == 200:
            res_json = res.json()
            # if bool(res_json['plat']):
            # plat无论搜没搜到答案都返回0
            # 这个参数是tikuadapter用来设定自定义的平台类型
            if not len(res_json['answer']['bestAnswer']):
                logger.error("查询失败, 返回：" + res.text)
                return None
            sep = "\n"
            return sep.join(res_json['answer']['bestAnswer']).strip()
        # else:
        #   logger.error(f'{self.name}查询失败:\n{res.text}')
        return None

    def _init_tiku(self):
        # self.load_token()
        self.api = self._conf['url']

class AI(Tiku):
    """AI大模型答题实现，带重试与更鲁棒的解析"""

    def __init__(self) -> None:
        super().__init__()
        self.name = 'AI大模型答题'
        self.last_request_time = None
        self.client: Optional[OpenAI] = None
        self._httpx_client: Optional[httpx.Client] = None
        self.http_proxy: Optional[str] = None
        self.min_interval_seconds: float = 3
        self.timeout: float = 30
        self.max_retries: int = 3
        self.retry_delay: float = 2.0
        self.disable_ssl_verify: bool = False
        self._interval_lock = threading.Lock()
        # 全局并发控制：限制同一 AI 客户端同时在请求中的题目数量
        self.max_active_requests: int = 3
        self._request_semaphore: Optional[threading.Semaphore] = None
        # 精简提示词：直接输出 JSON，禁止多余内容
        self._system_prompts = {
            "single": (
                "单选题答题。直接输出JSON，禁止解释、思考过程或Markdown。\n"
                "格式：{\"Answer\": [\"B\"]}  （B为正确选项字母，仅填A/B/C/D之一）"
            ),
            "multiple": (
                "多选题答题。直接输出JSON，禁止解释、思考过程或Markdown。\n"
                "格式：{\"Answer\": [\"A\", \"C\"]}  （填所有正确选项字母）"
            ),
            "completion": (
                "填空题答题。直接输出JSON，禁止解释、思考过程或Markdown。\n"
                "格式：{\"Answer\": [\"答案1\", \"答案2\"]}  （按空格顺序填写答案）"
            ),
            "judgement": (
                "判断题答题。直接输出JSON，禁止解释、思考过程或Markdown。\n"
                "格式：{\"Answer\": [\"正确\"]} 或 {\"Answer\": [\"错误\"]}"
            ),
            "default": (
                "答题助手。直接输出JSON，禁止解释、思考过程或Markdown。\n"
                "格式：{\"Answer\": [\"答案\"]}"
            ),
        }

    def _build_client(self):
        httpx_kwargs = {"timeout": self.timeout}
        if self.http_proxy:
            httpx_kwargs["proxy"] = self.http_proxy
        if self.disable_ssl_verify:
            httpx_kwargs["verify"] = False
        if self._httpx_client:
            try:
                self._httpx_client.close()
            except Exception:
                pass
        self._httpx_client = httpx.Client(**httpx_kwargs)

    def _respect_interval(self):
        with self._interval_lock:
            if self.last_request_time:
                interval_time = time.time() - self.last_request_time
                if interval_time < self.min_interval_seconds:
                    sleep_time = self.min_interval_seconds - interval_time
                    logger.debug(f"AI请求间隔过短, 等待 {sleep_time:.2f} 秒")
                    time.sleep(sleep_time)
            self.last_request_time = time.time()

    def _build_messages(self, q_info: dict) -> list[dict]:
        options = [_clean_option_prefix(opt) for opt in _prepare_option_lines(q_info.get('options', []))]
        q_type = q_info.get('type', 'single')
        system_prompt = self._system_prompts.get(q_type, self._system_prompts['default'])
        user_content = f"题目：{q_info.get('title', '')}".strip()
        if options:
            user_content = f"{user_content}\n选项：{chr(10).join(options)}"
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

    def _invoke_completion(self, messages: list[dict]) -> Optional[str]:
        if not self._httpx_client:
            logger.error("AI题库 HTTP 客户端未初始化")
            return None
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                # 全局并发控制：限制同时在请求中的题目数量
                sem = self._request_semaphore
                if sem is not None:
                    sem.acquire()
                try:
                    self._respect_interval()
                    headers = {
                        "Authorization": f"Bearer {self.key}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": self.model,
                        "messages": messages,
                    }
                    response = self._httpx_client.post(
                        self.endpoint,
                        headers=headers,
                        json=payload,
                    )
                finally:
                    if sem is not None:
                        sem.release()
                if response.status_code != 200:
                    try:
                        err_body = response.json()
                    except Exception:
                        err_body = response.text[:200]
                    raise RuntimeError(f"Error code: {response.status_code} - {err_body}")

                data = response.json()
                raw_content = data["choices"][0]["message"]["content"] or ""

                # 去掉 <think> 和 </think> 标记本身，但保留其中的内容，以便从中提取 Answer JSON
                text_without_tags = re.sub(r"(?is)</?think>", "", raw_content)
                base_text = (text_without_tags or "").strip()
                if not base_text:
                    logger.warning("AI大模型返回内容为空（仅包含 <think> 标签或空白），将视为无答案处理")
                    return None

                answers: list[str] = []

                # 优先从文本中提取包含 Answer/answer 字段的 JSON 段，取最后一个作为最终答案
                json_candidate = None
                try:
                    pattern = r'\{[^{}]*(?:"Answer"|"answer")\s*:\s*\[[^\]]*\][^{}]*\}'
                    matches = re.findall(pattern, base_text, flags=re.DOTALL)
                    if matches:
                        json_candidate = matches[-1].strip()
                    else:
                        # 回退到去除 Markdown 代码块后的整体文本
                        json_candidate = _strip_json_block(base_text)
                except Exception:
                    json_candidate = _strip_json_block(base_text)

                json_candidate_stripped = (json_candidate or "").strip()

                if json_candidate_stripped:
                    # 优先按JSON解析；若失败，再尝试将单引号风格的字典转换为JSON
                    try:
                        payload = json.loads(json_candidate_stripped)
                        answers = _ensure_answer_list(payload.get('Answer') or payload.get('answer'))
                    except json.JSONDecodeError as json_exc:
                        payload = None
                        candidate_fixed = json_candidate_stripped
                        # 针对形如 {'Answer': ['输入/输出']} 的内容，尝试将单引号替换为双引号后再次解析
                        if "'" in candidate_fixed and '"' not in candidate_fixed:
                            try:
                                candidate_fixed = candidate_fixed.replace("'", '"')
                                payload = json.loads(candidate_fixed)
                            except Exception:
                                payload = None
                        if payload is not None:
                            answers = _ensure_answer_list(payload.get('Answer') or payload.get('answer'))
                        else:
                            logger.warning(
                                f"AI大模型返回内容不是标准JSON，将按纯文本处理: {json_exc}; candidate={json_candidate_stripped[:200]!r}"
                            )
                            answers = _ensure_answer_list(base_text)
                else:
                    # 没有可用的 JSON 片段，直接按纯文本处理
                    if base_text:
                        answers = _ensure_answer_list(base_text)
                    else:
                        logger.warning("AI大模型返回内容为空，将视为无答案处理")
                        return None

                if not answers:
                    logger.warning("AI大模型返回空答案，将视为无答案处理")
                    return None
                return "\n".join(answers).strip()
            except Exception as exc:
                last_error = exc
                msg = str(exc)
                if "429" in msg or "rate limit" in msg.lower():
                    cool_down = max(self.min_interval_seconds * 2, 5)
                    logger.warning(
                        f"AI大模型请求失败 ({attempt}/{self.max_retries}) 且触发限流，将休眠 {cool_down:.2f} 秒: {exc}"
                    )
                    time.sleep(cool_down)
                else:
                    logger.warning(f"AI大模型请求失败 ({attempt}/{self.max_retries}): {exc}")
                    time.sleep(self.retry_delay * attempt)
        logger.error(f"AI大模型连续失败，最后错误: {last_error}")
        return None

    def _query(self, q_info: dict):
        messages = self._build_messages(q_info)
        return self._invoke_completion(messages)

    def _init_tiku(self):
        self.endpoint = self._conf['endpoint']
        self.key = self._conf['key']
        self.model = self._conf['model']
        self.http_proxy = self._conf.get('http_proxy')
        self.min_interval_seconds = float(self._conf.get('min_interval_seconds', 3))
        self.timeout = float(self._conf.get('timeout', 30))
        self.max_retries = int(self._conf.get('max_retries', 3))
        self.retry_delay = float(self._conf.get('retry_delay', 2))
        self.disable_ssl_verify = str(self._conf.get('disable_ssl_verify', 'false')).lower() in {'1', 'true', 'yes', 'on', 'y'}
        # 允许通过 ai_concurrency 配置最大同时在请求中的题目数量；缺省为 3
        max_req_raw = self._conf.get('ai_concurrency')
        try:
            self.max_active_requests = int(max_req_raw) if max_req_raw is not None else 3
        except (TypeError, ValueError):
            self.max_active_requests = 3
        if self.max_active_requests < 1:
            self.max_active_requests = 1
        self._request_semaphore = threading.Semaphore(self.max_active_requests)
        self._build_client()


class SiliconFlow(Tiku):
    """硅基流动大模型答题实现"""
    def __init__(self):
        super().__init__()
        self.name = '硅基流动大模型'
        self.last_request_time = None

    def _query(self, q_info: dict):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        prompt_map = {
            "single": "单选题。直接输出JSON，禁止解释或Markdown。格式：{\"Answer\": [\"B\"]}（仅填选项字母）",
            "multiple": "多选题。直接输出JSON，禁止解释或Markdown。格式：{\"Answer\": [\"A\", \"C\"]}（填所有正确选项字母）",
            "completion": "填空题。直接输出JSON，禁止解释或Markdown。格式：{\"Answer\": [\"答案\"]}",
            "judgement": "判断题。直接输出JSON，禁止解释或Markdown。格式：{\"Answer\": [\"正确\"]} 或 {\"Answer\": [\"错误\"]}"
        }

        system_prompt = prompt_map.get(q_info.get('type'),
                                       "直接输出JSON答案，禁止解释或Markdown。格式：{\"Answer\": [\"答案\"]}")

        cleaned_options = [_clean_option_prefix(opt) for opt in _prepare_option_lines(q_info.get('options', []))]
        user_content = f"题目：{q_info.get('title', '')}".strip()
        if cleaned_options:
            user_content = f"{user_content}\n选项：{chr(10).join(cleaned_options)}"

        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            "stream": False,
            "max_tokens": 4096,
            "temperature": 0.7,
            "top_p": 0.7,
            "response_format": {"type": "text"}
        }

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.last_request_time:
                    interval = time.time() - self.last_request_time
                    if interval < self.min_interval:
                        time.sleep(self.min_interval - interval)

                response = self._session.post(
                    self.api_endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout
                )
                self.last_request_time = time.time()

                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code}: {response.text[:200]}")

                result = response.json()
                content = result['choices'][0]['message']['content']
                parsed = json.loads(_strip_json_block(content))
                answers = _ensure_answer_list(parsed.get('Answer') or parsed.get('answer'))
                if not answers:
                    raise ValueError("硅基流动返回答案为空")
                return "\n".join(answers).strip()
            except (requests.RequestException, ValueError, KeyError, json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                logger.warning(f"硅基流动API调用失败 ({attempt}/{self.max_retries}): {exc}")
                time.sleep(self.retry_delay * attempt)

        logger.error(f"硅基流动API连续失败，最后错误: {last_error}")
        return None

    def _init_tiku(self):
        # 从配置文件读取参数
        self.api_endpoint = self._conf.get('siliconflow_endpoint', 'https://api.siliconflow.cn/v1/chat/completions')
        self.api_key = self._conf['siliconflow_key']
        self.model_name = self._conf.get('siliconflow_model', 'deepseek-ai/DeepSeek-V3')
        self.min_interval = int(self._conf.get('min_interval_seconds', 3))
        self.timeout = int(self._conf.get('timeout', 30))
        self.max_retries = int(self._conf.get('max_retries', 3))
        self.retry_delay = float(self._conf.get('retry_delay', 2))
        self._session = requests.Session()
