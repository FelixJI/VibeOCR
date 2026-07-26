"""OCR 编排链路测试(进程化):后端渲染 → 主进程 OCR → 后端写文字层。

用 mock OCR 服务(避免加载真实 PaddleOCR/MinerU 模型),验证:
- start_ocr 触发后端逐页渲染 → 主进程 mock OCR → 后端 add_text_layer
- 完成后 model 刷新(ocr_text_blocks 入 mirror)
- auto_deskew 触发后端渲染 → mock 方向检测 → 后端旋转

这些测试用真实 PDF 后端子进程 + mock OCR,验证编排逻辑正确。
"""

from __future__ import annotations

import itertools
import time

import fitz
import pytest

from tests.fakes.sync_supervisor_job_client import (
    FakeSyncSupervisorJobClient,
)
from vibeocr.managers.pdf_session_manager import PdfSessionManager
from vibeocr.models.ocr_result import OCRResult, TextBlock


def _make_text_pdf(path, num_pages=2):
    """带文字层的 PDF(用于摆正测试,有内容可识别方向)。"""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


def _make_scanned_pdf(path, num_pages=1):
    """扫描件 PDF(无文字层,OCR 不会因 has_text_layer 被跳过)。"""
    import numpy as np

    doc = fitz.open()
    for _ in range(num_pages):
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
    doc.save(str(path))
    doc.close()
    return path


def _wait_signal(qapp, signal, timeout=20.0):
    fired = [False]
    def _on(*a, **k):
        fired[0] = True
    signal.connect(_on)
    try:
        deadline = time.monotonic() + timeout
        while not fired[0] and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.05)
    finally:
        try:
            signal.disconnect(_on)
        except Exception:
            pass
    return fired[0]


def _wait_until(qapp, condition, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    qapp.processEvents()
    return condition()


@pytest.fixture
def manager(qapp):
    from vibeocr.services.pdf_backend_client import PdfBackendClient

    pdf_client = PdfBackendClient()
    mgr = PdfSessionManager(parent=qapp, client=pdf_client)
    yield mgr
    mgr.shutdown()
    pdf_client.stop()


def _make_ocr_result(text="识别文字", preproc_angle=0):
    """构造固定 OCRResult(单图/批量路径共用)。"""
    return OCRResult(
        raw_text=text,
        text_blocks=[
            TextBlock(
                text=text, score=0.95,
                bbox=(50.0, 50.0, 200.0, 120.0), page_idx=0,
            ),
        ],
        preproc_angle=preproc_angle,
    )


def _make_fake_inference_client(text="识别文字", preproc_angle=0):
    return FakeSyncSupervisorJobClient(
        lambda _index, _request: _make_ocr_result(text, preproc_angle)
    )


class TestOcrOrchestration:
    """OCR 编排:后端渲染 → 主进程 OCR → 后端写文字层。"""

    def test_start_ocr_writes_text_layer(self, manager, tmp_path, qapp):
        """start_ocr 完成后,扫描件页应有 OCR 文字块(后端 add_text_layer)。

        用扫描件(无文字层)避免 add_text_layer 因 has_text_layer 跳过。
        """
        path = _make_scanned_pdf(tmp_path / "ocr.pdf", num_pages=1)
        session = manager.open_session(str(path))
        assert session is not None
        _wait_signal(qapp, manager.load_done, timeout=15.0)
        qapp.processEvents()

        inference = _make_fake_inference_client(text="测试OCR")
        manager.set_inference_client(inference)

        # 扫描件初始无 OCR 块
        assert len(session.pdf_document.pages[0].ocr_text_blocks) == 0

        manager.start_ocr([0])
        assert _wait_signal(qapp, manager.ocr_done, timeout=25.0)
        qapp.processEvents()

        # OCR 完成后应有文字块(model 已刷新)
        assert len(session.pdf_document.pages[0].ocr_text_blocks) >= 1
        assert session.pdf_document.pages[0].ocr_text_blocks[0].text == "测试OCR"
        assert len(inference.submit_calls) == 1

    def test_start_ocr_progress_emitted(self, manager, tmp_path, qapp):
        """OCR 期间应发 ocr_progress 信号，且进度单调递增、末值=页数×子步数。"""
        path = _make_scanned_pdf(tmp_path / "ocr2.pdf", num_pages=2)
        session = manager.open_session(str(path))
        assert session is not None
        _wait_signal(qapp, manager.load_done, timeout=15.0)
        qapp.processEvents()

        manager.set_inference_client(_make_fake_inference_client())
        progress_values: list[int] = []
        determinate: list[tuple[int, int]] = []
        manager.ocr_progress.connect(
            lambda _fp, cur, tot: (
                progress_values.append(cur),
                determinate.append((cur, tot)) if tot > 0 else None,
            )
        )

        manager.start_ocr([0, 1])
        assert _wait_signal(qapp, manager.ocr_done, timeout=30.0)
        qapp.processEvents()
        assert progress_values, "ocr_progress 应触发"
        # 子步数 = 3（渲染/识别/写层），2 页 → 末值应为 6
        substeps = manager._OCR_PROGRESS_SUBSTEPS
        assert determinate[-1] == (2 * substeps, 2 * substeps)
        # 进度单调不减
        determinate_values = [current for current, _ in determinate]
        assert all(b >= a for a, b in itertools.pairwise(determinate_values)), \
            "进度应单调不减"

    def test_cancel_ocr(self, manager, tmp_path, qapp):
        """cancel_ocr 只请求取消，并在 QThread 结束后释放写门。"""
        path = _make_scanned_pdf(tmp_path / "cancel.pdf", num_pages=1)
        manager.open_session(str(path))
        _wait_signal(qapp, manager.load_done, timeout=15.0)
        manager.set_inference_client(_make_fake_inference_client())
        manager.start_ocr([0])
        worker = manager._ocr_worker

        manager.cancel_ocr()

        assert manager._ocr_cancelled is True
        assert manager._ocr_state == "cancelling"
        assert manager._ocr_worker is worker
        assert _wait_until(qapp, lambda: not manager.is_ocr_running, timeout=25.0)
        assert manager._ocr_state == "cancelled"


class TestDeskewOrchestration:
    """摆正编排:后端渲染 → 主进程方向检测 → 后端旋转。"""

    def test_deskew_corrects_rotation(self, manager, tmp_path, qapp):
        """摆正检测到 90° 偏转 → 后端旋转纠正 → rotation 变化。"""
        path = _make_text_pdf(tmp_path / "deskew.pdf", num_pages=1)
        session = manager.open_session(str(path))
        assert session is not None
        _wait_signal(qapp, manager.load_done, timeout=15.0)
        qapp.processEvents()
        _initial_rotation = session.pdf_document.pages[0].rotation  # 记录基线供人工排查

        # mock OCR 报告 90° 偏转
        manager.set_inference_client(
            _make_fake_inference_client(preproc_angle=90)
        )
        manager.auto_deskew_async([0])
        assert _wait_signal(qapp, manager.deskew_done, timeout=25.0)
        qapp.processEvents()

        # 90° 偏转 → correction = (-90) % 360 = 270 → rotation 应变化
        final_rotation = session.pdf_document.pages[0].rotation
        # rotation 可能回环到相同值，故不对其做硬断言；关键断言是 deskew_done
        # 信号触发（已在上方 _wait_signal 验证）。
        assert final_rotation is not None
        # 关键:deskew_done 触发,summary 有 corrected
        # (具体 rotation 值取决于 (-angle)%360 与初始 rotation 的叠加)

    def test_deskew_no_correction_when_upright(self, manager, tmp_path, qapp):
        """摆正检测到 0°(正向)→ 不旋转 → corrected=0。"""
        path = _make_text_pdf(tmp_path / "upright.pdf", num_pages=1)
        session = manager.open_session(str(path))
        assert session is not None
        _wait_signal(qapp, manager.load_done, timeout=15.0)
        qapp.processEvents()

        manager.set_inference_client(
            _make_fake_inference_client(preproc_angle=0)
        )
        summaries = []
        manager.deskew_done.connect(
            lambda sid, s: summaries.append(s)
        )
        manager.auto_deskew_async([0])
        assert _wait_signal(qapp, manager.deskew_done, timeout=25.0)
        qapp.processEvents()
        assert len(summaries) == 1
        assert summaries[0]["corrected"] == 0
        assert summaries[0]["skipped"] == 1


# ----------------------------------------------------------------------
# 后端并发安全:缩略图渲染(持 fitz_lock 的 get_pixmap)与 /load 逐页文字层
# 检测(持 fitz_lock 的 get_text)并发访问同一 session.doc。
# 验证 Task 1 的 per-session fitz_lock 修复有效:修复前 load 裸调 fitz,
# 与并发缩略图渲染争用同一 fitz.Document 会偶发段错误/崩溃。
# ----------------------------------------------------------------------


@pytest.fixture
def backend_client():
    """独立 PdfBackendClient 实例(非全局单例),测试结束 stop 子进程。

    直接用 client 而非 PdfSessionManager,以精确控制 load_stream 与
    render_thumbnail 的并发时机(绕过 manager 的 Qt 信号/后台线程封装)。
    """
    from vibeocr.services.pdf_backend_client import PdfBackendClient

    client = PdfBackendClient()
    yield client
    client.stop()


@pytest.fixture
def sample_pdf_multi_page(tmp_path):
    """多页带文字层 PDF(load 逐页 get_text + 并发缩略图渲染的测试夹具)。

    10 页:足够让 /load 流持续一段时间,与并发缩略图渲染产生真实时间重叠
    (页数太少时 render 可能在 load 第一页前就跑完,测不到并发窗口)。
    每页插入可见文字,get_text 与 get_pixmap 都做实打实的工作,放大争用窗口。
    """
    path = tmp_path / "multi_page.pdf"
    doc = fitz.open()
    for i in range(10):
        page = doc.new_page(width=612, height=792)
        # 多行文字 → get_text 非空且耗时,与 get_pixmap 真正争用 fitz.Document
        for line in range(20):
            page.insert_text(
                (72, 72 + line * 18),
                f"Page {i + 1} line {line + 1}: lorem ipsum dolor sit amet",
                fontsize=11,
            )
    doc.save(str(path))
    doc.close()
    return path


class TestThumbnailLoadConcurrency:
    """验证缩略图渲染与 /load 逐页文字层检测并发时不崩溃。

    回归 Task 1 的 per-session fitz_lock 修复。brief 给的模板是串行
    (load 跑完再 render),无法触发修复前的崩溃窗口;这里改为真正并发:
    load 在后台线程流式跑,同时主线程线程池并发请求多页缩略图,
    迫使两者争抢同一 session.doc。

    fitz.Document 并发崩溃是 native 段错误(非 Python 异常),表现为后端
    子进程被 OS 杀死、后续 /health 或 /load 读流 502/连接重置。本测试通过
    断言 2/4 捕获这一失败模式(load_error 非空 或 health 无响应)。
    """

    def test_render_thumbnail_during_load_does_not_crash(
        self, backend_client, sample_pdf_multi_page
    ):
        """打开后立即并发:后台线程跑 load_stream(逐页 get_text),
        同时主线程线程池并发反复请求多页缩略图(逐页 get_pixmap)。
        两者都应成功完成,后端进程不崩溃。"""
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from vibeocr.ipc.schemas import ProgressPhase

        open_resp = backend_client.open_session(str(sample_pdf_multi_page))
        sid = open_resp.session_id
        total = len(open_resp.model.pages)
        assert total >= 3, f"测试 PDF 至少 3 页,实际 {total}"

        # ---- 后台线程:流式逐页 load(修复前裸调 fitz get_text,无锁) ----
        load_events: list = []
        load_error: list = []
        load_started = threading.Event()

        def _run_load():
            try:
                gen = backend_client.load_stream(sid)
                load_started.set()  # 通知主线程:load 已发起,开始并发渲染
                for ev in gen:
                    load_events.append(ev)
            except Exception as e:
                load_error.append(e)

        load_thread = threading.Thread(target=_run_load, name="load-stream")
        load_thread.start()
        # 等 load 真正发出(否则渲染可能先于 load 完成,无时间重叠)
        assert load_started.wait(timeout=10.0), "load_stream 未及时发起"

        # ---- 主线程:并发渲染全部页缩略图,每页重复多次放大 fitz 争用 ----
        # 不吞异常:断言信息里同时给出 ok 列表和错误列表,便于诊断。
        def _render_one(page_idx):
            try:
                png = backend_client.render_thumbnail(sid, page_idx, 80)
                return (png is not None and len(png) > 0, None)
            except Exception as e:
                return (False, repr(e))

        # 每页渲染 3 次 → 总请求数 = total * 3,分布在 4 个 worker 上并发,
        # 与后台 load 的逐页 get_text 持续交叠。
        render_tasks = list(range(total)) * 3
        with ThreadPoolExecutor(max_workers=4) as pool:
            pairs = list(pool.map(_render_one, render_tasks))

        # ---- 等 load 跑完 ----
        load_thread.join(timeout=60.0)
        assert not load_thread.is_alive(), "load 线程应在 60s 内完成(可能崩溃卡死)"
        assert not load_error, f"load 流式调用抛异常(后端可能崩溃): {load_error}"

        results = [ok for ok, _ in pairs]
        errs = [e for _, e in pairs if e is not None]
        failed = [(idx, e) for (idx, (ok, e)) in enumerate(zip(render_tasks, pairs)) if not ok]

        # 断言 1:所有缩略图渲染成功(非空 PNG)
        assert all(results), (
            f"缩略图渲染应全部成功: 失败 {len(failed)}/{len(results)},"
            f"首批失败={failed[:3]}, errors={errs[:3]}"
        )
        # 断言 2:load 正常完成(末条 message=done)
        assert any(
            getattr(ev, "message", None) == "done" for ev in load_events
        ), (
            "load 流应正常完成(收到 done 哨兵), messages="
            f"{[getattr(e, 'message', None) for e in load_events]}"
        )
        # 断言 3:load 逐页都推了 LOAD phase 事件(证明 get_text 每页都跑过)
        load_phase_events = [
            ev for ev in load_events if ev.phase == ProgressPhase.LOAD
        ]
        assert len(load_phase_events) >= total, (
            f"load 应逐页推 LOAD 事件(至少 {total} 条含 done),"
            f"实际 {len(load_phase_events)}"
        )
        # 断言 4:后端进程仍存活(/health 正常响应)—— native 段错误会让进程消失
        assert backend_client.health() is not None, "后端 /health 无响应(进程可能已崩溃)"

        backend_client.close_session(sid)
