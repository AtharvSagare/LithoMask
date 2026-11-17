"""Phase 9 end-to-end demo (scripted, headless): the same path the GUI takes.

draw target -> Run OPC -> apply to document -> verification report -> JEOL52 Route-B package.
Run from the LithoMask folder:   python examples\end_to_end_demo.py [contacts|lineend|tjunction|all]
Writes example projects to examples\projects\*.mop and results to examples\e2e_out\<name>\.
Units: nm.  Target layer 1/0; corrected mask 100/0; assists 101/0.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from lithomask import Library, rectangle
from lithomask.edit import Document
from lithomask import workflow
from lithomask.workflow import ProcessSettings, RunSettings
from lithomask.export import DoseTable, FieldOptions, JeolOptions, export_route_b

PROJ = os.path.join(HERE, "projects")
OUT = os.path.join(HERE, "e2e_out")


def contacts():
    """3 x 3 array of 180 nm contact holes, pitch 450 nm (dark-field: the squares are openings)."""
    lib = Library("CONTACT_ARRAY")
    top = lib.new_cell("TOP")
    for i in range(3):
        for j in range(3):
            x, y = i * 450.0, j * 450.0
            top.add(rectangle(x, y, x + 180, y + 180, layer=1))
    return lib, RunSettings(process=ProcessSettings(tone="dark_field", anchor_shape="hole", anchor_cd=180.0))


def lineend():
    """Two 180 nm lines facing each other across a 250 nm tip-to-tip gap (line-end pullback / bridging)."""
    lib = Library("LINE_END")
    top = lib.new_cell("TOP")
    top.add(rectangle(0, 0, 1500, 180, layer=1), rectangle(1750, 0, 3250, 180, layer=1))
    return lib, RunSettings(process=ProcessSettings(anchor_cd=180.0))


def tjunction():
    """T-junction: 180 nm bar with a 180 nm stem (inner/outer corner rounding)."""
    lib = Library("T_JUNCTION")
    top = lib.new_cell("TOP")
    top.add(rectangle(0, 0, 1800, 180, layer=1), rectangle(810, 180, 990, 1100, layer=1))
    return lib, RunSettings(process=ProcessSettings(anchor_cd=180.0))


EXAMPLES = {"contacts": contacts, "lineend": lineend, "tjunction": tjunction}


def run_example(name: str):
    lib, settings = EXAMPLES[name]()
    os.makedirs(PROJ, exist_ok=True)
    out = os.path.join(OUT, name)
    os.makedirs(out, exist_ok=True)
    lib.save_mop(os.path.join(PROJ, f"{name}.mop"))                  # example project, opens in the GUI

    doc = Document(lib)
    target = workflow.polygons_on_layer(doc, settings.target_layer)
    run = workflow.run_opc(target, settings, progress=lambda s, i, r, m: print(f"  {s} it{i}: RMS {r:.1f} nm"))
    n_main, n_sraf = workflow.apply_to_document(doc, run)
    print(f"[{name}] {run.summary()}  -> wrote {n_main} main / {n_sraf} assist polygons")

    paths = workflow.write_verification(run, out, name=name)
    print(f"[{name}] verification report: {paths}")

    # JEOL52 Route-B package of the corrected mask only (main + assists), 1 nm pattern unit
    mask_lib = Library(name.upper() + "_MASK")
    cell = mask_lib.new_cell("TOP")
    cell.add(*[p for p in doc.cell.flatten() if p.key in (tuple(settings.mask_layer), tuple(settings.sraf_layer))])
    opts = JeolOptions(pattern_unit=1.0, shot_pitch=4.0, fields=FieldOptions(field_size=1_000_000.0, subfield_size=50_000.0),
                       dose=DoseTable(base_dose=300.0, factors={settings.mask_layer[0]: 1.0, settings.sraf_layer[0]: 1.0}))
    rep = export_route_b(mask_lib, os.path.join(out, "jeol"), name, opts)
    print(f"[{name}] {rep.summary()}")
    lib.save_mop(os.path.join(out, f"{name}_with_opc.mop"))
    return run


def main(argv):
    which = argv[1] if len(argv) > 1 else "all"
    for n in (EXAMPLES if which == "all" else [which]):
        run_example(n)


if __name__ == "__main__":
    main(sys.argv)
