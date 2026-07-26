# Phase 8 Atomic Switch — Staging & Verification Checklist

**Status:** staging (Phase 8 not yet executed). This document is the runbook the
final atomic switch must follow, plus the per-item verification evidence.

The plan (`specs/2026-07-24-inference-supervisor-rewrite-plan.md` §8) is explicit:
the production entry point changes **once**, the legacy code is deleted in the same
change, and there is no runtime dual-stack. This checklist tracks each requirement
and links to the artifacts/tests that prove it.

---

## Pre-conditions (already met in earlier phases)

- [x] HTTP v2 Python contracts + parser (Phase 1): `packages/vibeocr-contracts-py/src/vibeocr/protocol/v2/`
- [x] HTTP v2 .NET DTOs + source-gen context + golden parity (Phase 1 .NET): `src/dotnet/VibeOCR.Contracts/HttpV2/`, `tests/dotnet/VibeOCR.Contracts.Tests/HttpV2GoldenContractTests.cs` (Python↔.NET `JsonNode.DeepEquals`, 25 tests green)
- [x] Supervisor bootstrap + job engine + scheduling/residency (Phase 2–3): `packages/vibeocr-backend/src/vibeocr/supervisor/`
- [x] Paddle / MinerU / PDF adapter seams (Phase 4–6): `.../supervisor/inference/`, `.../supervisor/pdf/`
- [x] PySide Qt-safe adapter + single/batch wiring (Phase 7A): `apps/vibeocr-pyside/src/vibeocr/pyside/supervisor_adapter.py`
- [x] WinUI `IInferenceClient` + `InferenceHttpClient` + `InferenceSupervisorProcess` (Phase 7B): `src/dotnet/VibeOCR.Platform/Inference/`

## Phase 7B ViewModel migration status (WinUI)

Each ViewModel got a coexisting v2 path (extra constructor taking `IInferenceClient` + a `*ViaSupervisor*` method), leaving the legacy path intact until the Phase 8 atomic switch. The v2 paths are exercised by dedicated `*SupervisorTests.cs` files with hand-written `FakeInferenceClient` fakes.

- [x] **RecognitionViewModel** — one-element recognition job. `RecognizeViaSupervisorAsync` + `RecognitionViewModelSupervisorTests.cs` (5 tests).
- [x] **BatchViewModel** — one logical job for all inputs (no UI microbatch slicing). `StartViaSupervisorAsync` + `BatchViewModelSupervisorTests.cs` (5 tests).
- [x] **SettingsViewModel** — residency status read via `GetResidencyAsync`. `LoadResidencyViaSupervisorAsync` + `SettingsViewModelSupervisorTests.cs` (5 tests).
- [x] **PdfViewModel** — PDF page OCR as a recognition job (render pages via legacy RPC, submit as multi-element job). `StartOcrViaSupervisorAsync` + `PdfViewModelSupervisorTests.cs` (3 tests). **Known limitation:** the v2 path does NOT write the recognised text back into the PDF text layer (the legacy `StartPdfOcr` RPC does); it exposes per-page text via `PdfPageViewModel.OcrText`. Full text-layer-writeback migration requires a supervisor-side PDF-OCR job kind. Session/render/save/delete operations have no v2 equivalent and stay legacy.
- [ ] **QrCodeViewModel** — **not migrated; requires interface extension.** QR decode (`qrcode.decode`) and generate (`qrcode.generate`) are NOT recognition jobs: decode returns structured `QrCodeResult[]`, generate returns image bytes — neither matches the recognition-job + `ResultEntry.Payload["text"]` shape. Forcing them through `SubmitRecognitionAsync` would run OCR on the QR (wrong output, wrong backend pipeline). Before this ViewModel can migrate, the supervisor must expose either `SubmitDecodeAsync`/`GetDecodeResultAsync` or a separate `IQrCodeClient` seam. Tracked as a Phase 8 prerequisite for full QR parity.

> Net: 4 of 5 WinUI ViewModels migrated (Recognition/Batch/Settings/PDF). QR is blocked on a supervisor interface extension, documented here rather than faked.

## Phase 8 deliverables

### 1. Legacy-architecture guards (ratchet) — DONE

Guards that fail loudly if the new v2 code regresses into legacy transport. Verified
by negative tests (inject a legacy reference → guard fires → restore → green).

- `tests/architecture/test_v2_no_legacy_transport.py`
  - `test_python_v2_tree_has_no_legacy_transport` — scans the v2 protocol/supervisor/PySide-adapter trees for `VIBEOCR_OCR_TRANSPORT`, legacy transport imports (`vibeocr.worker_host`, `vibeocr.ipc`, `vibeocr.services`, `vibeocr.client.batch`, `vibeocr.client.session`), and the legacy sync/SHM client classes.
  - `test_dotnet_v2_tree_has_no_legacy_transport` — scans `HttpV2/` and `Inference/` for `SharedPayloadRef`, `WorkerHostClient`, `RpcEnvelope`, `FrameCodec`, `NamedPipe`, `VIBEOCR_OCR_TRANSPORT`.
  - `test_pyside_supervisor_adapter_does_not_import_legacy_sync_client` — the new adapter must not import the legacy sync backend session.

> These are scoped guards (the legacy symbols still exist in v1 code until §3 below).
> They form the ratchet: once the v2 trees are clean, they can never regress.

### 2. Settings data migration — DONE

One-time conversion of legacy `pipeline_ttls` (ambiguous `0`) to the v2 `residency`
schema (`ttl_seconds|null` + `pinned`), with pre-migration backup + idempotency.

- `packages/vibeocr-client-py/src/vibeocr/migration/residency_migration.py`
- `tests/migration/test_residency_migration.py` (10 tests)
- Key safety property: legacy `0` for an **unknown** pipeline maps to
  `ttl_seconds=null, pinned=false` (inherit), never to a hard pin that could block a
  running task (ADR §8 / `test_legacy_zero_never_silently_becomes_pin_for_unknown_pipeline`).

### 3. Atomic switch (NOT YET DONE — final Phase 8 step)

When the remaining ViewModels are migrated and the supervisor is production-wired,
the following change happens in **one** commit:

- [ ] PySide + WinUI startup entry points switch to `vibeocr-supervisor` (Python) / `InferenceSupervisorProcess` (.NET).
- [ ] Delete the §5.3 legacy modules:
  - Python `packages/vibeocr-*/src/vibeocr/worker_host/` (all)
  - `protocol/v1/` + `docs/protocol/v1.md`
  - `services/ocr_worker_process.py`, `ocr_service_subprocess.py`, `worker_runtime_state.py`, `mineru_runtime_cache.py`, `mineru_batch_service.py`
  - `utils/shared_memory_v2.py`, old `workers/ocr_worker.py`, `workers/batch_queue_manager.py`
  - Python `BackendClient`/`SyncBackendClient`/`OcrHttpClient`
  - .NET `WorkerHostClient.cs`, `SharedPayloadClient.cs`, `FrameCodec.cs`, `RpcEnvelope`, `RpcMethods`, `SharedPayloadRef`
  - PySide `MinerUPreflightWorker`, main-process `MinerUService` bypass, direct `PdfBackendClient` ownership
  - old WorkerHost/SHM/patch tests
- [ ] Remove `VIBEOCR_OCR_TRANSPORT` and all SHM/Named Pipe fallback.
- [ ] Remove runtime monkey-patches and the old TTL-diagnostics workflow + install call.
- [ ] Update Python package descriptions / entry points / README / ADR / dev docs.
- [ ] Complete distribution ownership: pipelines/model runtime move into backend; remove `vibeocr-backend → vibeocr-client-py` reverse dependency.
- [ ] Update PyInstaller hidden imports, wheel staging, WinUI publish, artifact manifest.
- [ ] Update release workflow test selection + protocol asset copy.
- [ ] After deletion, **promote the scoped guards to repo-wide bans** (the legacy
      symbols no longer exist anywhere, so the guards become absolute).

### 4. Post-switch verification gates

- [ ] Legacy symbol scan = zero (repo-wide `VIBEOCR_OCR_TRANSPORT`, `SharedPayloadRef`, `SyncBackendClient`, `WorkerHostClient`, `RpcEnvelope`).
- [ ] Both release artifacts contain only v2/supervisor.
- [ ] Feature-parity matrix (`docs/quality/feature-parity.md`) all PASS with supervisor as the semantic source.
- [ ] Previous-version rollback works using the `.v1.bak` settings backup (run `migrate_settings_file`, then roll back, then start).

---

## Rollback rule (non-negotiable)

The plan forbids any "production can choose old/new" env var or setting. A severe
regression is recovered by **rolling back to the previous release + config backup**,
not by re-enabling legacy transport inside the new version. The settings migration
writes a `.v1.bak` precisely so the previous version can start against the original
config.
