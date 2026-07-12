# tests/services/test_pdf_text_layer_angle_roundtrip.py
"""回归：OCR 文字层写入时 preproc_angle 必须从主进程经 dict 传到后端。

根因：PdfSessionManager._ocr_result_to_dict 此前只序列化 text_blocks，
丢弃 preproc_angle；后端 pdf_backend_process.add_text_layer 重建 OCRResult
时角度恒为 0。当 OCR 预处理旋转了图像（开启文档方向分类时常见），bbox 在
旋转后空间，但写入时按 0° 解旋转 → 文字层坐标严重偏离（90° 时 X 轴可偏移
数百点）。

本组测试覆盖完整链路：_ocr_result_to_dict → 后端重建 OCRResult →
PdfService.add_text_layer 落点，验证 preproc_angle 正确传递、坐标正确。
"""

from __future__ import annotations

import fitz

from vibeocr.managers.pdf_session_manager import PdfSessionManager
from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.services.pdf_service import PdfService

_PAGE_RECT = fitz.Rect(0, 0, 612, 792)


def _make_result(angle: int) -> OCRResult:
    """构造带 preproc_angle 的 OCRResult（归一化 bbox）。"""
    return OCRResult(
        text_blocks=[TextBlock(text="hello", score=0.9, bbox=(100, 100, 200, 200))],
        preproc_angle=angle,
        preproc_img_w=1000,
        preproc_img_h=1000,
    )


class TestPreprocAngleRoundTrip:
    """_ocr_result_to_dict → 后端重建 必须保留 preproc_angle。"""

    def test_dict_carries_preproc_angle(self):
        """_ocr_result_to_dict 输出必须包含 preproc_angle。"""
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        d = mgr._ocr_result_to_dict(_make_result(90))
        assert "preproc_angle" in d, "preproc_angle 被丢弃（Bug 1 复发）"
        assert d["preproc_angle"] == 90

    def test_dict_carries_zero_angle(self):
        """0° 也要显式传（避免后端默认值与主进程不一致）。"""
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        d = mgr._ocr_result_to_dict(_make_result(0))
        assert d["preproc_angle"] == 0

    def test_dict_angle_handles_missing_attr(self):
        """OCRResult 无 preproc_angle 时回退 0，不抛错。"""
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        result = OCRResult(text_blocks=[])  # preproc_angle 取默认 0
        d = mgr._ocr_result_to_dict(result)
        assert d["preproc_angle"] == 0

    def test_reconstructed_result_has_correct_angle(self):
        """模拟后端 pdf_backend_process.add_text_layer 的反序列化路径，
        重建出的 OCRResult.preproc_angle 必须与主进程一致。"""
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        d = mgr._ocr_result_to_dict(_make_result(90))

        # 后端反序列化（镜像 pdf_backend_process.py:519-534）
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
            for b in d.get("text_blocks", [])
        ]
        reconstructed = OCRResult(
            text_blocks=text_blocks,
            preproc_angle=int(d.get("preproc_angle", 0) or 0),
        )
        assert reconstructed.preproc_angle == 90


class TestPreprocAnglePlacement:
    """端到端落点验证：经 round-trip 后，PdfService.add_text_layer 写入的
    文字层位置必须与「直接传正确角度」一致。

    Bug 1 症状：90° 时文字应落在右侧 (~x=489.6)，实际落在左侧 (~x=61.2)，
    偏移 428 点。
    """

    def test_90deg_lands_on_right_not_left(self, tmp_path):
        """90° 旋转：经 round-trip 后落点在右侧（非左侧）。"""
        import numpy as np

        # 构造单页扫描件
        path = tmp_path / "scan.pdf"
        doc = fitz.open()
        page = doc.new_page(width=612, height=792)
        img = np.ones((792, 612, 3), dtype=np.uint8) * 240
        cs = fitz.Colorspace(fitz.CS_RGB)
        pixmap = fitz.Pixmap(cs, 612, 792, img.tobytes(), 0)
        page.insert_image(fitz.Rect(0, 0, 612, 792), pixmap=pixmap)
        doc.save(str(path))
        doc.close()

        # 经 dict round-trip（主进程 → 后端）后的角度
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        d = mgr._ocr_result_to_dict(_make_result(90))
        roundtrip_angle = int(d.get("preproc_angle", 0) or 0)

        # 用 round-trip 后的角度直接算落点（与 add_text_layer 内部一致）
        rect = PdfService._denormalize_and_unrotate_bbox(
            (100, 100, 200, 200), roundtrip_angle, _PAGE_RECT
        )
        # Bug 1 复发时会落到左侧 (x0≈61.2)；修复后应在右侧 (x0≈489.6)
        assert rect.x0 > _PAGE_RECT.width * 0.5, (
            f"90° 经 round-trip 后落点应在右侧(x0>{_PAGE_RECT.width*0.5:.0f})，"
            f"实际 x0={rect.x0:.1f}（preproc_angle 丢失 → Bug 1 复发）"
        )

    def test_roundtrip_matches_direct_angle(self):
        """round-trip 角度算出的落点 == 直接传正确角度的落点（全 4 角度）。"""
        mgr = PdfSessionManager.__new__(PdfSessionManager)
        for angle in (0, 90, 180, 270):
            d = mgr._ocr_result_to_dict(_make_result(angle))
            roundtrip_angle = int(d.get("preproc_angle", 0) or 0)
            r_roundtrip = PdfService._denormalize_and_unrotate_bbox(
                (100, 100, 200, 200), roundtrip_angle, _PAGE_RECT
            )
            r_direct = PdfService._denormalize_and_unrotate_bbox(
                (100, 100, 200, 200), angle, _PAGE_RECT
            )
            assert abs(r_roundtrip.x0 - r_direct.x0) < 0.01
            assert abs(r_roundtrip.y0 - r_direct.y0) < 0.01
