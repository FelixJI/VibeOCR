# tests/services/test_pdf_service_bbox.py
"""Tests for PDF bbox coordinate inverse transform."""

import fitz

from vibeocr.services.pdf_service import PdfService


# 标准信纸尺寸 page_rect: 612×792 points
_PAGE_RECT = fitz.Rect(0, 0, 612, 792)


class TestDenormalizeAndUnrotateBbox:
    """测试 _denormalize_and_unrotate_bbox 的四种旋转角度。"""

    def test_no_rotation(self):
        """0°：直接映射，无变换。"""
        # bbox 覆盖整个页面 [0, 1000]
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 1000.0, 1000.0), 0, _PAGE_RECT
        )
        assert result.is_empty is False
        assert abs(result.x0 - 0) < 1
        assert abs(result.y0 - 0) < 1
        assert abs(result.x1 - 612) < 1
        assert abs(result.y1 - 792) < 1

    def test_no_rotation_partial(self):
        """0°：部分区域 bbox。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (100.0, 200.0, 500.0, 800.0), 0, _PAGE_RECT
        )
        assert abs(result.x0 - 61.2) < 1  # 100/1000 * 612
        assert abs(result.y0 - 158.4) < 1  # 200/1000 * 792
        assert abs(result.x1 - 306.0) < 1  # 500/1000 * 612
        assert abs(result.y1 - 633.6) < 1  # 800/1000 * 792

    def test_rotation_180_center(self):
        """180°：中心点保持不变。"""
        # bbox 在归一化空间的中心 (500, 500)
        result = PdfService._denormalize_and_unrotate_bbox(
            (450.0, 450.0, 550.0, 550.0), 180, _PAGE_RECT
        )
        cx = (result.x0 + result.x1) / 2
        cy = (result.y0 + result.y1) / 2
        # 页面中心 (306, 396)
        assert abs(cx - 306) < 2
        assert abs(cy - 396) < 2

    def test_rotation_180_corner(self):
        """180°：左上角映射到右下角。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 100.0, 100.0), 180, _PAGE_RECT
        )
        # 原始左上 → 旋转后映射到右下区域
        assert result.x0 > 300  # 应在右半部分
        assert result.y0 > 400  # 应在下半部分

    def test_rotation_90_width_height_swap(self):
        """90°：旋转后宽度方向映射到页面高度方向。"""
        # 窄长条 (归一化 x: 0-100, y: 0-900)
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 100.0, 900.0), 90, _PAGE_RECT
        )
        # 90° 逆变换：y 方向映射到 x 方向，x 方向映射到 y 方向
        # 横向范围 = 0-900/1000*612 → 约 0-550.8
        # 纵向范围 = (1-100/1000)*792 - (1-0/1000)*792 → 约 712.8-792
        assert result.width > result.height  # 旋转后窄条变横条

    def test_rotation_270_width_height_swap(self):
        """270°：与 90° 方向相反。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 100.0, 900.0), 270, _PAGE_RECT
        )
        assert result.width > result.height

    def test_roundtrip_90(self):
        """90° 旋转后完整 bbox 覆盖整个页面。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 1000.0, 1000.0), 90, _PAGE_RECT
        )
        assert abs(result.width - 612) < 2
        assert abs(result.height - 792) < 2

    def test_roundtrip_270(self):
        """270° 旋转后完整 bbox 覆盖整个页面。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (0.0, 0.0, 1000.0, 1000.0), 270, _PAGE_RECT
        )
        assert abs(result.width - 612) < 2
        assert abs(result.height - 792) < 2

    def test_invalid_angle_defaults_to_zero(self):
        """无效角度视为 0°。"""
        result = PdfService._denormalize_and_unrotate_bbox(
            (100.0, 200.0, 500.0, 800.0), 45, _PAGE_RECT
        )
        expected = PdfService._denormalize_and_unrotate_bbox(
            (100.0, 200.0, 500.0, 800.0), 0, _PAGE_RECT
        )
        assert abs(result.x0 - expected.x0) < 1
        assert abs(result.y0 - expected.y0) < 1


class TestBboxToPixel:
    """测试 bbox_to_pixel 的坐标转换。"""

    def test_pdf_points_to_pixel(self):
        """source=pdf：PDF points → pixels。"""
        # 100pt @ 72dpi = 100px；@ 144dpi = 200px
        result = PdfService.bbox_to_pixel(
            (100.0, 100.0, 200.0, 200.0), _PAGE_RECT, render_dpi=72
        )
        assert abs(result[0] - 100) < 0.01
        assert abs(result[2] - 200) < 0.01

        result_144 = PdfService.bbox_to_pixel(
            (100.0, 100.0, 200.0, 200.0), _PAGE_RECT, render_dpi=144
        )
        assert abs(result_144[0] - 200) < 0.01
        assert abs(result_144[2] - 400) < 0.01

    def test_normalized_to_pixel(self):
        """source=normalized：[0,1000] → pixels。"""
        # 完整页面归一化 bbox @ 72dpi 应等于页面尺寸
        result = PdfService.bbox_to_pixel(
            (0.0, 0.0, 1000.0, 1000.0), _PAGE_RECT, render_dpi=72, source="normalized"
        )
        assert abs(result[0] - 0) < 0.01
        assert abs(result[2] - 612) < 0.01
        assert abs(result[3] - 792) < 0.01
