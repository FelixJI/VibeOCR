# VibeOCR repository migration

Development in `FelixJI/VibeOCR` ended after the repository split on
2026-07-29. The source of truth is now:

| Area | Repository | First release | Tag target commit |
|---|---|---:|---|
| Runtime API, schemas, SDKs and golden fixtures | [`FelixJI/vibeocr-protocol`](https://github.com/FelixJI/vibeocr-protocol) | [`v2.0.0`](https://github.com/FelixJI/vibeocr-protocol/releases/tag/v2.0.0) | `94b83e257abf99fb7eaf4b8d2990d040a1c03fa9` |
| UI-free OCR/PDF runtime | [`FelixJI/vibeocr-backend`](https://github.com/FelixJI/vibeocr-backend) | [`v0.7.0`](https://github.com/FelixJI/vibeocr-backend/releases/tag/v0.7.0) | `5434078967ff1ecd47ff4ef8449cea74b6782c9c` |
| PySide desktop product | [`FelixJI/vibeocr-classic`](https://github.com/FelixJI/vibeocr-classic) | [`v0.7.0`](https://github.com/FelixJI/vibeocr-classic/releases/tag/v0.7.0) | `78bcd3d8ee5bd5c702326cc1c0ec8964b321c868` |
| WinUI desktop preview | [`FelixJI/vibeocr-next`](https://github.com/FelixJI/vibeocr-next) | [`v0.1.0-preview.1`](https://github.com/FelixJI/vibeocr-next/releases/tag/v0.1.0-preview.1) | `7bd922e7645b4e74fba9202fce5d9d20d272f991` |

The frozen monorepo implementation baseline is
`2b8c0e5b6443a74221c04c83161aaa9f4d9d62e5`.
Every new repository contains a `MIGRATION.md` that binds its extracted
contents to that commit.

## Source path mapping

| Former monorepo path | New owner |
|---|---|
| `packages/vibeocr-contracts-py` | `vibeocr-protocol` |
| `packages/vibeocr-runtime-client-py` | `vibeocr-protocol` |
| `src/dotnet/VibeOCR.Contracts` | `vibeocr-protocol` |
| `src/dotnet/VibeOCR.Runtime.Client` | `vibeocr-protocol` |
| `packages/vibeocr-backend` | `vibeocr-backend` |
| `apps/vibeocr-pyside` | `vibeocr-classic` |
| `src/dotnet/VibeOCR.Platform` | `vibeocr-next` |
| `src/dotnet/VibeOCR.App` | `vibeocr-next` |
| `src/dotnet/VibeOCR.Bootstrapper` | `vibeocr-next` |

## Upgrade behavior

The migration release in this repository intentionally contains no
installer or application assets. Existing VibeOCR builds do not follow
releases across repositories, so they will not automatically upgrade to
Classic or Next. Download the desired portable release manually:

- Stable desktop product: [VibeOCR Classic releases](https://github.com/FelixJI/vibeocr-classic/releases)
- WinUI preview: [VibeOCR Next releases](https://github.com/FelixJI/vibeocr-next/releases)

Issues and changes for active code belong in the corresponding new
repository. This archived repository remains available only for history
and migration discovery.
