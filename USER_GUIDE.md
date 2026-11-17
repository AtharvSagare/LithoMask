# LithoMask User Guide

LithoMask designs photomask layouts, corrects them with computational OPC (optical proximity correction), verifies the
simulated print, and prepares a JEOL52 e-beam hand-off package. All lengths are in nanometres unless stated.

## 1. Install and start
- Double-click `install.bat` (creates `.venv`, installs numpy, scipy, gdstk, shapely, pyclipper, PySide6, pytest).
- Start the editor: `run_lithomask.bat`, or `python -m lithomask.gui [file.mop|file.gds]`.
- Windows executable (no Python needed): download `LithoMask-Setup.exe` or the portable `LithoMask.exe` from the GitHub Releases page. To build it yourself run `build_exe.bat`; the result is `dist\LithoMask.exe`.

## 2. Draw the target
Draw the shapes that should **print** (rectangle, polygon, circle, ellipse, ring, wire, array, text). Use the layer panel to
pick the layer; the default target layer is 1/0. Edit exact numbers in the Properties panel. Undo/redo is Ctrl+Z / Ctrl+Y.
Save as `.mop` (project) or export GDSII / OASIS / CIF / DXF (File > Export layout).

## 3. Run OPC  (Run > Run OPC..., Ctrl+R)
| Setting | Meaning |
|---|---|
| Process preset | Starting wavelength / NA / source / tone. Edit freely afterwards. |
| Target layer | Layer holding the shapes that should print. |
| OPC method | Model-based (EPE iteration, default), rule-based (bias, hammerheads), inverse/pixel-based (slow). |
| Add assists | Inserts sub-resolution assist features (SRAF) before correction. |
| Mask tone | `clear_field`: target = chrome on clear glass (lines). `dark_field`: target = openings (contacts). |
| Anchor CD | Isolated feature size that prints on size; sets the resist threshold. "estimate from target" uses the smallest feature width - enter it manually for pad + line shapes. |
| Pixel / margin | Simulation grid. Smaller pixel = more accurate, slower. A grid over 6 M pixels is refused. |

A progress window shows EPE RMS per iteration; **Cancel** stops after the current step. The corrected mask goes to layer
100/0 (assists 101/0) as ONE undoable step; running again replaces the previous result.

## 4. Compare and verify
- Run > Compare (Ctrl+K): target (green), corrected mask (blue), printed uncorrected (red dashed), printed corrected (yellow).
- "Verification report ..." writes HTML + PNG: EPE/CD maps, hotspots (bridging, pinching, line-end shortening), mask rule check.

## 5. Export for the JEOL52 writer  (Run > Export JEOL52..., Ctrl+Shift+J)
Writes a **Route B** package: GDSII + BEAMER job script + settings, field/subfield plan, dose table, README, JSON report.
Convert to `.v30` in BEAMER. LithoMask does **not** write `.v30` directly: that binary format is licensee-only and no public
specification exists; a native writer (Route A) will only be added after a real facility `.v30` + `.jdi/.jdf` sample
can be parsed, rewritten and byte-compared.

## 6. Examples
`python examples\end_to_end_demo.py all` builds `examples\projects\{contacts,lineend,tjunction}.mop` and runs
OPC -> verification -> JEOL52 package for each into `examples\e2e_out\`. Open the `.mop` files in the GUI to try it by hand.

## 7. Known limits
- Scalar imaging model; the resist model is a calibrated constant/variable threshold (calibration: `lithomask.calibration`).
- Inverse OPC is slow (minutes). The full test suite takes ~7 minutes.
- TODO-VERIFY items are listed in `notes\phase8_summary.md` and `notes\jeol52_format.md`.

## Mask and wafer size (View > Mask and wafer settings..., Ctrl+Shift+M)
Works like CleWin5 (View > Background settings). Two tabs plus a position tab:
- **Mask**: Custom, or a square plate of 3, 4, 5, 6, 7 or 9 inch. Tick "Show mask dimensions" to draw the plate outline; the status bar shows "Mask size: NNmm x NNmm".
- **Wafer**: 1, 2, 3 inch, 100, 125, 150, 200, 300, 450 mm, or Custom diameter. Tick "Show wafer outline" to draw the circle. "Edge exclusion" removes an unusable rim. "Optical reduction" is mask : wafer (1 for contact / 1x, 4 or 5 for a stepper).
- **Position**: centre of the mask and wafer in layout coordinates (default 0, 0).
LithoMask warns before **Run OPC**, **Export layout** and **Export JEOL52** if objects lie outside the mask, if the pattern (divided by the reduction) reaches past the usable wafer radius, or if the whole mask plate does not fit inside the wafer. You can continue anyway or cancel. Settings are saved in the .mop file and are undoable.

### Seeing the mask and wafer (zoom, grid, units)
- View > **Fit mask / wafer** (key W) zooms so the whole mask rectangle and wafer circle are visible. It also runs automatically after you press OK in the settings dialog. **Fit all** (F) fits the design; with an empty cell it fits the outlines.
- Zoom range: about 5 nm across the window up to 100 m, so a 100 cm view is fine. The mouse wheel zooms at the cursor.
- Rulers and the cursor read-out switch units by themselves (nm, um, mm, cm), for example "12.5 mm".
- The status bar shows the view width, the grid-line spacing (it coarsens when you zoom out) and the snap spacing.
- The **Snap grid** box takes a unit: "250 nm", "0.5 mm", "1 cm". A bare number means nm. Snapping stays at that value at every zoom level, so zoom in before you draw fine detail.
- Display units (View > Display units) now include mm and cm for the numeric dialogs and the properties panel.

### One-click outlines
View > **Show mask outline** and **Show wafer outline** (also on the toolbar, next to Fit mask / wafer). Ticking one draws it and zooms to fit. The mask is a blue rectangle, the wafer a gold circle. The first time you open View > Mask and wafer settings, both "Show" boxes are already ticked. The outlines are only drawn when these boxes are ticked.
