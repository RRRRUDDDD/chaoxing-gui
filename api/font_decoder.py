import re

import api.cxsecret_font as cxfont
from api.exceptions import FontDecodeError
from api.logger import logger


class FontDecoder:
    """超星加密字体解码器。

    用于解码超星平台使用特殊字体加密的内容。调用方自行解析出
    ``<style id="cxSecretStyle">`` 后交给 :meth:`load_style` 构建映射。
    """

    # 正则表达式常量
    FONT_BASE64_PATTERN = r"base64,([\w\W]+?)\'"
    FONT_DATA_URL_PREFIX = "data:application/font-ttf;charset=utf-8;base64,"

    def __init__(self) -> None:
        self.__font_map = None

    def load_style(self, style_text: str) -> None:
        """Build the font map from a style tag that was already parsed."""
        try:
            match = re.search(self.FONT_BASE64_PATTERN, style_text or "")
            if not match:
                raise FontDecodeError("无法从样式标签中提取字体数据")
            font_data_url = self.FONT_DATA_URL_PREFIX + match.group(1)
            self.__font_map = cxfont.font2map(font_data_url)
        except Exception as e:
            logger.warning(f"初始化字体映射失败: {e}")
            self.__font_map = None

    def decode(self, target_str: str) -> str:
        """解码加密字符串。

        Args:
            target_str: 需要解码的加密字符串

        Returns:
            解码后的字符串

        Raises:
            FontDecodeError: 当字体映射未初始化时抛出
        """
        if not self.__font_map:
            raise FontDecodeError("字体映射未初始化，无法解码")

        return cxfont.decrypt(self.__font_map, target_str)
