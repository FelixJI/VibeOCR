"""The Phase 0 drift report must include both real client implementations."""

from scripts.report_runtime_api_v2_drift import (
    _operations,
    actual_openapi,
    build_report,
    csharp_client_operations,
    python_client_operations,
)


def test_python_and_csharp_client_surfaces_are_extracted() -> None:
    python = python_client_operations()
    csharp = csharp_client_operations()
    assert ("POST", "/v2/jobs") in python
    assert ("POST", "/v2/jobs") in csharp
    assert ("GET", "/v2/pdf/health") in python
    assert ("POST", "/v2/qrcode/generate") in csharp


def test_report_exposes_client_only_routes() -> None:
    actual = set(_operations(actual_openapi()))
    python = python_client_operations()
    assert ("GET", "/v2/pdf/health") in python - actual
    report = build_report()
    assert "Client-only operations" in report
    assert "`GET` | `/v2/pdf/health`" in report
