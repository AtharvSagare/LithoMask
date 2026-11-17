"""Phase 7 demo: verify uncorrected vs model-OPC masks for the Phase 5 test layouts (L-shape, line-end) and write HTML/PNG reports.

Run from the LithoMask folder:  python examples\verify_demo.py
Output: examples\verify_out\*.html, *_overlay.png, *_window.png
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from lithomask import geometry as g
from lithomask.optics import Imager, Source
from lithomask.opc import ModelOptions, OPCOptions, ProcessModel, correct
from lithomask.verify import MRCRules, VerifyOptions, WindowOptions, verify, write_report

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_out")


def layouts():
    w, L = 180.0, 800.0
    return {
        "L-shape": [g.polygon([(0, 0), (L, 0), (L, w), (w, w), (w, L), (0, L)])],
        "Line-end": [g.rectangle(0, 0, w, 1000.0)],
    }


def main():
    im = Imager(wavelength=193.0, na=0.75, source=Source.conventional(0.6))
    pm = ProcessModel.anchored(im, cd=180.0, tone="clear_field", shape="line", pixel=8.0)
    opts = VerifyOptions(mrc=MRCRules(min_width=60, min_space=60, grid=1.0),
                         window=WindowOptions(doses=(0.9, 1.0, 1.1), defoci=(-200.0, 0.0, 200.0), n_gauges=2))
    for name, tgt in layouts().items():
        res = correct(tgt, pm, OPCOptions(stages=("model",), model=ModelOptions(max_iter=8)))
        for tag, mask in (("uncorrected", tgt), ("OPC", res.mask)):
            r = verify(tgt, mask, pm, opts, name=f"{name} {tag}")
            p = write_report(r, OUT)
            print(f"{r.name}: EPE RMS {r.epe_stats['rms']:.1f} nm, hotspots {len(r.hotspots)}, MRC {len(r.mrc)} -> {p['html']}")


if __name__ == "__main__":
    main()
