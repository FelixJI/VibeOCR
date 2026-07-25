# Inference Supervisor Rewrite — Completion-State Audit (2026-07-25)

This document is an honest audit of where the unified-inference-supervisor
rewrite (`specs/2026-07-24-inference-supervisor-rewrite-plan.md`, Phases 0–10)
stands on branch `feature/inference-supervisor-rewrite`. It exists so the next
session has a precise hand-off: what is done, what blocks the atomic switch,
and the concrete remaining steps. It is **not** a claim that the objective is
complete — the plan's §10 Definition of Done is NOT met.

## TL;DR

The rewrite is at **"v2 seam built and wired, not yet the default execution
path."** All v2 contracts, the supervisor engine, the adapters (as seams),
both front-end client seams, the legacy guards, the settings migration, and
the production-factory wiring are in place and tested. **The atomic switch
cannot yet be flipped** because (a) every front-end ViewModel still uses the
legacy worker as its *primary* path (v2 paths are coexisting opt-in methods),
(b) QrCodeViewModel has no v2 path (blocked on a supervisor interface
extension), and (c) the supervisor's Paddle/MinerU/PDF adapters are seams that
do not yet drive the real model/child-process lifecycle in production. Flipping
the switch now would delete code the app still depends on for every operation.

## What is DONE (with evidence)

### Phase 0 — Baseline + ADR
- `specs/2026-07-24-inference-supervisor-adr.md` (supersedes the 2026-07-14 WorkerHost ADR).
- `tests/fixtures/inference/manifest.json` (fixed test corpus manifest).

### Phase 1 — HTTP v2 contracts (Python + .NET, golden-parity)
- Python: `packages/vibeocr-contracts-py/src/vibeocr/protocol/v2/` (DTOs, errors, strict parser, JSON Schema, OpenAPI snapshot, golden fixtures). 49 Python contract tests green.
- .NET: `src/dotnet/VibeOCR.Contracts/HttpV2/` (enums, records, source-gen `HttpV2JsonContext`). `tests/dotnet/VibeOCR.Contracts.Tests/HttpV2GoldenContractTests.cs` proves Python↔.NET `JsonNode.DeepEquals` on 9 fixtures + 16-code error registry.
- **Exit criterion "Python 与 C# golden 100% 一致": MET.**

### Phase 2 — Supervisor bootstrap + job engine
- `packages/vibeocr-backend/src/vibeocr/supervisor/` (bootstrap, auth, app, composition, module, jobs/{registry,staging,retention}, main entry point).
- Python client `packages/vibeocr-client-py/src/vibeocr/supervisor/` (client, process, job_handle, errors, contracts).
- Thread-safe `JobRecord` (per-record lock + retention hook), honest cancel state machine (cancel_requested → cancelled), bounded shutdown, concurrency tests.
- E2E tests via httpx ASGI transport (fake executor): submit→events→result→cancel.

### Phase 3 — Scheduling / budgets / recovery / residency
- `inference/{scheduler,budgets,recovery,residency}.py` with deterministic fake-clock/fake-GPU tests (30 tests). Single-heavy GPU lease, priority aging, transport/compute budget split, OOM/坏输入/transient recovery, TTL/pin/LRU/capacity.

### Phase 4–6 — Adapter seams (Paddle / MinerU / PDF)
- `inference/paddle_adapter.py`, `inference/mineru_adapter.py`, `pdf/adapter.py` — **seams only**. They expose the right interfaces (`recognize_many`, unique-stem multi-file, transactional save) but the concrete driving of `OCRService`/MinerU-API/PyMuPDF-child is stubbed. See "Blocks" below.

### Phase 7A — PySide Qt-safe adapter
- `apps/vibeocr-pyside/src/vibeocr/pyside/supervisor_adapter.py` (`SupervisorClientAdapter` QObject, typed signals, generation guard, shutdown). Single/Batch tabs have `recognize_via_supervisor` / `submit_batch_via_supervisor` coexisting methods. 14 PySide tests green.

### Phase 7B — WinUI client + 4/5 ViewModel migration
- `src/dotnet/VibeOCR.Platform/Inference/` (`IInferenceClient`, `InferenceHttpClient`, `InferenceClientException`, `InferenceSupervisorProcess`).
- Migrated ViewModels (coexisting v2 methods): Recognition, Batch, Settings, Pdf. QrCodeViewModel NOT migrated (interface gap).
- .NET tests: 209 green (App 132 + Contracts 25 + Platform 52).

### Phase 8 prep
- Legacy guards `tests/architecture/test_v2_no_legacy_transport.py` (scoped ratchet; negative-tested).
- Settings migration `packages/vibeocr-client-py/src/vibeocr/migration/residency_migration.py` (idempotent, backup).
- Atomic-switch runbook `specs/2026-07-25-phase8-atomic-switch-checklist.md`.
- **Production wiring**: `DeferredInferenceClient` + `App.xaml.cs` factory now constructs the 4 migrated ViewModels with the v2 client reference (deferred — throws until `Attach`). Production still runs the legacy worker path.

### Test totals (this branch)
- Python: 244 tests green (pyside 14 + supervisor 181 + contracts/v2 49 + guards 39 + migration 10 — overlaps at the suite level; new-code coverage ~87%).
- .NET: 209 tests green (App 132 + Contracts 25 + Platform 52).

## What BLOCKS the Phase 8 atomic switch (honest list)

1. **Every ViewModel's primary path is still legacy.** Deleting `worker_host`/`SharedPayloadRef`/`RpcMethods`/`IWorkerHostClient` breaks compilation of `App.xaml.cs`, `BatchViewModel`, `PdfViewModel`, `QrCodeViewModel`/`QrCodeCommands`, `RecognitionViewModel`, `ResultActions`, `SettingsViewModel`. The v2 `*ViaSupervisor*` methods are opt-in, not the default. The switch requires making v2 the default call site in each ViewModel first.
2. **QrCodeViewModel has no v2 path.** QR decode/generate are not recognition jobs; the supervisor exposes no decode/generate endpoint. Requires either a supervisor `IQrCodeClient` seam or dedicated v2 endpoints before QR can leave the legacy worker.
3. **Supervisor adapters are seams, not production-driving.** `PaddlePipelineAdapter.recognize_many` calls `self.service.recognize_batch`, but the composition root (`build_supervisor`) defaults to `_NullExecutor`. `MinerUProcessAdapter`/`PdfProcessAdapter` do not start/stop the real MinerU API / PyMuPDF child in production wiring. Starting the supervisor now yields a job engine that accepts jobs but cannot run real OCR.
4. **PDF text-layer writeback** is lost on the v2 path (it returns text, doesn't write the PDF in place); a supervisor-side PDF-OCR job kind is needed for full parity.
5. **PySide main-loop wiring** of `SupervisorClientAdapter` as the default is not done (adapter exists; tabs call the legacy client by default).
6. **Phase 9/10** (threshold calibration / fault injection / release / rollback drill) are not started — they require real model benchmarking and the release pipeline.

## Concrete next steps (in dependency order)

1. **Make the supervisor actually run OCR.** Wire `build_supervisor` (and `composition.py`) to construct `PaddlePipelineAdapter(service=OCRService())` as the default executor when a backend is available, behind the existing `Executor` seam. Add an integration test that submits a recognition job and gets real text back (local GPU available; gate the heavy model-load test so CI skips it). Do the same for MinerU (start the API subprocess) and PDF (own the existing child).
2. **Make v2 the default ViewModel path** on both front-ends. For WinUI: replace the legacy `StartAsync`/`Recognize*Async` bodies with calls into the v2 methods (remove the legacy call sites), keeping the legacy worker only where the v2 interface genuinely has no equivalent (QR, PDF session/save). For PySide: make `SupervisorClientAdapter` the default backend used by the tabs.
3. **Resolve the QR gap.** Add a supervisor QR decode/generate surface (or `IQrCodeClient`) and migrate `QrCodeViewModel`, OR explicitly scope QR out of the first switched release and document it.
4. **Then** do the atomic delete + flip: in one change, delete §5.3 legacy modules, call `_inferenceGateway.Attach(real client)` at supervisor-process startup, promote the scoped guards to repo-wide bans, update packaging/CI/release. Verify the feature-parity matrix, run soak/benchmark, do the rollback drill.

Steps 1–3 are each multi-day, model-and-UI work. Step 4 is the true atomic switch and is only safe after 1–3.

## Why the atomic switch was NOT done this session

Deleting the legacy worker now would leave both front-ends unable to compile
or run (their primary execution paths use it), and the supervisor cannot yet
execute real OCR (adapter seams only). The plan's own §8 rule — "Phase 8
完成前重写分支不得发布" and "不提供运行时双栈" — means the branch is correctly
in a pre-switch state; the safe, verifiable incremental work (seams, wiring,
guards, migration, runbook) is what has been delivered and tested.
