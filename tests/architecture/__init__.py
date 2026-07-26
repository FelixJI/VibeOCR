"""Architecture boundary tests for the dual-frontend Supervisor design.

These tests enforce the dependency direction declared in the ADR
(specs/2026-07-14-dual-frontend-exclusive-workerhost-adr.md):

- PySide UI layer (views/widgets/ui) must NOT import backend packages
  (services/managers/workers/core/models/application/migration) directly.
  A temporary, monotonically-shrinking allowlist is the ratchet.
- Backend packages must NOT import the UI layer.
- Supervisor must be importable without PySide6.
- Protocol-v2 schemas and golden fixtures must agree across Python and C#.
"""
