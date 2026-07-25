# Phase 8 Atomic Switch — Precise Blocker List (2026-07-25, final)

This document lists every specific code dependency that prevents the Phase 8
atomic switch from being executed safely. It is the result of a full codebase
audit of branch `feature/inference-supervisor-rewrite`.

## Summary

All v2 seams, routing, and adapters are built and tested. **276 Python + 229
.NET tests pass.** Real Paddle OCR is verified end-to-end through the supervisor
(18s, 98% confidence). But **deleting the legacy code would break compilation
and runtime** because 15 source files still import legacy types as their
fallback path, and several operations have **no v2 supervisor endpoint yet**.

## Remaining .NET IWorkerHostClient dependencies (10 files)

These files import `IWorkerHostClient`/`SharedPayloadRef`/`RpcMethods`:

1. **`App.xaml.cs`** — owns `_workerGateway` (DeferredWorkerHostClient) + `_inferenceGateway`. Both are constructed; worker is the live production path; inference is deferred.
2. **`BatchViewModel.cs`** — `StartLegacyAsync` (fallback) + `ExportAsync`/`ExportAllAsync` (no v2 endpoint yet).
3. **`PdfViewModel.cs`** — `OpenAsync`/`RenderThumbnailAsync`/`RotateAsync`/`DeletePagesAsync`/`DeleteTextLayersAsync`/`SaveAsync` + `StartOcrLegacyAsync` (fallback). **PDF session/render/save have NO v2 supervisor endpoint.**
4. **`QrCodeViewModel.cs`** — `DecodeLegacyAsync`/`GenerateLegacyAsync` (fallback) + `ReleaseGeneratedImage` (payload lifecycle).
5. **`QrCodeCommands.cs`** — `QrCodeSaveCommands` uses `IWorkerHostClient.ReadPayload` to save generated images. **No v2 endpoint for reading payloads.**
6. **`RecognitionViewModel.cs`** — `StartAsync` (legacy fallback) + `CreateResultActions` passes `_worker` to `ResultActions`.
7. **`ResultActions.cs`** — legacy `ExportAsync` fallback via `worker.CallAsync(ExportOcr)`. v2 routing exists but falls back when unattached.
8. **`SettingsViewModel.cs`** — `LoadSnapshotLegacyAsync` + `SwitchBackendAsync` (no v2 switch-backend endpoint).
9. **`DeferredInferenceClient.cs`** — delegates to inner `IInferenceClient`.
10. **`QrCodePage.xaml.cs`** — constructs `QrCodeSaveCommands(_workerGateway, ...)`.

## Remaining Python get_backend_client dependencies (5 files)

1. **`subprocess_manager.py`** — manages the WorkerHost subprocess lifecycle.
2. **`export_jobs.py`** — export workers call `get_backend_client()` for sync export.
3. **`batch_recognition_tab.py`** — `BatchBackendAdapter` + `partition_batches` legacy path.
4. **`qrcode_tab.py`** — QR decode/generate/save legacy path.
5. **`single_recognition_tab.py`** — legacy OCR path (image decode + sync recognize).

## What MUST be built before Phase 8 can execute

### Missing v2 supervisor endpoints (Python)
- **PDF session operations**: `/v2/pdf/sessions/open|render|rotate|delete|save` — the plan §6 calls for these to be proxied through the supervisor. Currently the UI talks directly to the PDF backend child process.
- **Batch export**: `/v2/export` exists for single results but `ExportAllAsync` loops over items (could reuse the single endpoint).
- **Settings switch-backend**: `settings.switch_backend` has no v2 equivalent.

### Missing .NET v2 methods
- `IQrCodeClient.ReadPayloadAsync` or equivalent for `QrCodeSaveCommands`.
- PDF session methods on `IInferenceClient` or a new `IPdfSessionClient`.
- `ISettingsClient.SwitchBackendAsync` or equivalent.

### Then the atomic switch can proceed:
1. Delete §5.3 modules (worker_host, v1 protocol, SharedPayload, NamedPipe, VIBEOCR_OCR_TRANSPORT, monkey patches).
2. Call `_inferenceGateway.Attach(real client)` at supervisor startup.
3. Call `_workerGateway` → remove entirely.
4. Promote scoped guards to repo-wide bans.
5. Update packaging, CI, release docs.

## Estimate

Building the missing endpoints + wiring them: ~3-5 days of focused work.
The atomic delete+switch itself: ~1 day.
Phase 9 (benchmarks/gates): ~5-10 days.
Phase 10 (release/rollback): ~2-4 days + observation.
