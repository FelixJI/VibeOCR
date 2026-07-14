"""Architecture boundary tests for the dual-frontend exclusive-WorkerHost design.

These tests enforce the dependency direction declared in the ADR
(specs/2026-07-14-dual-frontend-exclusive-workerhost-adr.md):

- PySide UI layer (views/widgets/ui) must NOT import backend packages
  (services/managers/workers/core/models/application/migration) directly.
  A temporary, monotonically-shrinking allowlist is the ratchet.
- Backend packages must NOT import the UI layer.
- WorkerHost must be importable and self-testable without PySide6.
- Protocol method names must agree across contracts/v1 schema, C# RpcMethods,
  and Python PUBLIC_METHODS.
"""
