"""VibeOCR 应用服务边界（UI-free）。

此包定义 application 层：UI 只能调用 facade；facade 只依赖 dataclass/Protocol
和现有 service，不发 Qt signal，不接触 widget。导入此包不加载 PySide6。

可被 WorkerHost（Phase 1）和 WinUI 壳（Phase 2+）共享。
"""

from __future__ import annotations

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
