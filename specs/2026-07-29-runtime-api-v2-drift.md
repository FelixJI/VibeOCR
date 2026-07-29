# Runtime API v2 drift report

Generated from commit: `4a1f498867fd0c20b5c4e22b27a3e88fcea6c72b`

The historical `openapi.snapshot.json` is **non-authoritative**. This report compares its operation surface with `create_app(...).openapi()` from the real Backend without starting OCR providers.

## Summary

| Metric | Count |
|---|---:|
| Historical operations | 16 |
| Actual Backend operations | 35 |
| Python Runtime Client operations | 22 |
| C# Next Runtime Client operations | 14 |
| Shared operations | 8 |
| Actual-only operations | 27 |
| Snapshot-only operations | 8 |
| Client-only operations | 0 |
| Backend operations not observed in either client | 10 |
| Operations without a concrete response content schema | 0 |
| Duplicate generated operation IDs | 0 |

## Actual-only operations

| Method | Path |
|---|---|
| `GET` | `/v2/jobs/{job_id}/observe` |
| `GET` | `/v2/pdf/sessions/{session_id}/render` |
| `POST` | `/v2/export` |
| `POST` | `/v2/jobs` |
| `POST` | `/v2/jobs/command` |
| `POST` | `/v2/pdf/sessions/open` |
| `POST` | `/v2/pdf/sessions/{session_id}/add_text_layer` |
| `POST` | `/v2/pdf/sessions/{session_id}/add_text_layer_batch` |
| `POST` | `/v2/pdf/sessions/{session_id}/cancel` |
| `POST` | `/v2/pdf/sessions/{session_id}/close` |
| `POST` | `/v2/pdf/sessions/{session_id}/delete_pages` |
| `POST` | `/v2/pdf/sessions/{session_id}/delete_text_layers` |
| `POST` | `/v2/pdf/sessions/{session_id}/detect_text_layers` |
| `POST` | `/v2/pdf/sessions/{session_id}/insert_blank` |
| `POST` | `/v2/pdf/sessions/{session_id}/insert_from` |
| `POST` | `/v2/pdf/sessions/{session_id}/load` |
| `POST` | `/v2/pdf/sessions/{session_id}/model` |
| `POST` | `/v2/pdf/sessions/{session_id}/move_page` |
| `POST` | `/v2/pdf/sessions/{session_id}/render_preview` |
| `POST` | `/v2/pdf/sessions/{session_id}/render_thumbnail` |
| `POST` | `/v2/pdf/sessions/{session_id}/reorder` |
| `POST` | `/v2/pdf/sessions/{session_id}/reset_cancel` |
| `POST` | `/v2/pdf/sessions/{session_id}/rewrite_text_layer` |
| `POST` | `/v2/pdf/sessions/{session_id}/rotate` |
| `POST` | `/v2/pdf/sessions/{session_id}/save` |
| `POST` | `/v2/pdf/sessions/{session_id}/save_transactional` |
| `POST` | `/v2/pdf/sessions/{session_id}/update_block_text` |

## Snapshot-only operations

| Method | Path |
|---|---|
| `DELETE` | `/v2/jobs/{job_id}` |
| `GET` | `/v2/jobs/{job_id}/events` |
| `GET` | `/v2/jobs/{job_id}/result` |
| `POST` | `/v2/jobs/pdf-ocr` |
| `POST` | `/v2/jobs/recognition` |
| `POST` | `/v2/jobs/{job_id}/cancel` |
| `POST` | `/v2/jobs/{job_id}/retry` |
| `PUT` | `/v2/runtime/residency` |

## Client-only operations

| Method | Path |
|---|---|
| — | — |

## Backend operations not observed in either client

| Method | Path |
|---|---|
| `POST` | `/v2/pdf/sessions/{session_id}/add_text_layer` |
| `POST` | `/v2/pdf/sessions/{session_id}/delete_text_layers` |
| `POST` | `/v2/pdf/sessions/{session_id}/insert_blank` |
| `POST` | `/v2/pdf/sessions/{session_id}/insert_from` |
| `POST` | `/v2/pdf/sessions/{session_id}/load` |
| `POST` | `/v2/pdf/sessions/{session_id}/move_page` |
| `POST` | `/v2/pdf/sessions/{session_id}/reorder` |
| `POST` | `/v2/pdf/sessions/{session_id}/rewrite_text_layer` |
| `POST` | `/v2/pdf/sessions/{session_id}/save_transactional` |
| `POST` | `/v2/pdf/sessions/{session_id}/update_block_text` |

## Untyped actual operations

| Method | Path |
|---|---|
| — | — |

## Consequence

Phase 1 must define stable explicit `operationId`, request/response/error schemas, multipart/binary/NDJSON examples and golden cases for the actual surface. It must not copy the historical snapshot.
