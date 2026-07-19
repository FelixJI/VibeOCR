"""PySide 冻结入口标准流编码门禁。"""

from __future__ import annotations

from vibeocr import main as main_module


class _FakeStream:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def reconfigure(self, **kwargs: str) -> None:
        self.calls.append(kwargs)


def test_configure_standard_streams_forces_utf8(monkeypatch) -> None:
    stdout = _FakeStream()
    stderr = _FakeStream()
    monkeypatch.setattr(main_module.sys, "stdout", stdout)
    monkeypatch.setattr(main_module.sys, "stderr", stderr)

    main_module._configure_standard_streams()

    expected = [{"encoding": "utf-8", "errors": "replace"}]
    assert stdout.calls == expected
    assert stderr.calls == expected


def test_configure_standard_streams_tolerates_missing_stream(monkeypatch) -> None:
    monkeypatch.setattr(main_module.sys, "stdout", None)
    monkeypatch.setattr(main_module.sys, "stderr", object())

    main_module._configure_standard_streams()
