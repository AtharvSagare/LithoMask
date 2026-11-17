"""Phase 4 demo: mask -> aerial image -> resist contour for an L-shape + contact hole. Saves examples/resist_demo.png.
Run:  python examples/resist_demo.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from lithomask import geometry as g
from lithomask.optics import Grid, Imager, Source
from lithomask.resist import ConstantThreshold, CompactResist, Term, measure_cd, simulate_print

here = os.path.dirname(os.path.abspath(__file__))
im = Imager(193.0, 0.75, Source.annular(0.5, 0.8))
pix = 6.0
grid = Grid(-600, -600, 200, 200, pix)
# clear-field mask: chrome L-shape (printed resist = dark region)
L = g.polygon([(-300, -300), (100, -300), (100, -150), (-150, -150), (-150, 300), (-300, 300)])
ctr = ConstantThreshold(0.3)
cmp = CompactResist([Term(1.0), Term(0.15, 0, 0.0, 1, 0, 25.0, ("c", "sigma"))], 0.35)

fig, ax = plt.subplots(1, 3, figsize=(15, 4.8))
for a, model, name in ((ax[1], ctr, "constant threshold 0.30"), (ax[2], cmp, "compact (I + 0.15 I*G25), t=0.35")):
    pr = simulate_print([L], im, model, grid, "clear_field")
    a.imshow(pr.aerial, origin="lower", extent=grid.extent, cmap="gray")
    for p in pr.contours:
        a.plot(*p.outer[list(range(len(p.outer))) + [0]].T, "r", lw=1.5, label="resist")
    a.plot(*L.outer[list(range(len(L.outer))) + [0]].T, "c--", lw=1, label="target")
    a.set_title(name)
ax[0].imshow(pr.aerial * 0 + 1, origin="lower", extent=grid.extent, cmap="gray", vmin=0, vmax=1)
ax[0].fill(*L.outer.T, color="k")
ax[0].set_title("Mask (chrome L-shape)")
for a in ax:
    a.set_xlabel("x (nm)"); a.set_ylabel("y (nm)")
fig.tight_layout()
out = os.path.join(here, "resist_demo.png")
fig.savefig(out, dpi=130)
pr = simulate_print([L], im, ctr, grid, "clear_field")
print("saved", out, "| vertical-arm CD", round(measure_cd(pr.field, grid, (-450, 100), (50, 100), pr.level, "dark"), 1),
      "nm (drawn 150)")
