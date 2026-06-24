"""系统 CJK 字体探测 + fontTools 子集化。

为 PDF 文字层提供可嵌入的子集字体：按本页实际用到的字符做子集化，
生成临时小字体文件，PyMuPDF 嵌入后自动生成 ToUnicode CMap，
使文字层在所有主流阅读器可搜索/复制（不依赖阅读器自带 Adobe GB1 CMap）。

跨平台探测系统 CJK 字体，无需随包分发字体。探测失败时返回 None，
调用方回退 china-s（当前行为）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class CjkFontResolver:
    """系统 CJK 字体探测 + fontTools 子集化。

    进程级单例：通过模块级 `_CJK_RESOLVER` 实例访问，避免重复探测。
    子集字体按字符集 hash 缓存到临时目录，相同字符集复用。
    """

    # 跨平台候选优先级（复用 qrcode_service._load_font 的模式）
    _WIN_CANDIDATES = [
        "C:/Windows/Fonts/msyh.ttc",  # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",  # 黑体
        "C:/Windows/Fonts/simsun.ttc",  # 宋体
        "C:/Windows/Fonts/Deng.ttf",  # 等线
    ]
    _MAC_CANDIDATES = [
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Songti.ttc",
    ]
    _LINUX_CANDIDATES = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
    ]

    def __init__(self) -> None:
        self._system_font: str | None = None  # 探测缓存（None 表示已探测且无）
        self._probed: bool = False  # 是否已探测过
        self._subset_cache: dict[frozenset[str], str] = {}  # 字符集 → 子集路径

    @property
    def _candidates(self) -> list[str]:
        """按平台返回候选字体路径列表。"""
        if sys.platform == "win32":
            return self._WIN_CANDIDATES
        if sys.platform == "darwin":
            return self._MAC_CANDIDATES
        return self._LINUX_CANDIDATES

    def _get_candidates(self) -> list[str]:
        """按平台返回候选字体路径列表（可被测试 monkeypatch 覆盖）。

        注意：用方法而非 property，因为 property 无 setter 无法被
        monkeypatch.setattr 覆盖。_find_system_font 调用此方法。
        """
        return self._candidates

    def _find_system_font(self) -> str | None:
        """探测首个存在的系统 CJK 字体（结果缓存）。"""
        if self._probed:
            return self._system_font
        for path in self._get_candidates():
            if Path(path).is_file():
                self._system_font = path
                break
        self._probed = True
        if self._system_font is None:
            logger.warning(
                "[CjkFontResolver] 未找到系统 CJK 字体，文字层将回退 china-s"
            )
        return self._system_font
