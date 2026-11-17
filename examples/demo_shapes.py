"""Phase 1 demo: build a small mask layout with every primitive, save GDSII + .mop, and print a summary."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lithomask import (Library, rectangle, polygon, circle, ellipse, ring, regular_polygon, path,
                     boolean, offset, total_area)

here = os.path.dirname(os.path.abspath(__file__))
lib = Library("DEMO")

hole = lib.new_cell("CONTACT")                       # 500 nm contact hole on layer 1
hole.add(rectangle(-250, -250, 250, 250, layer=1))

top = lib.new_cell("TOP")
top.add(rectangle(0, 0, 20000, 10000, layer=10))                           # die outline, layer 10
top.add(circle(3000, 3000, 1500, tol=1.0, layer=2))                        # circle
top.add(ellipse(8000, 3000, 2000, 1000, tol=1.0, rotation=30, layer=2))    # rotated ellipse
top.add(ring(13000, 3000, 800, 1500, tol=1.0, layer=2))                    # ring
top.add(regular_polygon(17500, 3000, 1500, 6, layer=2))                    # hexagon
top.add(path([(1000, 7000), (6000, 7000), (6000, 9500)], 400, end="flat", join="miter", layer=3))   # L wire
top.add(path([(8000, 7000), (10000, 9500), (12000, 7000)], 300, end="round", join="round", layer=3))  # V wire, round
top.add_ref(hole, origin=(14000, 7000), columns=5, rows=3, spacing=(1000, 1000))                       # 5x3 contact array
# boolean + bias examples on layer 4
a, b = [rectangle(0, 0, 1500, 1500, layer=4)], [circle(1500, 1500, 900, layer=4)]
top.add([p.translate(15500, 500) for p in boolean(a, b, "or")])
top.add([p.translate(15500 - 6000, 500 + 5500) for p in offset(a, 100, join="miter")])

lib.write_gds(os.path.join(here, "demo.gds"))
lib.save_mop(os.path.join(here, "demo.mop"))
flat = lib.flatten("TOP")
print(f"cells: {list(lib.cells)}; flattened polygons: {len(flat)}; union area: {total_area(flat):.0f} nm^2")
print("wrote:", os.path.join(here, "demo.gds"), "and demo.mop")
