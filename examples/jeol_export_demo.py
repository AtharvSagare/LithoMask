"""Phase 8 demo: make a JEOL52 Route-B package for conversion in BEAMER.

Run from the LithoMask folder:  python examples\jeol_export_demo.py
Output: examples\jeol_export_out\ (GDSII, fractured preview, BEAMER settings,
JDF skeleton, dose table, README, and JSON validation report).

This deliberately does not write a .v30 file: its binary format remains
TODO-VERIFY until a real file from the target JEOL system is supplied.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lithomask import Library, circle, rectangle, ring
from lithomask.export import DoseTable, FieldOptions, JeolOptions, export_route_b

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jeol_export_out")


def main():
    lib = Library("JEOL_DEMO")
    top = lib.new_cell("TOP")
    top.add(
        rectangle(0, 0, 1200, 300, layer=1),
        ring(1800, 150, 80, 180, tol=1.0, layer=1),
        circle(2500, 150, 100, tol=1.0, layer=2),
    )
    opts = JeolOptions(
        pattern_unit=1.0,
        shot_pitch=4.0,
        fields=FieldOptions(field_size=1_000_000.0, subfield_size=50_000.0),
        dose=DoseTable(base_dose=300.0, factors={1: 1.0, 2: 1.15}),
    )
    report = export_route_b(lib, OUT, "jeol_demo", opts)
    print(report.summary())
    print(f"Route-B package written to {OUT}")


if __name__ == "__main__":
    main()
