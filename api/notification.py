"""
通知服务模块，用于向外部服务发送通知消息。
支持多种通知服务，如ServerChan、Qmsg、Bark和Windows系统通知。
"""

import configparser
import subprocess
import sys
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional
from xml.sax.saxutils import escape

import requests

from api.logger import logger
from api.privacy import redact, register_config


NOTIFICATION_TIMEOUT = (5, 10)


class NotificationService(ABC):
    """
    通知服务基类，定义通知服务的公共接口和实现。
    所有具体的通知服务类应继承此类并实现必要的方法。
    """

    CONFIG_PATH = "config.ini"

    def __init__(self):
        """初始化通知服务"""
        self.name = self.__class__.__name__
        self.url = ""
        self.tg_chat_id = ""
        self._conf = None
        self.disabled = False

    def config_set(self, config: Dict[str, str]) -> None:
        """
        设置通知服务的配置
        
        Args:
            config: 包含配置参数的字典
        """
        register_config(config)
        self._conf = config

    def _load_config_from_file(self) -> Optional[Dict[str, str]]:
        """
        从配置文件中加载通知服务的配置
        
        Returns:
            成功返回配置字典，失败返回None
        """
        try:
            config = configparser.ConfigParser()
            config.read(self.CONFIG_PATH, encoding="utf8")
            register_config(config['notification'])
            return config['notification']
        except (KeyError, FileNotFoundError):
            logger.info("未找到notification配置，已忽略外部通知功能")
            self.disabled = True
            return None

    def init_notification(self) -> None:
        """初始化通知服务，加载配置并进行必要的设置"""
        if not self._conf:
            self._conf = self._load_config_from_file()

        if not self.disabled and self._conf:
            self._init_service()

    @abstractmethod
    def _init_service(self) -> None:
        """
        初始化特定的通知服务，由子类实现
        """
        pass

    @abstractmethod
    def _send(self, message: str) -> bool:
        """
        发送通知消息，由子类实现

        Args:
            message: 要发送的消息内容

        Returns:
            是否确认送达；失败返回 False，由 send() 决定是否重试。
        """
        pass

    def send(self, message: str, *, attempts: int = 2) -> bool:
        """
        发送通知消息的公共接口；失败自动重试，返回是否至少一次确认成功。

        Args:
            message: 要发送的消息内容
            attempts: 最大尝试次数（含首次），最小为 1
        """
        if self.disabled:
            return True
        text = redact(message)
        for _ in range(max(1, attempts)):
            if self._send(text):
                return True
        return False

    def _post(self, provider: str, ok: Optional[Callable[[dict], bool]] = None, **kwargs) -> bool:
        """
        HTTP 通知服务共用的发送路径：POST 后校验状态码与 JSON 响应。

        Args:
            provider: 日志中显示的服务名（如 "Server酱"）
            ok: 可选的响应级成功判定；为 None 时任何可解析的 JSON 都视为送达
        """
        try:
            response = requests.post(self.url, timeout=NOTIFICATION_TIMEOUT, **kwargs)
            response.raise_for_status()
            result = response.json()
        except requests.RequestException as exc:
            logger.error("{}通知发送失败: {}", provider, exc)
            return False
        except ValueError as exc:
            logger.error("{}返回数据解析失败: {}", provider, exc)
            return False
        if ok is not None and not ok(result):
            logger.error("{}通知发送失败: {}", provider, result)
            return False
        logger.info("{}通知发送成功: {}", provider, result)
        return True


class DefaultNotification(NotificationService):
    """
    默认通知服务，当未配置任何通知服务时使用
    """

    def _init_service(self) -> None:
        pass

    def _send(self, message: str) -> bool:
        return True

    def get_notification_from_config(self) -> NotificationService:
        """
        根据配置创建具体的通知服务实例
        
        Returns:
            通知服务实例
        """
        if not self._conf:
            self._conf = self._load_config_from_file()

        if self.disabled:
            return self

        try:
            provider_name = self._conf['provider']
            if not provider_name:
                raise KeyError("未指定通知服务提供商")

            # 获取对应的通知服务类（仅注册表白名单内的类名可实例化）
            provider_class = PROVIDER_REGISTRY.get(provider_name)
            if not provider_class:
                logger.error("未找到名为 {} 的通知服务提供商", provider_name)
                self.disabled = True
                return self

            # 创建通知服务实例
            service = provider_class()
            service.config_set(self._conf)
            return service

        except KeyError:
            self.disabled = True
            logger.info("未找到外部通知配置，已忽略外部通知功能")
            return self


class ServerChan(NotificationService):
    """
    Server酱通知服务
    """

    def _init_service(self) -> None:
        """初始化Server酱服务"""
        if not self._conf or not self._conf.get('url'):
            self.disabled = True
            logger.info("未找到Server酱url配置，已忽略该通知服务")
            return

        self.url = self._conf['url']
        logger.info("已初始化Server酱通知服务")

    def _send(self, message: str) -> bool:
        """
        通过Server酱发送通知

        Args:
            message: 要发送的消息内容
        """
        # text 与 desp 同时携带消息，兼容两个版本的Server酱
        return self._post("Server酱",
                          json={"text": message, "desp": message},
                          headers={"Content-Type": "application/json;charset=utf-8"})


class Qmsg(NotificationService):
    """
    Qmsg酱通知服务
    """

    def _init_service(self) -> None:
        """初始化Qmsg酱服务"""
        if not self._conf or not self._conf.get('url'):
            self.disabled = True
            logger.info("未找到Qmsg酱url配置，已忽略该通知服务")
            return

        self.url = self._conf['url']
        logger.info("已初始化Qmsg酱通知服务")

    def _send(self, message: str) -> bool:
        """
        通过Qmsg酱发送通知

        Args:
            message: 要发送的消息内容
        """
        return self._post("Qmsg酱",
                          params={"msg": message},
                          headers={"Content-Type": "application/json;charset=utf-8"})


class Bark(NotificationService):
    """
    Bark通知服务
    """

    def _init_service(self) -> None:
        """初始化Bark服务"""
        if not self._conf or not self._conf.get('url'):
            self.disabled = True
            logger.info("未找到Bark的url配置，已忽略该通知服务")
            return

        self.url = self._conf['url']
        logger.info("已初始化Bark通知服务")

    def _send(self, message: str) -> bool:
        """
        通过Bark发送通知

        Args:
            message: 要发送的消息内容
        """
        return self._post("Bark", params={"body": message})

class Telegram(NotificationService):
    """
    通过Telegram发送通知
    """

    def _init_service(self) -> None:
        """初始化Telegram服务"""
        if not self._conf or not self._conf.get('url') or not self._conf.get('tg_chat_id'):
            self.disabled = True
            logger.info("未找到Telegram的url或tg_chat_id配置，已忽略该通知服务")
            return
        self.tg_chat_id = self._conf['tg_chat_id']
        self.url = self._conf['url']
        logger.info("已初始化Telegram通知服务")

    def _send(self, message: str) -> bool:
        """
        通过Telegram发送通知

        Args:
            message: 要发送的消息内容
        """
        # parse_mode=HTML 要求正文先转义，防止消息里的 <>& 被当作标签解析
        return self._post("Telegram",
                          data={"chat_id": self.tg_chat_id,
                                "text": escape(message),
                                "parse_mode": "HTML"},
                          ok=lambda result: bool(result.get("ok")))

class Windows(NotificationService):
    """
    Windows 系统通知（Toast），通过 Windows PowerShell 调用 WinRT 接口发送。
    注意：必须使用 Windows PowerShell 5.1 (powershell.exe)，
    PowerShell 7 (pwsh) 没有 WinRT 投影，无法加载 Toast 类型。
    """

    def _init_service(self) -> None:
        """初始化Windows系统通知服务"""
        if sys.platform != 'win32':
            self.disabled = True
            logger.info("当前系统不是 Windows，已忽略Windows系统通知服务")
            return

        # 预先探测 PowerShell 能否成功弹出 Toast（同时作为启用确认）
        ok, err = self._run_powershell(self._build_script("Windows 系统通知已启用"))
        if not ok:
            self.disabled = True
            logger.error("Windows系统通知不可用，已忽略该通知服务: {}", err)
            return

        logger.info("已初始化Windows系统通知服务")

    @staticmethod
    def _build_script(message: str) -> str:
        """
        构造发送 Toast 通知的 PowerShell 脚本

        Args:
            message: 通知内容（调用方需保证已 XML 转义）
        """
        return (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] | Out-Null; "
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
            "ContentType = WindowsRuntime] | Out-Null; "
            "$xmlText = @'\n"
            '<toast><visual><binding template="ToastGeneric">'
            f"<text>{message}</text>"
            '</binding></visual></toast>\n'
            "'@; "
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
            "$xml.LoadXml($xmlText); "
            "$toast = New-Object Windows.UI.Notifications.ToastNotification($xml); "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
            "'Microsoft.Windows.Explorer').Show($toast)"
        )

    @staticmethod
    def _run_powershell(script: str) -> tuple:
        """
        执行 PowerShell 脚本

        Args:
            script: PowerShell 脚本内容

        Returns:
            (是否成功, 错误信息)
        """
        try:
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                capture_output=True,
                timeout=15,
            )
            if result.returncode == 0:
                return True, ''
            return False, result.stderr.decode('utf-8', errors='replace').strip()
        except Exception as e:
            return False, str(e)

    def _send(self, message: str) -> bool:
        """
        发送Windows系统通知

        Args:
            message: 要发送的消息内容
        """
        # 消息需经过 XML 转义后再嵌入 Toast 模板
        ok, err = self._run_powershell(self._build_script(escape(message)))
        if ok:
            logger.info("Windows系统通知发送成功")
        else:
            logger.error("Windows系统通知发送失败: {}", err)
        return ok


# provider 白名单：get_notification_from_config 只接受这些类名，避免 globals() 查找误实例化无关对象。
PROVIDER_REGISTRY: Dict[str, type] = {
    provider.__name__: provider for provider in (ServerChan, Qmsg, Bark, Telegram, Windows)
}


# 为了向后兼容，保留原来的Notification类
Notification = DefaultNotification
