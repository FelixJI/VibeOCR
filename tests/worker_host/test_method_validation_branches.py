"""method_validation.py 拒绝分支补充测试。

既有 test_method_validation.py 通过 golden.json 覆盖了所有方法的正向校验，
并零散覆盖少量拒绝分支（unknown method、unknown field、batch 长度）。
本文件系统覆盖各校验函数的拒绝路径，提升分支覆盖：

- 类型校验助手：_string/_integer/_boolean/_object/_uuid/_shared_ref
- pipeline_ttls 校验（未知管道名 / 非整数 / 负值 / bool）
- ocr/ocr_batch/ocr_export: pipeline 不支持、options 非对象、image 字段缺失、
  result_count 越界、batch 过大
- pdf 系列：session_id 缺失、page_indices 非法、operation 缺失
- pipeline_cache 系列：loaded_pipelines/released 非字符串数组、results 非法
- settings 系列：backend 非法、preload_pipelines 非字符串数组
- switch_backend / install_dependency 分支
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from vibeocr.worker_host.method_validation import (
    MethodPayloadError,
    validate_method_payload,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = (
    ROOT / "packages" / "vibeocr-contracts-py" / "src" / "vibeocr" / "protocol" / "v1"
)


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    return json.loads((CONTRACTS_DIR / "golden.json").read_text(encoding="utf-8"))


def _positive(golden: dict[str, Any], method: str, direction: str) -> dict[str, Any]:
    """深拷贝 golden 的正向 payload，供测试修改。"""
    key = "request_envelope" if direction == "request" else "response_envelope"
    return copy.deepcopy(
        golden["positive"][method][key][
            "payload" if direction == "request" else "result"
        ]
    )


def _valid_shared_ref() -> dict[str, Any]:
    return {
        "name": "Local\\VibeOCR-00000000-0000-4000-8000-000000000000-00000000-0000-4000-8000-000000000010",
        "size": 100,
        "media_type": "image/png",
        "sha256": "a" * 64,
        "owner": "client",
        "expires_unix_ms": 1800000000000,
    }


# ---------------------------------------------------------------------------
# 类型校验助手（通过 system.ping / ocr 等入口间接覆盖）
# ---------------------------------------------------------------------------


class TestPrimitiveValidators:
    def test_non_object_payload_rejected(self) -> None:
        with pytest.raises(MethodPayloadError, match="must be a JSON object"):
            validate_method_payload("system.ping", "request", "not-a-dict")

    def test_string_must_be_non_empty(self) -> None:
        with pytest.raises(MethodPayloadError, match="non-empty string"):
            validate_method_payload("system.ping", "request", {"nonce": ""})

    def test_string_must_be_string_type(self) -> None:
        with pytest.raises(MethodPayloadError, match="non-empty string"):
            validate_method_payload("system.ping", "request", {"nonce": 123})

    def test_required_field_missing(self) -> None:
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("system.ping", "request", {})

    def test_unknown_direction_rejected(self) -> None:
        with pytest.raises(ValueError, match="direction must be"):
            validate_method_payload("system.ping", "sideways", {})


# ---------------------------------------------------------------------------
# shared_ref 校验（ocr.recognize.image）
# ---------------------------------------------------------------------------


class TestSharedRefValidation:
    def test_shared_ref_invalid_name_namespace(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "request")
        payload["image"]["name"] = "Local\\EvilCo-deadbeef"
        with pytest.raises(MethodPayloadError, match="invalid namespace"):
            validate_method_payload("ocr.recognize", "request", payload)

    def test_shared_ref_invalid_sha256(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "request")
        payload["image"]["sha256"] = "xyz"
        with pytest.raises(MethodPayloadError, match="SHA-256"):
            validate_method_payload("ocr.recognize", "request", payload)

    def test_shared_ref_missing_required_field(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "request")
        del payload["image"]["owner"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("ocr.recognize", "request", payload)

    def test_shared_ref_invalid_owner(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "request")
        payload["image"]["owner"] = "attacker"
        with pytest.raises(MethodPayloadError, match="owner"):
            validate_method_payload("ocr.recognize", "request", payload)

    def test_shared_ref_non_integer_size(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "request")
        payload["image"]["size"] = "big"
        with pytest.raises(MethodPayloadError, match="size"):
            validate_method_payload("ocr.recognize", "request", payload)

    def test_shared_ref_not_object(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "request")
        payload["image"] = "not-a-ref"
        with pytest.raises(MethodPayloadError, match="must be a JSON object"):
            validate_method_payload("ocr.recognize", "request", payload)


# ---------------------------------------------------------------------------
# OCR pipeline / options / batch
# ---------------------------------------------------------------------------


class TestOcrValidation:
    def test_ocr_unsupported_pipeline(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "request")
        payload["pipeline"] = "EVIL_PIPELINE"
        with pytest.raises(MethodPayloadError, match="not supported by protocol"):
            validate_method_payload("ocr.recognize", "request", payload)

    def test_ocr_options_not_object(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "request")
        payload["options"] = ["not", "a", "dict"]
        with pytest.raises(MethodPayloadError, match="options must be an object"):
            validate_method_payload("ocr.recognize", "request", payload)

    def test_ocr_response_unsupported_pipeline(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "response")
        payload["pipeline"] = "FAKE"
        with pytest.raises(MethodPayloadError, match="not supported"):
            validate_method_payload("ocr.recognize", "response", payload)

    def test_ocr_response_raw_blocks_not_array(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "response")
        payload["raw_blocks"] = "not-array"
        with pytest.raises(MethodPayloadError, match="raw_blocks must be an array"):
            validate_method_payload("ocr.recognize", "response", payload)

    def test_ocr_response_text_with_scores_not_array(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "response")
        payload["text_with_scores"] = "x"
        with pytest.raises(
            MethodPayloadError, match="text_with_scores must be an array"
        ):
            validate_method_payload("ocr.recognize", "response", payload)

    def test_ocr_response_image_width_not_integer(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize", "response")
        payload["image_width"] = True  # bool 被拒绝
        with pytest.raises(MethodPayloadError, match="image_width"):
            validate_method_payload("ocr.recognize", "response", payload)

    def test_ocr_batch_too_many_images(self, golden) -> None:
        payload = _positive(golden, "ocr.recognize_batch", "request")
        payload["images"] = [payload["images"][0] for _ in range(65)]
        with pytest.raises(MethodPayloadError, match="between 1 and 64"):
            validate_method_payload("ocr.recognize_batch", "request", payload)


class TestOcrExportValidation:
    def test_export_missing_required_fields(self) -> None:
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("ocr.export", "request", {})

    def test_export_unsupported_format(self, golden) -> None:
        payload = _positive(golden, "ocr.export", "request")
        payload["format"] = "pdf"  # 非法格式
        with pytest.raises(MethodPayloadError, match="unsupported export format"):
            validate_method_payload("ocr.export", "request", payload)


# ---------------------------------------------------------------------------
# PDF 系列
# ---------------------------------------------------------------------------


class TestPdfValidation:
    def test_pdf_missing_session_id(self, golden) -> None:
        payload = _positive(golden, "pdf.close", "request")
        del payload["session_id"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("pdf.close", "request", payload)

    def test_pdf_command_missing_operation(self, golden) -> None:
        payload = _positive(golden, "pdf.command", "request")
        del payload["operation"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("pdf.command", "request", payload)

    def test_pdf_command_empty_operation(self, golden) -> None:
        payload = _positive(golden, "pdf.command", "request")
        payload["operation"] = ""
        with pytest.raises(MethodPayloadError, match="non-empty string"):
            validate_method_payload("pdf.command", "request", payload)

    def test_pdf_rotate_missing_page_indices(self, golden) -> None:
        payload = _positive(golden, "pdf.rotate", "request")
        del payload["page_indices"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("pdf.rotate", "request", payload)

    def test_pdf_rotate_invalid_page_indices(self, golden) -> None:
        payload = _positive(golden, "pdf.rotate", "request")
        payload["page_indices"] = "not-a-list"
        with pytest.raises(MethodPayloadError, match="non-negative integers"):
            validate_method_payload("pdf.rotate", "request", payload)

    def test_pdf_rotate_invalid_angle(self, golden) -> None:
        payload = _positive(golden, "pdf.rotate", "request")
        payload["angle"] = 45
        with pytest.raises(MethodPayloadError, match="angle must be"):
            validate_method_payload("pdf.rotate", "request", payload)

    def test_pdf_delete_pages_missing_page_indices(self, golden) -> None:
        payload = _positive(golden, "pdf.delete_pages", "request")
        del payload["page_indices"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("pdf.delete_pages", "request", payload)

    def test_pdf_add_text_layer_missing_page_index(self, golden) -> None:
        payload = _positive(golden, "pdf.add_text_layer", "request")
        del payload["page_index"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("pdf.add_text_layer", "request", payload)

    def test_pdf_delete_text_layers_missing_page_indices(self, golden) -> None:
        payload = _positive(golden, "pdf.delete_text_layers", "request")
        del payload["page_indices"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("pdf.delete_text_layers", "request", payload)

    def test_pdf_save_requires_session_id(self) -> None:
        # session_id 是必填
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("pdf.save", "request", {})

    def test_pdf_save_invalid_output_path_type(self, golden) -> None:
        payload = _positive(golden, "pdf.save", "request")
        payload["output_path"] = 123  # 非字符串
        with pytest.raises(MethodPayloadError, match="output_path"):
            validate_method_payload("pdf.save", "request", payload)

    def test_pdf_start_ocr_missing_required(self, golden) -> None:
        payload = _positive(golden, "pdf.start_ocr", "request")
        del payload["session_id"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("pdf.start_ocr", "request", payload)


# ---------------------------------------------------------------------------
# pipeline_cache 系列
# ---------------------------------------------------------------------------


class TestPipelineCacheValidation:
    def test_status_loaded_pipelines_not_array(self, golden) -> None:
        payload = _positive(golden, "pipeline_cache.status", "response")
        payload["loaded_pipelines"] = "OCR"
        with pytest.raises(
            MethodPayloadError, match="loaded_pipelines must be an array"
        ):
            validate_method_payload("pipeline_cache.status", "response", payload)

    def test_status_loaded_pipelines_unknown_pipeline(self, golden) -> None:
        payload = _positive(golden, "pipeline_cache.status", "response")
        payload["loaded_pipelines"] = ["EVIL"]
        with pytest.raises(
            MethodPayloadError, match="loaded_pipelines must be an array"
        ):
            validate_method_payload("pipeline_cache.status", "response", payload)

    def test_status_last_used_not_object(self, golden) -> None:
        payload = _positive(golden, "pipeline_cache.status", "response")
        payload["last_used_unix_ms"] = "x"
        with pytest.raises(MethodPayloadError, match="must be a JSON object"):
            validate_method_payload("pipeline_cache.status", "response", payload)

    def test_status_ready_not_boolean(self, golden) -> None:
        payload = _positive(golden, "pipeline_cache.status", "response")
        payload["ready"] = "yes"
        with pytest.raises(MethodPayloadError, match="ready"):
            validate_method_payload("pipeline_cache.status", "response", payload)

    def test_set_ttl_unknown_pipeline_name(self) -> None:
        with pytest.raises(MethodPayloadError, match="未知管道名"):
            validate_method_payload(
                "pipeline_cache.set_ttl",
                "request",
                {"pipeline_ttls": {"EVIL": 10}},
            )

    def test_set_ttl_non_integer_value(self) -> None:
        with pytest.raises(MethodPayloadError, match="必须是整数"):
            validate_method_payload(
                "pipeline_cache.set_ttl",
                "request",
                {"pipeline_ttls": {"OCR": "abc"}},
            )

    def test_set_ttl_negative_value(self) -> None:
        with pytest.raises(MethodPayloadError, match="必须 >= 0"):
            validate_method_payload(
                "pipeline_cache.set_ttl",
                "request",
                {"pipeline_ttls": {"OCR": -1}},
            )

    def test_set_ttl_bool_value_rejected(self) -> None:
        # bool 是 int 子类但应被拒绝
        with pytest.raises(MethodPayloadError, match="必须是整数"):
            validate_method_payload(
                "pipeline_cache.set_ttl",
                "request",
                {"pipeline_ttls": {"OCR": True}},
            )

    def test_release_response_unknown_pipeline(self, golden) -> None:
        payload = _positive(golden, "pipeline_cache.release", "response")
        payload["released"] = ["EVIL"]
        with pytest.raises(MethodPayloadError, match="released must be an array"):
            validate_method_payload("pipeline_cache.release", "response", payload)

    def test_release_request_heavy_only_not_boolean(self, golden) -> None:
        payload = _positive(golden, "pipeline_cache.release", "request")
        payload["heavy_only"] = "yes"
        with pytest.raises(MethodPayloadError, match="heavy_only"):
            validate_method_payload("pipeline_cache.release", "request", payload)

    def test_load_request_pipelines_unknown(self) -> None:
        with pytest.raises(MethodPayloadError, match="pipelines must be an array"):
            validate_method_payload(
                "pipeline_cache.preload",
                "request",
                {"pipelines": ["EVIL"]},
            )

    def test_load_request_pipelines_not_array(self) -> None:
        with pytest.raises(MethodPayloadError, match="pipelines must be an array"):
            validate_method_payload(
                "pipeline_cache.preload",
                "request",
                {"pipelines": "OCR"},
            )

    def test_load_response_results_unknown_pipeline(self, golden) -> None:
        payload = _positive(golden, "pipeline_cache.preload", "response")
        payload["results"] = {"EVIL": True}
        with pytest.raises(MethodPayloadError, match="unknown pipeline result"):
            validate_method_payload("pipeline_cache.preload", "response", payload)

    def test_load_response_results_value_not_boolean(self, golden) -> None:
        payload = _positive(golden, "pipeline_cache.preload", "response")
        payload["results"] = {"OCR": "yes"}
        with pytest.raises(MethodPayloadError, match=r"results\.OCR"):
            validate_method_payload("pipeline_cache.preload", "response", payload)


# ---------------------------------------------------------------------------
# settings 系列
# ---------------------------------------------------------------------------


class TestSettingsValidation:
    def test_response_invalid_backend(self, golden) -> None:
        payload = _positive(golden, "settings.snapshot", "response")
        payload["backend"] = "tpu"
        with pytest.raises(MethodPayloadError, match="backend must be cpu or gpu"):
            validate_method_payload("settings.snapshot", "response", payload)

    def test_response_preload_pipelines_not_strings(self, golden) -> None:
        payload = _positive(golden, "settings.snapshot", "response")
        payload["preload_pipelines"] = [123]
        with pytest.raises(MethodPayloadError, match="preload_pipelines"):
            validate_method_payload("settings.snapshot", "response", payload)

    def test_response_pipeline_ttls_unknown(self, golden) -> None:
        payload = _positive(golden, "settings.snapshot", "response")
        payload["pipeline_ttls"] = {"EVIL": 5}
        with pytest.raises(MethodPayloadError, match="未知管道名"):
            validate_method_payload("settings.snapshot", "response", payload)

    def test_switch_backend_invalid(self) -> None:
        with pytest.raises(MethodPayloadError, match="backend must be cpu or gpu"):
            validate_method_payload(
                "settings.switch_backend", "request", {"backend": "tpu"}
            )

    def test_switch_backend_response_restart_required_not_boolean(self, golden) -> None:
        payload = _positive(golden, "settings.switch_backend", "response")
        payload["restart_required"] = "yes"
        with pytest.raises(MethodPayloadError, match="restart_required"):
            validate_method_payload("settings.switch_backend", "response", payload)

    def test_install_dependency_name_required(self) -> None:
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("settings.install_dependency", "request", {})

    def test_install_dependency_name_empty(self) -> None:
        with pytest.raises(MethodPayloadError, match="non-empty string"):
            validate_method_payload(
                "settings.install_dependency", "request", {"name": ""}
            )

    def test_install_dependency_source_not_string(self) -> None:
        with pytest.raises(MethodPayloadError, match="source"):
            validate_method_payload(
                "settings.install_dependency",
                "request",
                {"name": "x", "source": 123},
            )

    def test_install_dependency_response_installed_not_boolean(self, golden) -> None:
        payload = _positive(golden, "settings.install_dependency", "response")
        payload["installed"] = "yes"
        with pytest.raises(MethodPayloadError, match="installed"):
            validate_method_payload("settings.install_dependency", "response", payload)


# ---------------------------------------------------------------------------
# QR 系列
# ---------------------------------------------------------------------------


class TestQrValidation:
    def test_qr_decode_missing_image(self, golden) -> None:
        payload = _positive(golden, "qrcode.decode", "request")
        del payload["image"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("qrcode.decode", "request", payload)

    def test_qr_decode_response_results_not_object(self, golden) -> None:
        payload = _positive(golden, "qrcode.decode", "response")
        # 查找 results 字段（可能在不同键下）
        # 多数 decode 响应有 results 数组；构造非法格式
        payload["results"] = "x"
        with pytest.raises(MethodPayloadError):
            validate_method_payload("qrcode.decode", "response", payload)

    def test_qr_generate_missing_data(self, golden) -> None:
        payload = _positive(golden, "qrcode.generate", "request")
        del payload["data"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("qrcode.generate", "request", payload)

    def test_qr_generate_svg_missing_data(self, golden) -> None:
        payload = _positive(golden, "qrcode.generate_svg", "request")
        del payload["data"]
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("qrcode.generate_svg", "request", payload)


# ---------------------------------------------------------------------------
# task.cancel / memory.release
# ---------------------------------------------------------------------------


class TestTaskAndMemory:
    def test_cancel_task_id_not_string(self) -> None:
        with pytest.raises(MethodPayloadError, match="task_id"):
            validate_method_payload("task.cancel", "request", {"task_id": 123})

    def test_memory_release_name_not_string(self) -> None:
        with pytest.raises(MethodPayloadError, match="name"):
            validate_method_payload("memory.release", "request", {"name": 123})

    def test_memory_release_missing_name(self) -> None:
        with pytest.raises(MethodPayloadError, match="missing fields"):
            validate_method_payload("memory.release", "request", {})
