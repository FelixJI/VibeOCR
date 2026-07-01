# tests/core/test_pipeline_paddlocr_vl.py
"""PaddleOCR-VL 管道解析回归测试。

回归背景：PaddleX 结果对象是 dict 子类，parsing_res_list/content_list/images
是 dict key 而非实例属性。早期代码用 hasattr(res, ...) + res.xxx 属性访问，
对 dict 子类 hasattr 恒为 False，导致 VL 识别丢块（与表格/公式同一类 bug）。
"""

from vibeocr.core.pipelines.pipeline_paddlocr_vl import (
    PADDLEOCR_VL_SPEC,
    PaddleOCRVLOptions,
    _recognize_paddlocr_vl,
)


class _DictResult(dict):
    """模拟 PaddleX 结果：dict 子类，键需下标访问。"""


class _FakePipeline:
    def __init__(self, result_list):
        self._result_list = result_list

    def predict(self, input, **kwargs):
        return list(self._result_list)


class _FakeService:
    def __init__(self, result_list):
        self._pipeline = _FakePipeline(result_list)

    def get_or_create_pipeline(self, name):
        return self._pipeline


def test_vl_options_defaults():
    opts = PaddleOCRVLOptions()
    assert opts.pipeline == "PaddleOCR-VL"


def test_vl_spec():
    assert PADDLEOCR_VL_SPEC.name == "PaddleOCR-VL"
    assert PADDLEOCR_VL_SPEC.display_name == "文档P（PaddleOCR-VL）"


def test_recognize_vl_extracts_blocks_from_dict_result():
    """回归：parsing_res_list 必须用下标访问（dict 子类）。

    修复前 hasattr(res, "parsing_res_list") 对 dict 子类恒为 False，
    VL 识别所有块被丢弃。
    """
    res = _DictResult(
        {
            "parsing_res_list": [
                {
                    "block_bbox": [10, 20, 100, 50],
                    "block_content": "hello world",
                    "block_label": "text",
                    "block_order": 0,
                },
                {
                    "block_bbox": [10, 60, 100, 90],
                    "block_content": "",  # 空内容应被跳过
                    "block_label": "figure",
                    "block_order": 1,
                },
            ]
        }
    )
    service = _FakeService([res])
    result = _recognize_paddlocr_vl(
        service, image=None, options=PaddleOCRVLOptions()
    )

    assert result.pipeline_type == "PaddleOCR-VL"
    assert len(result.text_blocks) == 1
    assert result.text_blocks[0].text == "hello world"
    assert len(result.content_list) == 1


def test_recognize_vl_extracts_content_list_and_images():
    """content_list / images 同样是 dict key，必须下标访问。"""
    res = _DictResult(
        {
            "parsing_res_list": [],
            "content_list": [{"type": "text", "text": "from cl"}],
            "images": {"img1": b"\x89PNG"},
        }
    )
    service = _FakeService([res])
    result = _recognize_paddlocr_vl(
        service, image=None, options=PaddleOCRVLOptions()
    )

    assert any(c.get("text") == "from cl" for c in result.content_list)
    assert result.images and "img1" in result.images


class _VLBlock:
    """模拟真实 PaddleOCRVLBlock（普通对象，属性访问，非 dict）。

    真实属性：content/label/bbox/global_block_id。早期代码误用
    block.get("block_content")，对非 dict 对象会 AttributeError。
    """

    def __init__(self, content, label="text", bbox=None, global_block_id=-1):
        self.content = content
        self.label = label
        self.bbox = bbox or [10, 20, 100, 50]
        self.global_block_id = global_block_id


def test_recognize_vl_extracts_object_blocks():
    """回归：PaddleOCRVLBlock 是对象（非 dict），必须属性访问。

    修复前 block.get("block_content") 对对象抛 AttributeError，VL 识别
    整体异常被吞 → 返回空。
    """
    res = _DictResult(
        {
            "parsing_res_list": [
                _VLBlock(content="title text", label="title", global_block_id=0),
                _VLBlock(content="", label="figure", global_block_id=1),
            ]
        }
    )
    service = _FakeService([res])
    result = _recognize_paddlocr_vl(
        service, image=None, options=PaddleOCRVLOptions()
    )

    assert len(result.text_blocks) == 1
    assert result.text_blocks[0].text == "title text"
    assert result.text_blocks[0].label == "title"
