"""PDF 后端子进程 — FastAPI 服务,持有所有 fitz.Document + PdfDocument 规范模型。

主进程通过 localhost HTTP 调用本服务。fitz 调用全部收敛在此单进程单线程内,
彻底规避 PyMuPDF 不支持多线程的隐患。

启动方式:python -m vibeocr.services.pdf_backend_process --port <port>
由主进程 PdfBackendClient 托管(端口探测 + JobObjectGuard 孤儿清理 + 崩溃重启)。

设计要点:
- SessionRegistry 按 session_id 持有 (doc, pdf_document, cancel_event)
- 变更操作返回 ModelDiff(增量),主进程 apply 到 mirror
- 长操作(摆正/删除文字层/OCR 写层/保存)走流式进度(chunked line JSON)
- 渲染(缩略图/预览)返回字节流(PNG),主进程构 QPixmap
- 取消:协作式,POST /session/{id}/cancel 设置 event,任务循环检查
"""

from __future__ import annotations

import argparse
import io
import logging
import socket
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

import fitz
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from vibeocr.ipc.schemas import (
    AddTextLayerRequest,
    DeletePagesRequest,
    DetectTextLayersRequest,
    DetectTextLayersResponse,
    EmptyOk,
    HealthResponse,
    InsertBlankRequest,
    InsertFromRequest,
    ModelDiff,
    MovePageRequest,
    MutateResponse,
    OpenRequest,
    OpenResponse,
    PageListRequest,
    PdfDocumentMirror,
    PdfPageInfoMirror,
    ProgressEvent,
    ProgressPhase,
    RenderPreviewRequest,
    RenderThumbnailRequest,
    ReorderRequest,
    RewriteTextLayerRequest,
    RotateRequest,
    SaveRequest,
    SaveResponse,
    TextBlockMirror,
    TextLayerInfoMirror,
    UpdateBlockTextRequest,
)
from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo, TextLayerInfo
from vibeocr.services.pdf_service import PdfService

logger = logging.getLogger(__name__)

# 全局注册表(单进程单例)
_REGISTRY: "SessionRegistry | None" = None


def _get_registry() -> "SessionRegistry":
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = SessionRegistry()
    return _REGISTRY


# ---- 镜像 / diff 构造 ---------------------------------------------------

def _text_layer_to_mirror(tl: TextLayerInfo) -> TextLayerInfoMirror:
    return TextLayerInfoMirror(
        index=tl.index,
        text_preview=tl.text_preview,
        char_count=tl.char_count,
        bbox=tl.bbox,
        color_id=tl.color_id,
    )


def _text_block_to_mirror(b: TextBlock) -> TextBlockMirror:
    return TextBlockMirror(
        text=b.text,
        score=b.score,
        bbox=b.bbox,
        page_idx=b.page_idx,
        is_manually_edited=b.is_manually_edited,
        label=b.label,
        order=b.order,
    )


def _page_to_mirror(info: PdfPageInfo) -> PdfPageInfoMirror:
    return PdfPageInfoMirror(
        page_index=info.page_index,
        rotation=info.rotation,
        has_text_layer=info.has_text_layer,
        text_layers=[_text_layer_to_mirror(t) for t in info.text_layers],
        is_scanned=info.is_scanned,
        rect=info.rect,
        ocr_text_blocks=[_text_block_to_mirror(b) for b in info.ocr_text_blocks],
        ocr_preproc_angle=info.ocr_preproc_angle,
        deskewed=info.deskewed,
    )


def _doc_to_mirror(doc_model: PdfDocument) -> PdfDocumentMirror:
    return PdfDocumentMirror(
        file_path=doc_model.file_path,
        pages=[_page_to_mirror(p) for p in doc_model.pages],
        is_modified=doc_model.is_modified,
        has_structural_change=doc_model.has_structural_change,
        render_dpi=doc_model.render_dpi,
        thumbnail_dpi=doc_model.thumbnail_dpi,
    )


def _diff_full(doc_model: PdfDocument) -> ModelDiff:
    """全量 model diff(打开/结构变更)。"""
    return ModelDiff(full_model=_doc_to_mirror(doc_model), structural_change=True)


def _diff_pages(
    doc_model: PdfDocument,
    pages: list[int],
    *,
    invalidate_thumbnails: list[int] | None = None,
    modified: bool | None = None,
) -> ModelDiff:
    """增量页 diff(内容变更:旋转/加文字层/摆正/编辑块)。"""
    return ModelDiff(
        replaced_pages=[_page_to_mirror(doc_model.pages[p]) for p in pages if 0 <= p < len(doc_model.pages)],
        invalidated_thumbnails=invalidate_thumbnails or [],
        modified_flag=modified,
    )


# ---- Session 注册表 -----------------------------------------------------

@dataclass
class BackendSession:
    session_id: str
    file_path: str
    doc: fitz.Document
    pdf_document: PdfDocument
    cancel_event: threading.Event = field(default_factory=threading.Event)
    # fitz(Document) 非线程安全:并发渲染缩略图时串行化 get_pixmap 等 fitz 调用。
    # 锁粒度仅覆盖 fitz 栅格化,PIL 缩放/PNG 编码在锁外可并行。
    fitz_lock: threading.Lock = field(default_factory=threading.Lock)
    # 文字层后台逐页检测线程(打开后异步跑,逐页发 progress)
    _load_thread: threading.Thread | None = None


class SessionRegistry:
    """session_id → BackendSession。单线程访问(FastAPI 同步路由在线程池跑,
    但 fitz 操作通过 session 内单例串行;真正的并发隔离靠 doc 单线程持有)。"""

    def __init__(self) -> None:
        self._sessions: dict[str, BackendSession] = {}
        self._lock = threading.Lock()

    def add(self, file_path: str) -> BackendSession:
        sid = uuid.uuid4().hex[:16]
        # fitz.open + 占位 PdfDocument(轻量,不逐页读)
        doc, pdf_document = PdfService.open_doc(file_path)
        session = BackendSession(session_id=sid, file_path=file_path, doc=doc, pdf_document=pdf_document)
        with self._lock:
            self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> BackendSession:
        with self._lock:
            s = self._sessions.get(session_id)
        if s is None:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        return s

    def remove(self, session_id: str) -> None:
        with self._lock:
            s = self._sessions.pop(session_id, None)
        if s is not None:
            try:
                s.doc.close()
            except Exception:
                pass

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


# ---- FastAPI 应用 -------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):  # noqa: ARG001
    logger.info("[pdf-backend] 服务启动")
    yield
    # 关闭所有 session
    reg = _get_registry()
    for sid in list(reg._sessions.keys()):  # noqa: SLF001
        reg.remove(sid)
    logger.info("[pdf-backend] 服务关闭")


app = FastAPI(title="VibeOCR PDF Backend", lifespan=_lifespan)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    import os
    return HealthResponse(status="ok", sessions=_get_registry().count(), pid=os.getpid())


@app.post("/session/open", response_model=OpenResponse)
def session_open(req: OpenRequest) -> OpenResponse:
    """打开 PDF,返回 session_id + 占位 model(逐页信息由 /load 异步填充)。"""
    try:
        session = _get_registry().add(req.path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"打开失败: {e}") from e
    return OpenResponse(session_id=session.session_id, model=_doc_to_mirror(session.pdf_document))


@app.post("/session/{sid}/close", response_model=EmptyOk)
def session_close(sid: str) -> EmptyOk:
    _get_registry().remove(sid)
    return EmptyOk()


@app.post("/session/{sid}/model", response_model=PdfDocumentMirror)
def session_model(sid: str) -> PdfDocumentMirror:
    """全量刷新 model(主进程定期校准用)。"""
    s = _get_registry().get(sid)
    return _doc_to_mirror(s.pdf_document)


# ---- 后台逐页文字层检测(打开后流式)----------------------------------

def _detect_one_page(session: BackendSession, i: int) -> PdfPageInfo:
    """检测单页文字层/扫描/几何,写入 session model 并返回 info。"""
    rotation = PdfService.page_rotation(session.doc, i)
    page_rect = PdfService.page_rect(session.doc, i)
    has_text_layer = bool(session.doc[i].get_text("text").strip())
    is_scanned = (
        not has_text_layer and PdfService.is_page_scanned(session.doc, i)
    )
    info = PdfPageInfo(
        page_index=i,
        rotation=rotation,
        has_text_layer=has_text_layer,
        text_layers=[],
        is_scanned=is_scanned,
        rect=page_rect,
    )
    session.pdf_document.pages[i] = info
    return info


@app.post("/session/{sid}/load")
def session_load(sid: str) -> StreamingResponse:
    """流式逐页文字层检测:每完成一页推一条 NDJSON ProgressEvent。

    主进程收到后可立即展示页数 + 占位缩略图(open 已返回页数),
    然后逐页染色文字层状态,无需等全部检测完。

    每行 JSON:
        {"phase":"load","current":N,"total":T,"page_index":I,
         "page_payload":{PdfPageInfoMirror...}}
    末行:{"phase":"load","current":total,"total":total,"message":"done"}
    """
    import json as _json

    s = _get_registry().get(sid)
    total = s.pdf_document.page_count

    def gen():
        for i in range(total):
            if s.cancel_event.is_set():
                break
            try:
                info = _detect_one_page(s, i)
                page_mirror = _page_to_mirror(info)
                ev = ProgressEvent(
                    phase=ProgressPhase.LOAD,
                    current=i + 1,
                    total=total,
                    page_index=i,
                    page_payload=page_mirror.model_dump(mode="json"),
                )
                yield (_json.dumps(ev.model_dump(mode="json")) + "\n").encode()
            except Exception as e:
                logger.error("[pdf-backend] load page %d failed: %s", i, e)
        # 完成哨兵
        done_ev = ProgressEvent(
            phase=ProgressPhase.LOAD, current=total, total=total, message="done"
        )
        yield (_json.dumps(done_ev.model_dump(mode="json")) + "\n").encode()

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ---- 渲染 ---------------------------------------------------------------

@app.post("/session/{sid}/render_thumbnail")
def render_thumbnail(sid: str, req: RenderThumbnailRequest) -> StreamingResponse:
    """渲染缩略图,返回 PNG 字节流。

    直接用 fitz Pixmap → PNG,不经过 QPixmap(后端子进程无 QApplication,
    不能用 PdfService.render_page)。先按 thumbnail_dpi 渲染再缩放到目标尺寸。

    并发安全:fitz(Document) 非线程安全,用 per-session fitz_lock 串行化栅格化;
    PIL 缩放/PNG 编码在锁外,可被多线程并发执行(缩略图并发渲染的提速来源)。
    """
    s = _get_registry().get(sid)
    try:
        # 锁内:fitz 栅格化,取出原始像素字节(fitz 对象锁外不安全)
        with s.fitz_lock:
            page = s.doc[req.page]
            zoom = s.pdf_document.thumbnail_dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            width, height = pix.width, pix.height
            samples = bytes(pix.samples)  # 拷贝,锁外不再触碰 fitz 对象
        # 锁外:PIL 缩放 + PNG 编码(CPU 密集,无 fitz 调用,可并行)
        from PIL import Image
        img = Image.frombytes("RGB", (width, height), samples)
        img.thumbnail((req.size, req.size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return StreamingResponse(io.BytesIO(buf.getvalue()), media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"渲染缩略图失败: {e}") from e


@app.post("/session/{sid}/render_preview")
def render_preview(sid: str, req: RenderPreviewRequest) -> StreamingResponse:
    """渲染预览页,返回 PNG 字节流(直接 fitz Pixmap → PNG)。

    并发安全:与 render_thumbnail 共用 per-session fitz_lock 串行化栅格化;
    PIL/PNG 编码在锁外。OCR 批量渲染会并发调用本接口。
    """
    s = _get_registry().get(sid)
    try:
        # 锁内:fitz 栅格化,取出原始像素字节(fitz 对象锁外不安全)
        with s.fitz_lock:
            page = s.doc[req.page]
            zoom = req.dpi / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            width, height = pix.width, pix.height
            samples = bytes(pix.samples)
        # 锁外:PIL 转 PNG(CPU 密集,无 fitz 调用,可并行)
        from PIL import Image
        img = Image.frombytes("RGB", (width, height), samples)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return StreamingResponse(io.BytesIO(buf.getvalue()), media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"渲染预览失败: {e}") from e


@app.post("/session/{sid}/detect_text_layers", response_model=DetectTextLayersResponse)
def detect_text_layers(sid: str, req: DetectTextLayersRequest) -> DetectTextLayersResponse:
    s = _get_registry().get(sid)
    try:
        layers = PdfService.detect_text_layers(s.doc, req.page)
        # 同步更新 model(主进程下次取 model 时可见)
        if 0 <= req.page < len(s.pdf_document.pages):
            s.pdf_document.pages[req.page].text_layers = layers
            s.pdf_document.pages[req.page].has_text_layer = len(layers) > 0
        return DetectTextLayersResponse(
            text_layers=[_text_layer_to_mirror(t) for t in layers]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"检测文字层失败: {e}") from e


# ---- 变更操作 -----------------------------------------------------------

@app.post("/session/{sid}/rotate", response_model=MutateResponse)
def rotate_pages(sid: str, req: RotateRequest) -> MutateResponse:
    s = _get_registry().get(sid)
    try:
        PdfService.rotate_pages(s.doc, s.pdf_document, req.pages, req.angle)
        return MutateResponse(diff=_diff_pages(
            s.pdf_document, req.pages, invalidate_thumbnails=req.pages, modified=True
        ))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"旋转失败: {e}") from e


@app.post("/session/{sid}/delete_pages", response_model=MutateResponse)
def delete_pages(sid: str, req: DeletePagesRequest) -> MutateResponse:
    s = _get_registry().get(sid)
    try:
        PdfService.delete_pages(s.doc, s.pdf_document, req.pages)
        return MutateResponse(diff=_diff_full(s.pdf_document))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除页失败: {e}") from e


@app.post("/session/{sid}/insert_blank", response_model=MutateResponse)
def insert_blank(sid: str, req: InsertBlankRequest) -> MutateResponse:
    s = _get_registry().get(sid)
    try:
        PdfService.insert_blank_page(s.doc, s.pdf_document, req.after_index, req.width, req.height)
        return MutateResponse(diff=_diff_full(s.pdf_document))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插入空白页失败: {e}") from e


@app.post("/session/{sid}/insert_from", response_model=MutateResponse)
def insert_from(sid: str, req: InsertFromRequest) -> MutateResponse:
    s = _get_registry().get(sid)
    try:
        PdfService.insert_pages_from(s.doc, s.pdf_document, req.source_path, req.after_index)
        return MutateResponse(diff=_diff_full(s.pdf_document))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插入页失败: {e}") from e


@app.post("/session/{sid}/move_page", response_model=MutateResponse)
def move_page(sid: str, req: MovePageRequest) -> MutateResponse:
    s = _get_registry().get(sid)
    try:
        PdfService.move_page(s.doc, s.pdf_document, req.from_index, req.to_index)
        return MutateResponse(diff=_diff_full(s.pdf_document))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"移动页失败: {e}") from e


@app.post("/session/{sid}/reorder", response_model=MutateResponse)
def reorder(sid: str, req: ReorderRequest) -> MutateResponse:
    s = _get_registry().get(sid)
    try:
        PdfService.reorder_pages(s.doc, s.pdf_document, req.new_order)
        return MutateResponse(diff=_diff_full(s.pdf_document))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重排失败: {e}") from e


@app.post("/session/{sid}/add_text_layer", response_model=MutateResponse)
def add_text_layer(sid: str, req: AddTextLayerRequest) -> MutateResponse:
    """写入 OCR 文字层。ocr_result 是序列化的 OCRResult dict。

    简化:主进程负责把 OCRResult 序列化(含 text_blocks),后端反序列化后调
    PdfService.add_text_layer。
    """
    s = _get_registry().get(sid)
    try:
        # 反序列化 OCRResult(主进程传 dict)
        ocr_result_data = req.ocr_result
        text_blocks = [
            TextBlock(
                text=b["text"],
                score=b["score"],
                bbox=tuple(b["bbox"]) if b.get("bbox") else None,
                page_idx=b.get("page_idx"),
                is_manually_edited=b.get("is_manually_edited", False),
                label=b.get("label", "text"),
                order=b.get("order", -1),
            )
            for b in ocr_result_data.get("text_blocks", [])
        ]
        ocr_result = OCRResult(text_blocks=text_blocks)
        PdfService.add_text_layer(
            s.doc, s.pdf_document, req.page, ocr_result,
            pdf_settings=_settings_from_dict(req.pdf_settings),
            overwrite=req.overwrite,
        )
        return MutateResponse(diff=_diff_pages(
            s.pdf_document, [req.page], invalidate_thumbnails=[req.page], modified=True
        ))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"加文字层失败: {e}") from e


@app.post("/session/{sid}/rewrite_text_layer", response_model=MutateResponse)
def rewrite_text_layer(sid: str, req: RewriteTextLayerRequest) -> MutateResponse:
    s = _get_registry().get(sid)
    try:
        blocks = [
            TextBlock(
                text=b.text, score=b.score, bbox=b.bbox,
                page_idx=b.page_idx, is_manually_edited=b.is_manually_edited,
                label=b.label, order=b.order,
            )
            for b in req.text_blocks
        ]
        PdfService.rewrite_text_layer(
            s.doc, s.pdf_document, req.page, blocks, req.preproc_angle,
            pdf_settings=_settings_from_dict(req.pdf_settings),
        )
        return MutateResponse(diff=_diff_pages(
            s.pdf_document, [req.page], modified=True
        ))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"重写文字层失败: {e}") from e


@app.post("/session/{sid}/update_block_text", response_model=MutateResponse)
def update_block_text(sid: str, req: UpdateBlockTextRequest) -> MutateResponse:
    """双击编辑文字块(仅内存模型)。"""
    s = _get_registry().get(sid)
    try:
        info = s.pdf_document.pages[req.page]
        if 0 <= req.block_index < len(info.ocr_text_blocks):
            b = info.ocr_text_blocks[req.block_index]
            if b.text != req.new_text:
                b.text = req.new_text
                b.is_manually_edited = True
                s.pdf_document.is_modified = True
        return MutateResponse(diff=_diff_pages(s.pdf_document, [req.page], modified=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新块文字失败: {e}") from e


# ---- 流式进度操作 -------------------------------------------------------

def _stream_lines(events: list) -> StreamingResponse:
    """把 ProgressEvent 列表序列化为 chunked 一行一 JSON。"""
    def gen() -> AsyncIterator[bytes]:
        import json as _json
        for ev in events:
            yield (_json.dumps(ev.model_dump(mode="json")) + "\n").encode()
    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/session/{sid}/delete_text_layers")
def delete_text_layers(sid: str, req: PageListRequest) -> StreamingResponse:
    """逐页删除文字层,流式进度。"""
    s = _get_registry().get(sid)
    events: list[ProgressEvent] = []
    total = len(req.pages)
    residual_pages: list[int] = []
    for n, page in enumerate(req.pages):
        if s.cancel_event.is_set():
            break
        try:
            has_text = PdfService.page_has_text(s.doc, page)
            if not has_text:
                PdfService.delete_text_layers(s.doc, s.pdf_document, page)
                events.append(ProgressEvent(
                    phase=ProgressPhase.DELETE, current=n + 1, total=total,
                    page_index=page, page_payload=(0, 0, False),
                ))
            else:
                deleted, rounds, residual = PdfService.delete_text_layers(
                    s.doc, s.pdf_document, page
                )
                if residual:
                    residual_pages.append(page)
                events.append(ProgressEvent(
                    phase=ProgressPhase.DELETE, current=n + 1, total=total,
                    page_index=page, page_payload=(deleted, rounds, residual),
                ))
        except Exception as e:
            logger.error("[pdf-backend] delete layer page %d: %s", page, e)
            events.append(ProgressEvent(
                phase=ProgressPhase.DELETE, current=n + 1, total=total,
                page_index=page, page_payload=None,
            ))
    events.append(ProgressEvent(
        phase=ProgressPhase.DELETE, current=total, total=total, message="done",
        page_payload={"residual_pages": residual_pages},
    ))
    return _stream_lines(events)


@app.post("/session/{sid}/save", response_model=SaveResponse)
def save(sid: str, req: SaveRequest) -> SaveResponse:
    """保存(rewrite + 落盘)。doc 可能被 close+reopen 替换。"""
    s = _get_registry().get(sid)
    try:
        result = PdfService.save_with_rewrite(
            s.doc, s.pdf_document, path=req.path,
            pdf_settings=_settings_from_dict(req.pdf_settings),
        )
        # 全量压缩时 doc 被替换
        new_doc = getattr(result, "new_doc", None)
        if new_doc is not None:
            try:
                s.doc.close()
            except Exception:
                pass
            s.doc = new_doc
        saved_path = result.path or s.pdf_document.file_path or ""
        return SaveResponse(path=saved_path, diff=_diff_full(s.pdf_document))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {e}") from e


@app.post("/session/{sid}/deskew", response_model=MutateResponse)
def deskew(sid: str, req: PageListRequest) -> MutateResponse:
    """自动摆正(简化:不在此处跑 OCR 方向检测,需主进程先识别后调 rotate)。

    完整摆正(渲染+OCR方向检测+旋转)需 OCR 服务,放主进程编排:
    主进程 → /render_preview 取图 → OCR 识别得 angle → /rotate 纠正。
    本路由保留供未来后端内部跑 OCR 时扩展。
    """
    raise HTTPException(status_code=501, detail="deskew 由主进程编排(渲染+OCR+旋转三步)")


@app.post("/session/{sid}/cancel", response_model=EmptyOk)
def cancel(sid: str) -> EmptyOk:
    s = _get_registry().get(sid)
    s.cancel_event.set()
    return EmptyOk()


@app.post("/session/{sid}/reset_cancel", response_model=EmptyOk)
def reset_cancel(sid: str) -> EmptyOk:
    s = _get_registry().get(sid)
    s.cancel_event.clear()
    return EmptyOk()


# ---- 辅助 ---------------------------------------------------------------

def _settings_from_dict(d: dict | None):
    """dict → PdfGlobalSettings。"""
    if d is None:
        return None
    from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
    if hasattr(PdfGlobalSettings, "from_dict"):
        return PdfGlobalSettings.from_dict(d)
    return PdfGlobalSettings(**d)


# ---- 入口 ---------------------------------------------------------------

def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="VibeOCR PDF Backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 = 自动选空闲端口")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        # 格式与 OCR worker / 整个子进程统一：YYYY-MM-DD HH:MM:SS [LEVEL] name: msg
        # 主进程 SubprocessLogForwarder 按此格式正则解析、级别还原、转发。
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,  # 主进程读 stdout 转发日志
    )

    port = args.port or _find_free_port()
    # 把选定的端口打到 stdout 第一行,主进程读取
    print(f"VIBEOCR_PDF_BACKEND_PORT={port}", flush=True)
    uvicorn.run(app, host=args.host, port=port, log_level=args.log_level)


if __name__ == "__main__":
    main()
