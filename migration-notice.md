# Repository migration notice

This release is a migration notice only. **It contains no installer,
portable archive, executable, or other application asset.**

VibeOCR development has moved to four repositories:

- Stable PySide desktop application and downloads:
  [VibeOCR Classic releases](https://github.com/FelixJI/vibeocr-classic/releases)
- WinUI desktop preview:
  [VibeOCR Next](https://github.com/FelixJI/vibeocr-next)
- UI-free local OCR/PDF runtime:
  [vibeocr-backend](https://github.com/FelixJI/vibeocr-backend)
- Runtime API, schemas and SDKs:
  [vibeocr-protocol](https://github.com/FelixJI/vibeocr-protocol)

Existing VibeOCR applications do not discover releases across GitHub
repositories and therefore **will not automatically upgrade** to Classic
or Next. Download the desired portable package manually from the new
product repository.

Development in `FelixJI/VibeOCR` has stopped. The repository is archived
for source history and migration discovery. See
[`MIGRATION.md`](https://github.com/FelixJI/VibeOCR/blob/v0.7.0/MIGRATION.md)
for the exact source-path mapping and first-release commit SHAs.
