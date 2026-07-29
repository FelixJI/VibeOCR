# `openapi.snapshot.json` status

**HISTORICAL / NON-AUTHORITATIVE**

This file records the pre-split supervisor snapshot and is retained only as migration evidence. It is known to differ from the real Backend and both clients, including jobs routes and untyped PDF/export operations.

The authoritative VibeOCR Local Runtime API v2.0.0 specification is
`openapi.yaml`; its frozen release baseline is
`baselines/openapi-2.0.0.yaml`. Downstream SDK and compatibility claims must
be generated from that baseline, never from this historical snapshot.

Run `python scripts/report_runtime_api_v2_drift.py` to compare this historical file with the actual FastAPI application surface.
