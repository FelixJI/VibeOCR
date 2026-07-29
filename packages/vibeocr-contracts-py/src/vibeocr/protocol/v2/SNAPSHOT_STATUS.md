# `openapi.snapshot.json` status

**NON-AUTHORITATIVE**

This file records the pre-split supervisor snapshot and is retained only as migration evidence. It is known to differ from the real Backend and both clients, including jobs routes and untyped PDF/export operations.

The authoritative VibeOCR Local Runtime API v2 specification will be `openapi.yaml` in `FelixJI/vibeocr-protocol`. Until that specification is generated and conformance-tested, no downstream repository may publish an SDK or compatibility claim from this snapshot.

Run `python scripts/report_runtime_api_v2_drift.py` to compare this historical file with the actual FastAPI application surface.
