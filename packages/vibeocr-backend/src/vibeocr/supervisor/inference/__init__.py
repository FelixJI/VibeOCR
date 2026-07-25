"""Inference adapters and scheduling.

Phase 3 adds :class:`DeviceScheduler`, :class:`BudgetPlanner`,
:class:`RecoveryPolicy` and :class:`ResidencyManager`. Phase 4/5 add the
Paddle and MinerU concrete adapters. Phase 2 only needs the
:class:`~vibeocr.supervisor.module.Executor` seam (defined in module.py).
"""

from __future__ import annotations
