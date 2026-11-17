"""LithoMask - photomask design, optical proximity correction and e-beam export."""
from .geometry import (Polygon, rectangle, polygon, circle, ellipse, regular_polygon, ring, path,
                       boolean, union, offset, bbox_of, total_area)
from .layout import Cell, Reference, Library, LayerInfo

__version__ = "1.0.0"
__all__ = ["Polygon", "rectangle", "polygon", "circle", "ellipse", "regular_polygon", "ring", "path",
           "boolean", "union", "offset", "bbox_of", "total_area", "Cell", "Reference", "Library", "LayerInfo"]
