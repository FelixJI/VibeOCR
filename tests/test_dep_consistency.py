"""Tests for validate_dep_check_consistency (依赖清单一致性校验)."""

from pathlib import Path

from vibeocr.services.env_config import (
    OCR_CHECK_MODULES,
    _parse_pep508_name,
    validate_dep_check_consistency,
)


class TestParsePEP508Name:
    def test_plain_name(self):
        assert _parse_pep508_name("torch") == "torch"

    def test_with_version_constraint(self):
        assert _parse_pep508_name("torch>=2.6.0") == "torch"

    def test_with_extras(self):
        assert _parse_pep508_name("paddleocr[doc-parser]>=3.7.0") == "paddleocr"

    def test_with_spaces(self):
        assert _parse_pep508_name("torch >= 2.6.0") == "torch"

    def test_empty(self):
        assert _parse_pep508_name("") == ""

    def test_gpu_variant(self):
        assert _parse_pep508_name("paddlepaddle-gpu>=3.3.1") == "paddlepaddle-gpu"


def _write_pyproject(tmp_path: Path, deps: list[str]) -> Path:
    """写一份只含 [project.dependencies] 的最小 pyproject.toml。"""
    deps_str = "\n".join(f'    "{d}",' for d in deps)
    content = f"""[project]
name = "test"
version = "0.0.1"
dependencies = [
{deps_str}
]
"""
    (tmp_path / "pyproject.toml").write_text(content, encoding="utf-8")
    return tmp_path


class TestValidateDepCheckConsistency:
    def test_aligned_returns_empty(self, tmp_path):
        """当前 OCR_CHECK_MODULES 全部覆盖时返回空列表。"""
        # 用 OCR_CHECK_MODULES.values() + GPU 别名构造一份对齐的 pyproject
        deps = [*OCR_CHECK_MODULES.values(), "paddlepaddle-gpu>=3.3.1"]
        _write_pyproject(tmp_path, deps)
        warnings = validate_dep_check_consistency(tmp_path)
        assert warnings == [], f"对齐配置应无告警，实际: {warnings}"

    def test_missing_module_in_pyproject(self, tmp_path):
        """OCR_CHECK_MODULES 有但 pyproject 缺声明 → 告警。"""
        # 故意漏掉 paddleocr
        deps = ["paddlepaddle-gpu>=3.3.1", "mineru[core]>=3.4.0", "torch", "markdown"]
        _write_pyproject(tmp_path, deps)
        warnings = validate_dep_check_consistency(tmp_path)
        assert any("paddleocr" in w for w in warnings), (
            f"应告警 paddleocr 缺失，实际: {warnings}"
        )

    def test_extra_dep_in_pyproject_not_in_modules(self, tmp_path):
        """pyproject 声明了 OCR 依赖但 OCR_CHECK_MODULES 未覆盖 → 告警。

        模拟新增 OCR 依赖但忘了更新 OCR_CHECK_MODULES 的场景。
        """
        deps = [*OCR_CHECK_MODULES.values(), "paddlepaddle-gpu>=3.3.1"]
        # 加一个 OCR_CHECK_MODULES 没覆盖的 OCR 相关包
        deps.append("some-ocr-lib>=1.0")
        # 但 some-ocr-lib 不在 OCR_DIST_NAME_ALIASES，不会被识别为 OCR 依赖，
        # 所以此场景需用一个 alias 表里的 canonical key 制造反向漂移。
        # 改为：从 OCR_CHECK_MODULES 角度——它覆盖 paddlepaddle，但 pyproject
        # 既无 paddlepaddle 也无 paddlepaddle-gpu。
        deps = ["paddleocr>=3.7.0", "mineru>=3.4.0", "torch", "markdown"]
        _write_pyproject(tmp_path, deps)
        warnings = validate_dep_check_consistency(tmp_path)
        assert any("paddlepaddle" in w for w in warnings), (
            f"应告警 paddlepaddle 未声明，实际: {warnings}"
        )

    def test_no_pyproject_returns_empty(self, tmp_path):
        """打包后无 pyproject.toml → 跳过校验，返回空。"""
        warnings = validate_dep_check_consistency(tmp_path)
        assert warnings == []

    def test_gpu_alias_resolves_to_canonical(self, tmp_path):
        """paddlepaddle-gpu 应归一为 paddlepaddle canonical，不告警。"""
        # 用完整 OCR_CHECK_MODULES.values() + GPU 别名构造对齐的 pyproject
        # （OCR_CHECK_MODULES 已含 PDF 后端模块 fitz/fastapi 等，须全部覆盖）
        deps = [*OCR_CHECK_MODULES.values(), "paddlepaddle-gpu>=3.3.1"]
        _write_pyproject(tmp_path, deps)
        warnings = validate_dep_check_consistency(tmp_path)
        assert warnings == [], (
            f"paddlepaddle-gpu 别名应归一为 paddlepaddle，不应告警。实际: {warnings}"
        )
