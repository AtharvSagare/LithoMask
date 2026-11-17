# jeol52_format.md - what is known about JEOL52 v3.0 (.v30)

## Verified from public pages (Phase 0 search)
- JEOL52 ("J52", file extension .v30 in job decks, e.g. P(5) 'APATTERN.v30') is the pattern format the JEOL e-beam writer reads.
  (UW WNF e-beam docs: pattern prep overview; job deck .jdf page.)
- Facilities typically convert GDSII -> J52 with GenISys BEAMER (licensed export module; "v30" is selectable in the Export dialog if licensed).
  (Weizmann submicron: jeol_format_0.pdf = BEAMER JEOL formatter manual.)
- Machine concepts: e-beam field = 2^19 beam positions per axis (524,288 "shots"/machine-grid points); vector-scan writing (beam only scans exposed
  shapes and jumps between them); "shot pitch" = centre-to-centre spacing of shots used to expose area; shapes are fractured to machine primitives.
  (UW WNF E-Beam System Basics 1; BEAMER manual.)
- BEAMER export dialog exposes parameters such as shot pitch, resolution, field/subfield sizes, PEC handling.

## NOT found (do not guess)
- Public binary layout of the .v30 file (header, field/subfield records, shape record encoding, endianness, dose/shot-rank fields).
- => Any native writer needs (a) a real sample .v30 and/or (b) the JEOL pattern-data specification. Mark everything below TODO-VERIFY.

## Export routes for Phase 8
Route A (native): parse a user-supplied sample .v30 -> derive structure -> write -> byte-compare round trip. Only claim support for fields proven this way.
Route B (fallback, always available): export GDSII (and OASIS if needed) with layer/datatype map + BEAMER job instructions; user converts in BEAMER
  or the facility tool. Also provide a validator that checks extents, minimum feature, grid snapping before conversion.

## What to ask the user before Phase 8
1. Which e-beam system/model and facility? (JEOL JBX-xxxx) 2. Do they have a sample .v30 or the JEOL/BEAMER manual? 3. Field/subfield sizes, shot pitch,
beam step, resist/dose strategy, PEC needed? 4. Is the OPC target a photomask written by e-beam, or direct write on wafer?
