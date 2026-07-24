"""OCR HTTP Worker 子进程入口（FastAPI + uvicorn）。

迁移自 SHM worker（ocr_worker_process + shared_memory_v2）。本模块是 worker
子进程的 HTTP 入口，主进程经 ``OcrHttpClient``（httpx）调用。

设计要点（见迁移方案）：
- 单进程内直接持有各 service/adapter（OCRService + QR + Settings），无 SHM、
  无半双工协议、无 ready 标志竞态。
- 复用 ``worker_host.composition`` 的 adapter（OcrServiceAdapter / Qr*Adapter /
  JsonSettingsAdapter），把模型对象转成 wire DTO，与原 WorkerHost handler 路径
  完全一致——UI 侧解析逻辑无需改。
- 仿 ``pdf_backend_process.py``：选空闲端口、首行 stdout 打端口、JobObjectGuard
  绑定生命周期、日志格式统一供主进程转发。
- 同步重活（PaddleOCR predict / QR 生成）用 ``asyncio.to_thread`` 包装，不阻塞
  事件循环。
- MinerU 路由**不在此 worker**：主进程直连 mineru-api（保持分流）。

端点对齐 ``sync_client.SyncBackendClient`` 的 25 个 ``*_sync`` 方法（除 PDF 14
个——PDF 后端已是独立 HTTP 子进程，UI 直连它，不经此 worker）。
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from contextlib import asynccontextmanager
from typing import Any

# paddle（cu129）先于 torch（cu126）import 会导致 torch 的 shm.dll 加载失败
# （WinError 127：CUDA 版本冲突）。paddleocr/paddlex 的 import 链会触发 paddle
# import，故必须在任何 paddle 相关 import 之前先 import torch 占位。
try:
    import torch  # noqa: F401  # 占位：让 torch 的 CUDA runtime 先加载
except Exception:
    pass  # 无 torch 环境忽略（CPU-only 部署）

# FastAPI 类型必须在模块级导入：路由参数注解（UploadFile/Form）需被解析成真实
# 类对象，放在函数内导入会让 pydantic 报 "not fully defined"。
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

logger = logging.getLogger(__name__)

# 进程级懒加载：lifespan/首次请求时建好，避免模块导入即加载 PaddleOCR。
_ocr_adapter: Any = None
_qr_decode: Any = None
_qr_generate: Any = None
_qr_generate_svg: Any = None
_settings_adapter: Any = None


# --------------------------------------------------------------------
# adapter 懒加载
# --------------------------------------------------------------------
def _get_ocr_adapter() -> Any:
    global _ocr_adapter
    if _ocr_adapter is None:
        from vibeocr.worker_host.composition import OcrServiceAdapter

        _ocr_adapter = OcrServiceAdapter(_ocr_service_factory())
    return _ocr_adapter


def _ocr_service_factory():
    def factory() -> Any:
        from vibeocr.services.ocr_service import OCRService

        return OCRService()

    return factory


def _get_qr_decode() -> Any:
    global _qr_decode
    if _qr_decode is None:
        from vibeocr.worker_host.composition import QrDecodeAdapter

        def factory() -> Any:
            from vibeocr.services.qrcode_decode_service import QrCodeDecodeService

            return QrCodeDecodeService()

        _qr_decode = QrDecodeAdapter(factory)
    return _qr_decode


def _get_qr_generate() -> Any:
    global _qr_generate
    if _qr_generate is None:
        from vibeocr.worker_host.composition import QrGenerateAdapter

        def factory() -> Any:
            from vibeocr.services.qrcode_service import QrcodeService

            return QrcodeService()

        _qr_generate = QrGenerateAdapter(factory)
    return _qr_generate


def _get_qr_generate_svg() -> Any:
    global _qr_generate_svg
    if _qr_generate_svg is None:
        from vibeocr.worker_host.composition import QrGenerateSvgAdapter

        def factory() -> Any:
            from vibeocr.services.qrcode_service import QrcodeService

            return QrcodeService()

        _qr_generate_svg = QrGenerateSvgAdapter(factory)
    return _qr_generate_svg


def _get_settings_adapter() -> Any:
    global _settings_adapter
    if _settings_adapter is None:
        from vibeocr.app_paths import resolve_app_paths
        from vibeocr.worker_host.composition import JsonSettingsAdapter

        paths = resolve_app_paths()

        def backend_resolver() -> str:
            try:
                from vibeocr.services.env_config import detect_use_gpu

                return "gpu" if detect_use_gpu(paths.project_root) else "cpu"
            except Exception:
                return "cpu"

        _settings_adapter = JsonSettingsAdapter(paths, backend_resolver)
    return _settings_adapter


def _ocr_service():
    """取 OCRService 单例（pipeline cache 操作直接用）。"""
    return _get_ocr_adapter()._get_service()


# --------------------------------------------------------------------
# lifespan
# --------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(app):
    logger.info("[ocr-worker-http] 服务启动")
    yield
    # 关闭：停 PipelineCacheManager 后台 watcher，避免线程泄漏。
    try:
        service = _ocr_adapter._service if _ocr_adapter is not None else None
        if service is not None and hasattr(service, "cache_manager"):
            service.cache_manager.shutdown()
    except Exception:
        logger.debug("[ocr-worker-http] 关闭 cache_manager 失败", exc_info=True)
    logger.info("[ocr-worker-http] 服务关闭")


# --------------------------------------------------------------------
# wire 结果转 dict（对齐 handlers/ocr._result_payload）
# --------------------------------------------------------------------
def _result_to_dict(result: Any) -> dict[str, Any]:
    import dataclasses

    text_blocks = list(getattr(result, "text_blocks", []) or [])
    flat_blocks: list[Any] = []
    for block in text_blocks:
        if dataclasses.is_dataclass(block) and not isinstance(block, type):
            flat_blocks.append(dataclasses.asdict(block))
        else:
            flat_blocks.append(block)
    return {
        "text": result.text,
        "pipeline": result.pipeline,
        "raw_blocks": list(result.raw_blocks),
        "markdown_text": result.markdown_text,
        "html_text": result.html_text,
        "raw_text": result.raw_text or result.text,
        "text_blocks": flat_blocks,
        "text_with_scores": list(result.text_with_scores),
        "content_list": list(result.content_list),
        "image_width": result.image_width,
        "image_height": result.image_height,
        "preproc_angle": result.preproc_angle,
        "preproc_img_w": result.preproc_img_w,
        "preproc_img_h": result.preproc_img_h,
    }


def _cancel():
    from vibeocr.application.contracts import CancelToken

    return CancelToken()


def _run_sync(func, *args):
    """在线程池跑同步 service 调用，避免阻塞事件循环。"""
    import asyncio

    return asyncio.to_thread(func, *args)


# --------------------------------------------------------------------
# FastAPI app
# --------------------------------------------------------------------
def _create_app():
    app = FastAPI(title="VibeOCR OCR Worker (HTTP)", lifespan=_lifespan)

    # ---- health ----
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # ---- OCR ----
    @app.post("/ocr/recognize")
    async def recognize(
        image: UploadFile = File(...),
        pipeline: str = Form("OCR"),
        language: str | None = Form(None),
        options_json: str | None = Form(None),
    ) -> dict[str, Any]:

        from vibeocr.application.contracts import OcrRequest

        image_data = await image.read()
        options = _parse_options(options_json)
        request = OcrRequest(
            image_data=image_data, pipeline=pipeline, language=language, options=options
        )
        try:
            result = await _run_sync(_get_ocr_adapter().recognize, request, _cancel())
        except Exception as exc:
            logger.exception("[ocr-worker-http] recognize 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return _result_to_dict(result)

    @app.post("/ocr/recognize_batch")
    async def recognize_batch(
        images: list[UploadFile] = File(...),
        pipeline: str = Form("OCR"),
        language: str | None = Form(None),
        options_json: str | None = Form(None),
    ) -> dict[str, Any]:
        from vibeocr.application.contracts import OcrRequest

        options = _parse_options(options_json)
        img_bytes_list = [await f.read() for f in images]
        requests = [
            OcrRequest(
                image_data=ib, pipeline=pipeline, language=language, options=options
            )
            for ib in img_bytes_list
        ]
        try:
            results = await _run_sync(
                _get_ocr_adapter().recognize_batch, requests, _cancel()
            )
        except Exception as exc:
            logger.exception("[ocr-worker-http] recognize_batch 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "results": [
                _result_to_dict(r) if r is not None else None for r in results
            ]
        }

    @app.post("/ocr/export")
    async def export_ocr(payload: dict[str, Any]) -> dict[str, Any]:
        from pathlib import Path

        from vibeocr.application.contracts import OcrExportRequest

        request = OcrExportRequest(
            raw_text=str(payload.get("raw_text", "")),
            markdown_text=str(payload.get("markdown_text", "")),
            html_text=str(payload.get("html_text", "")),
            raw_blocks=list(payload.get("raw_blocks", [])),
            output_path=Path(str(payload.get("output_path", ""))),
            format=str(payload.get("format", "")),
            overwrite=bool(payload.get("overwrite", False)),
        )
        try:
            result = await _run_sync(_get_ocr_adapter().export, request, _cancel())
        except Exception as exc:
            logger.exception("[ocr-worker-http] export 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "output_path": str(result.output_path),
            "bytes_written": result.bytes_written,
        }

    # ---- QR ----
    @app.post("/qrcode/generate")
    async def qr_generate(payload: dict[str, Any]) -> Response:
        data = payload.get("data")
        if not isinstance(data, str) or not data:
            raise HTTPException(status_code=400, detail="qrcode.generate requires 'data'")
        fmt = str(payload.get("format", "qrcode"))
        options: dict[str, Any] = {"format": "qr" if fmt == "qrcode" else fmt}
        if fmt == "barcode":
            options["format"] = str(payload.get("barcode_format", "code128")).lower()
        for key in (
            "size", "error_correction", "fg_color", "bg_color", "invert",
            "logo_path", "logo_ratio", "label_text", "label_position",
            "label_font_size",
        ):
            if key in payload:
                options[key] = payload[key]
        try:
            image_bytes = await _run_sync(
                _get_qr_generate().generate, data, options, _cancel()
            )
        except Exception as exc:
            logger.exception("[ocr-worker-http] qr generate 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return Response(content=image_bytes, media_type="image/png")

    @app.post("/qrcode/generate_svg")
    async def qr_generate_svg(payload: dict[str, Any]) -> dict[str, str]:
        data = payload.get("data")
        if not isinstance(data, str) or not data:
            raise HTTPException(
                status_code=400, detail="qrcode.generate_svg requires 'data'"
            )
        options: dict[str, Any] = {}
        for key in ("error_correction", "fg_color", "bg_color"):
            if key in payload:
                options[key] = payload[key]
        try:
            svg = await _run_sync(
                _get_qr_generate_svg().generate_svg, data, options, _cancel()
            )
        except Exception as exc:
            logger.exception("[ocr-worker-http] qr generate_svg 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"svg": svg}

    @app.post("/qrcode/decode")
    async def qr_decode(image: UploadFile = File(...)) -> dict[str, Any]:
        image_data = await image.read()
        try:
            codes = await _run_sync(_get_qr_decode().decode, image_data, _cancel())
        except Exception as exc:
            logger.exception("[ocr-worker-http] qr decode 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "codes": [
                {"data": c["data"], "format": c["format"], "is_url": bool(c.get("is_url"))}
                for c in codes
            ]
        }

    # ---- pipeline cache（直接调 OCRService.cache_manager，不经 adapter）----
    @app.get("/pipeline_cache/status")
    def pipeline_cache_status() -> dict[str, Any]:
        try:
            return dict(_ocr_service().cache_manager.status())
        except Exception as exc:
            logger.exception("[ocr-worker-http] cache_status 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/pipeline_cache/set_ttl")
    def pipeline_cache_set_ttl(payload: dict[str, Any]) -> dict[str, Any]:
        ttls = payload.get("pipeline_ttls")
        if not isinstance(ttls, dict):
            raise HTTPException(
                status_code=400, detail="pipeline_ttls must be an object"
            )
        # 尽力而为：锁被占（预加载/OCR）时返回 updated=False，不抛错。
        updated = bool(_ocr_service().set_pipeline_ttls(dict(ttls)))
        return {"updated": updated, "pipeline_ttls": dict(ttls)}

    @app.post("/pipeline_cache/release")
    def pipeline_cache_release(payload: dict[str, Any]) -> dict[str, Any]:
        heavy_only = bool(payload.get("heavy_only", True))
        released = _ocr_service().release_pipelines(heavy_only=heavy_only)
        return {"released": list(released)}

    @app.post("/pipeline_cache/preload")
    async def pipeline_cache_preload(payload: dict[str, Any]) -> dict[str, Any]:
        pipelines = payload.get("pipelines")
        if not isinstance(pipelines, list):
            raise HTTPException(status_code=400, detail="pipelines must be a list")
        enum_pipelines = _to_pipeline_enums(pipelines)
        if enum_pipelines is None:
            raise HTTPException(status_code=400, detail="pipelines contains unknown name")
        try:
            # OCRService.preload_pipelines_sequential 接收 list[OCRPipeline]（枚举），
            # 非 list[str]。返回 {pipeline_name: success}（name 已是 str）。
            results = await _run_sync(
                _ocr_service().preload_pipelines_sequential, enum_pipelines
            )
        except Exception as exc:
            logger.exception("[ocr-worker-http] preload 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"results": {str(k): bool(v) for k, v in results.items()}}

    @app.post("/pipeline_cache/warmup")
    async def pipeline_cache_warmup(payload: dict[str, Any]) -> dict[str, Any]:
        pipelines = payload.get("pipelines")
        if not isinstance(pipelines, list):
            raise HTTPException(status_code=400, detail="pipelines must be a list")
        enum_pipelines = _to_pipeline_enums(pipelines)
        if enum_pipelines is None:
            raise HTTPException(status_code=400, detail="pipelines contains unknown name")
        try:
            results = await _run_sync(_ocr_service().warmup_pipelines, enum_pipelines)
        except Exception as exc:
            logger.exception("[ocr-worker-http] warmup 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"results": {str(k): bool(v) for k, v in results.items()}}

    # ---- settings ----
    @app.get("/settings/snapshot")
    def settings_snapshot() -> dict[str, Any]:
        try:
            snap = _get_settings_adapter().get_snapshot()
        except Exception as exc:
            logger.exception("[ocr-worker-http] settings snapshot 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {
            "backend": snap.backend,
            "preload_pipelines": list(snap.preload_pipelines),
            "pipeline_ttls": dict(snap.pipeline_ttls),
        }

    @app.post("/settings/switch_backend")
    def switch_backend(payload: dict[str, Any]) -> dict[str, Any]:
        target = payload.get("backend")
        if not isinstance(target, str):
            raise HTTPException(status_code=400, detail="backend must be a string")
        try:
            new_backend = _get_settings_adapter().switch_backend(target)
        except Exception as exc:
            logger.exception("[ocr-worker-http] switch_backend 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"backend": new_backend}

    @app.post("/settings/install_dependency")
    async def install_dependency(payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if not isinstance(name, str):
            raise HTTPException(status_code=400, detail="name must be a string")
        source = payload.get("source")
        try:
            result = await _run_sync(
                _get_settings_adapter().install_dependency,
                name,
                str(source) if source is not None else None,
                _cancel(),
            )
        except Exception as exc:
            logger.exception("[ocr-worker-http] install_dependency 失败")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return dict(result)

    return app


def _parse_options(options_json: str | None) -> dict[str, Any]:
    import json

    if not options_json:
        return {}
    try:
        parsed = json.loads(options_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="options_json must be valid JSON"
        ) from exc
    return parsed if isinstance(parsed, dict) else {}


def _to_pipeline_enums(names: list[Any]) -> list[Any] | None:
    """list[str] → list[OCRPipeline]。含未知名时返回 None（调用方报 400）。

    OCRService.preload_pipelines_sequential / warmup_pipelines 接收
    list[OCRPipeline]（枚举），非 list[str]。
    """
    from vibeocr.core.pipelines import OCRPipeline

    enum_map = {p.value: p for p in OCRPipeline}
    result: list[Any] = []
    for name in names:
        key = str(getattr(name, "value", name))
        if key not in enum_map:
            return None
        result.append(enum_map[key])
    return result


# --------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------
def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="VibeOCR OCR Worker (HTTP)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 = 自动选空闲端口")
    parser.add_argument("--log-level", default="info")
    parser.add_argument(
        "--use-gpu", dest="use_gpu", action="store_true", default=True, help="使用 GPU（默认）"
    )
    parser.add_argument(
        "--no-gpu", dest="use_gpu", action="store_false", help="不使用 GPU"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        # 格式与 PDF 后端 / 整个子进程统一，供主进程 SubprocessLogForwarder 解析。
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    os.environ["VIBEOCR_USE_GPU"] = "true" if args.use_gpu else "false"

    port = args.port or _find_free_port()
    print(f"VIBEOCR_OCR_WORKER_PORT={port}", flush=True)

    import uvicorn

    app = _create_app()
    logger.info(
        "[ocr-worker-http] 启动 @ 127.0.0.1:%s (use_gpu=%s)", port, args.use_gpu
    )
    uvicorn.run(app, host=args.host, port=port, log_level=args.log_level)


if __name__ == "__main__":
    main()
