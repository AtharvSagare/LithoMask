"""Phase 3 demo: aerial image of a contact hole and of a dense line/space. Saves examples/aerial_demo.png.
Run:  python examples/aerial_demo.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from lithomask import geometry as g
from lithomask.optics import Grid, Imager, Source

here = os.path.dirname(os.path.abspath(__file__))
lam, na = 193.0, 0.75                                   # ArF dry; change to 365.0 / 0.5 for i-line
im = Imager(lam, na, Source.annular(0.5, 0.8))
pix = 8.0                                               # <= lambda/(4 NA) = 64 nm, fine for smooth plots

fig, ax = plt.subplots(1, 3, figsize=(15, 4.6))
# (a) contact hole 180 nm, dark-field mask (polygon = opening)
grid = Grid(-800, -800, 200, 200, pix)
I = im.image([g.circle(0, 0, 90.0)], grid, tone="dark_field")
h = ax[0].imshow(I, origin="lower", extent=grid.extent, cmap="inferno")
ax[0].contour(grid.xs, grid.ys, I, levels=[0.3], colors="w", linewidths=1)
ax[0].set_title(f"Contact hole 180 nm  (peak {I.max():.2f})")
plt.colorbar(h, ax=ax[0], fraction=0.046)
# (b) dense lines: 6 chrome lines of 100 nm at 200 nm pitch, clear-field mask (polygon = absorber)
lines = [g.rectangle(i * 200.0 - 50, -600, i * 200.0 + 50, 600) for i in range(-3, 3)]
grid2 = Grid(-900, -800, 225, 200, pix)
I2 = im.image(lines, grid2, tone="clear_field")
h = ax[1].imshow(I2, origin="lower", extent=grid2.extent, cmap="inferno")
ax[1].set_title("Dense L/S 100 nm / 200 nm pitch")
plt.colorbar(h, ax=ax[1], fraction=0.046)
# (c) cross-section and defocus
mid = I2.shape[0] // 2
ax[2].plot(grid2.xs, I2[mid], label="focus")
imd = Imager(lam, na, Source.annular(0.5, 0.8), defocus_nm=200.0)
ax[2].plot(grid2.xs, imd.image(lines, grid2)[mid], "--", label="defocus 200 nm")
ax[2].set_xlabel("x (nm)"); ax[2].set_ylabel("I (clear field = 1)"); ax[2].legend(); ax[2].set_title("Cross-section")
for a in ax[:2]:
    a.set_xlabel("x (nm)"); a.set_ylabel("y (nm)")
fig.tight_layout()
out = os.path.join(here, "aerial_demo.png")
fig.savefig(out, dpi=130)
print("saved", out, "| contact peak", round(float(I.max()), 3), "| L/S contrast",
      round(float((I2[mid].max() - I2[mid].min()) / (I2[mid].max() + I2[mid].min())), 3))
