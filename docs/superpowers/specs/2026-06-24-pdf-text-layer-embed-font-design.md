# PDF 文字层字体内嵌修复设计

**日期**: 2026-06-24
**状态**: 设计待评审
**关联**: 用户报告"添加文字层后保存，其他软件搜不到文字"

## 1. 背景与问题

PDF 处理标签页"添加文字层"功能写入的隐形文字层（`render_mode=3`），
**在 PyMuPDF 自身（`fitz.get_text()`）能正常提取，但在外部阅读器
（浏览器、macOS Preview、部分手机阅读器、pdftotext 等）无法搜索/复制**。

### 根因（已通过原始结构解剖确认）

`PdfService._write_blocks_to_page` (`pdf_service.py:417`) 使用 PyMuPDF 内置
CJK 字体 `china-s` 写入文字：

```python
page.insert_textbox(rect, block.text, fontname="china-s", ...)
```

生成的 PDF 字体对象结构：

```
/Subtype /Type0  /BaseFont /Heiti  /Encoding /UniGB-UTF16-H
DescendantFont → /CIDFontType0  /CIDSystemInfo (Adobe/GB1)
FontDescriptor → 无 /FontFile2   ← 字形数据未嵌入
Type0 → 无 /ToUnicode            ← Unicode 反向映射缺失
```

`china-s` 是 PyMuPDF 的内置 CJK 字体，依赖 Adobe 标准 CMap `UniGB-UTF16-H`。
这要求**阅读器本地装有 Adobe GB1 CMap 资源**才能把字形 ID 反向映射回 Unicode：

- **PyMuPDF**：内置映射 → `get_text()` 永远能读 → 现有 e2e 测试全绿（反而掩盖问题）
- **Adobe Reader / Foxit**：通常内置，可能能搜
- **浏览器 / macOS Preview / 手机阅读器 / pdftotext 部分版本**：不带 GB1 反向 CMap → **搜索/复制全部失效**

### WPS 参考文件对比

用户提供的 WPS 添加文字层文件（`发货单扫描件(OCR)_扫描版-测试.pdf`）
使用**同类非内嵌技巧**：`SimSun` + `GBK-EUC-H`，同样无 FontFile 无 ToUnicode。
WPS 文件还添加了**两次**（每页 F0+F1 两套重叠文字层，各约 70 条文本）。

WPS 这类方案同样依赖阅读器自带 CMap，**跨阅读器支持度不一，是脆弱方案**。

### 业界标准方案

原生文字版 PDF（Word 导出/打印）天生**带嵌入字体 + ToUnicode CMap**，
这是 PDF 规范保证可搜索的标准机制。OCR 文字层只要复刻此机制，
即可达到与原生文字版 PDF **完全等同的可搜索性**。

## 2. 目标与非目标

### 目标

- 文字层在**所有主流阅读器**可搜索/复制（Adobe、Foxit、浏览器、macOS Preview、pdftotext）
- 通过嵌入字体子集 + ToUnicode CMap 实现可搜索性
- **PDF 体积增量可忽略**（< 50KB/份，非整字体嵌入）
- 跨平台（Windows/macOS/Linux）字体探测，无需随包分发字体
- 探测失败时优雅回退（保持当前 china-s 行为，不阻断流程）

### 非目标

- 不解决"文字层添加多次叠加"问题（用户习惯/防重复守卫已存在 `add_text_layer` 的 overwrite 逻辑）
- 不改变隐形文字层 `render_mode=3` 策略
- 不修改 OCR 识别逻辑或坐标映射（经 ground-truth 往返测试验证，旋转坐标数学**正确**）

## 3. 关键技术验证（均已实测）

| 验证项 | 结果 |
|---|---|
| china-s 写入：ToUnicode/FontFile | 均缺（根因） |
| 嵌入整字体（msyh.ttc）体积 | +10.9 MB（不可接受） |
| 嵌入整字体（simhei.ttf）体积 | +3.5 MB（不可接受） |
| **fontTools 子集化（17字）** | **9,517KB → 5.3KB**（减少 1800 倍） |
| 子集化字体嵌入 PDF | ToUnicode=有, FontFile=有, 增量≈7KB |
| 跨阅读器可搜索性 | 嵌入字体+ToUnicode = 原生文字版 PDF 同等 |

## 4. 设计

### 4.1 架构概览

```
OCR 完成 → add_text_layer / rewrite_text_layer
         → _write_blocks_to_page
              1. 收集本页所有文本块字符集
              2. CjkFontResolver.resolve(chars) → 子集字体路径（或 None）
              3. 若拿到子集字体：insert_textbox(fontfile=subset_path, ...)
                 否则：回退 china-s（当前行为）
```

核心思想：**在写入文字层前，按本页实际用到的字符做 fontTools 子集化，
生成临时小子集字体文件，传给 `insert_textbox` 的 `fontfile` 参数**。
PyMuPDF 嵌入子集字体时自动生成 FontFile2 + ToUnicode。

### 4.2 组件：`CjkFontResolver`（新增，`utils/cjk_font_resolver.py`）

职责：
1. **探测系统 CJK 字体**（跨平台，单例缓存路径）
2. **子集化字体**（按字符集生成临时 TTF，缓存复用）

```python
class CjkFontResolver:
    """系统 CJK 字体探测 + fontTools 子集化。

    职责单一：给一组字符，返回一个可嵌入 PDF 的子集字体文件路径。
    探测结果按进程缓存；子集字体按字符集 hash 缓存到临时目录。
    """

    # 跨平台字体优先级（复用 qrcode_service._load_font 的模式）
    _WIN_CANDIDATES = [
        "C:/Windows/Fonts/msyh.ttc",    # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",  # 黑体
        "C:/Windows/Fonts/simsun.ttc",  # 宋体
        "C:/Windows/Fonts/Deng.ttf",    # 等线
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
        self._subset_cache: dict[frozenset[str], str] = {}  # 字符集→子集路径

    def _get_candidates(self) -> list[str]:
        """按平台返回候选字体路径列表（可被测试覆盖）。"""
        if sys.platform == "win32":
            return self._WIN_CANDIDATES
        if sys.platform == "darwin":
            return self._MAC_CANDIDATES
        return self._LINUX_CANDIDATES

    def resolve(self, chars: str) -> str | None:
        """返回覆盖 chars 的子集字体路径；探测失败返回 None。

        Args:
            chars: 本页文字层需要的所有字符。
        Returns:
            子集字体临时文件路径，或 None（探测失败，调用方回退 china-s）。
        """
        if not chars:
            return None
        sys_font = self._find_system_font()
        if sys_font is None:
            return None
        key = frozenset(chars)
        if key not in self._subset_cache:
            self._subset_cache[key] = self._subset(sys_font, chars)
        return self._subset_cache[key]

    def _find_system_font(self) -> str | None:
        """探测首个存在的系统 CJK 字体（结果缓存）。

        通过 _get_candidates() 取平台候选列表（可被测试覆盖），
        返回第一个 Path.is_file() 的路径。探测结果缓存到 _system_font。
        """
        if self._probed:
            return self._system_font
        for p in self._get_candidates():
            if Path(p).is_file():
                self._system_font = p
                break
        self._probed = True
        if self._system_font is None:
            logger.warning(
                "[CjkFontResolver] 未找到系统 CJK 字体，文字层将回退 china-s"
            )
        return self._system_font

    @staticmethod
    def _subset(orig_path: str, chars: str) -> str:
        """fontTools 子集化到临时文件，返回路径。

        .ttc（字体集合）需 fontNumber=0 取第一个 face；
        .ttf 直接打开。populate(text=...) 含 notdef/必需字形的自动闭包。
        """
        from fontTools import subset
        from fontTools.ttLib import TTFont
        import tempfile, os
        is_ttc = orig_path.lower().endswith(".ttc")
        font = TTFont(orig_path, fontNumber=0) if is_ttc else TTFont(orig_path)
        sub = subset.Subsetter()
        sub.populate(text=chars)
        sub.subset(font)
        fd, path = tempfile.mkstemp(suffix=".ttf", prefix="vibeocr_subset_")
        os.close(fd)
        font.save(path)
        return path
```

**设计要点**：
- **进程级单例**：`_write_blocks_to_page` 通过模块级实例访问，避免重复探测
- **字符集缓存**：同一份文档多页用相同字符集时复用子集文件（OCR 修正场景常见）
- **临时文件清理**：进程退出时清理（`atexit` 或 PdfService 关闭钩子）
- **纯函数友好**：便于单元测试（mock `_find_system_font`）

### 4.3 修改：`PdfService._write_blocks_to_page`

在写入前收集字符、解析子集字体，传给 `insert_textbox`：

```python
@staticmethod
def _write_blocks_to_page(doc, page_index, text_blocks, preproc_angle, settings):
    page = doc[page_index]
    page_rect = page.rect

    # 新增：收集本页字符，解析子集字体（探测失败则 None，回退 china-s）
    all_chars = "".join(b.text for b in text_blocks if b.text)
    font_path = _CJK_RESOLVER.resolve(all_chars)  # None 或子集路径
    fontname = "F0" if font_path else "china-s"

    written = skipped = 0
    for block in text_blocks:
        # ...（bbox 解析、rect 计算、fontsize 等既有逻辑不变）...
        for _ in range(settings.font_size_retry_count):
            rc = page.insert_textbox(
                rect, block.text, fontsize=fontsize,
                fontname=fontname,
                fontfile=font_path,   # 新增：None 时 PyMuPDF 用内置字体
                color=(0, 0, 0), render_mode=render_mode,
            )
            # ...（rc 判断、fontsize 缩放重试既有逻辑不变）...
        if not inserted:
            # insert_text 兜底分支同样改用 fontfile
            page.insert_text(baseline, block.text, fontsize=last_fontsize,
                fontname=fontname, fontfile=font_path, ...)
```

**关键**：`fontfile=None` 时 PyMuPDF 回退内置字体行为不变，保证探测失败的兼容性。

### 4.4 模块级单例与清理

```python
# pdf_service.py 顶部
from vibeocr.utils.cjk_font_resolver import CjkFontResolver

_CJK_RESOLVER = CjkFontResolver()
```

清理：`CjkFontResolver` 注册 `atexit` 钩子删除临时子集文件，或在
`PdfSessionManager.shutdown` 时调用 `_CJK_RESOLVER.cleanup()`。

```python
def cleanup(self) -> None:
    """删除所有缓存的子集临时文件（进程退出或 session 关闭时调用）。"""
    import os
    for path in self._subset_cache.values():
        try:
            os.remove(path)
        except OSError:
            pass
    self._subset_cache.clear()
```

`.ttc` 字体索引注意：fontTools 的 `TTFont(fontNumber=0)` 取集合中第一个
face（通常是 Regular）。若探测到的 .ttc 第一个 face 不含所需字形（极罕见），
subset 会抛错——由 `_write_blocks_to_page` 的现有异常处理兜底，回退 china-s。

## 5. 测试策略

### 5.1 单元测试（`tests/utils/test_cjk_font_resolver.py`）

- `test_find_system_font_returns_path_on_windows`（mock 平台 + 字体存在）
- `test_subset_reduces_size`（子集后远小于原字体）
- `test_subset_cache_reuses_same_charset`（相同字符集返回同路径）
- `test_resolve_returns_none_when_no_font`（mock 无字体，优雅降级）
- `test_resolve_returns_none_for_empty_chars`

### 5.2 集成测试（`tests/integration/test_pdf_text_layer_e2e.py` 扩展）

**关键：现有测试用 `fitz.get_text()` 验证，掩盖了问题。新增跨阅读器视角测试：**

- `test_text_layer_has_tounicode_cmap`：保存后检查 PDF 原始字节含 `ToUnicode`
- `test_text_layer_has_embedded_font`：检查 FontDescriptor 含 FontFile2
- `test_text_layer_searchable_without_reader_cmap`：用 `pikepdf` 或原始字节
  验证不依赖阅读器自带 CMap 也能反查 Unicode（模拟外部阅读器）
- `test_volume_increase_acceptable`：子集化后 PDF 增量 < 50KB
- `test_fallback_to_china_s_when_no_system_font`：mock 探测失败，仍能写入（china-s）

### 5.3 手动验证清单

- [ ] 生成测试 PDF，在 Chrome/Edge、Adobe Reader、macOS Preview、Foxit 分别验证搜索
- [ ] 对比修复前后 PDF 文件大小
- [ ] 旋转文档（90/180/270）文字层位置正确（坐标数学已验证正确）

## 6. 风险与权衡

| 风险 | 缓解 |
|---|---|
| fontTools 子集化耗时 | 单页字符集小（通常 < 500 字），子集化 < 100ms；字符集缓存避免重复 |
| 临时文件泄漏 | atexit 钩子 + shutdown 清理；子集文件按字符集 hash 命名可幂等 |
| 系统无 CJK 字体（罕见） | 优雅回退 china-s（当前行为），不阻断；日志告警 |
| .ttc 字体子集化 | fontTools 支持 .ttc；需指定 face index（默认 0） |
| 不同机器字形不同 | 对隐形文字层（render_mode=3）零影响（不渲染字形） |

## 7. 实现顺序（建议）

1. `CjkFontResolver` + 单元测试（先 TDD，验证探测与子集化）
2. 修改 `_write_blocks_to_page` 接入 resolver
3. 扩展集成测试（跨阅读器视角断言）
4. 手动验证 + 清理钩子

## 8. 涉及文件

- **新增**: `src/vibeocr/utils/cjk_font_resolver.py`
- **新增**: `tests/utils/test_cjk_font_resolver.py`
- **修改**: `src/vibeocr/services/pdf_service.py`（`_write_blocks_to_page` + 模块级 resolver）
- **扩展**: `tests/integration/test_pdf_text_layer_e2e.py`（跨阅读器断言）
