# Changelog

All notable changes to LithoMask are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-11-17

First public, open-source release.

### Added
- PySide6 mask layout editor: rectangle, polygon, circle, ellipse, ring, wire, text and array tools; move / rotate / scale / mirror /
  offset; align and distribute; boolean operations; cells with instances; layer table; numeric property editing; 1,000-step undo/redo;
  light and dark themes.
- Mask-plate and wafer settings with overflow checks before OPC and export.
- Hopkins / SOCS partially coherent imaging engine with conventional, annular, dipole and quasar sources.
- Constant-threshold, variable-threshold and compact resist models with sub-pixel contour extraction.
- OPC engine: rule-based, model-based (EPE iteration), inverse (pixel-based) and SRAF insertion.
- Verification: EPE / CD maps, hotspot detection, mask rule check with locations, process window, HTML + PNG report.
- Model calibration against CD, space and edge-placement gauges.
- Export: GDSII, OASIS, CIF, DXF, `.map` sidecar and a JEOL52 hand-off package (fractured GDSII, Layout BEAMER job script, fields, dose table).
- Windows installer (`LithoMask-Setup.exe`), portable executable (`LithoMask.exe`) and a 22-page User Guide.
- 155 automated tests.

### Fixed
- Packaged executable failed to start with `ImportError: DLL load failed while importing QtCore` because an older Visual C++
  runtime was bundled ahead of the one Qt requires. The build spec now ships the matching runtime and UPX compression is disabled.