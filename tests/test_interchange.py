import gdstk

from lithomask import Library, rectangle, ring
from lithomask.export import write_cif, write_dxf, write_layer_map
from lithomask.layout import LayerInfo


def _library():
    lib = Library("T")
    lib.layers[(1, 0)] = LayerInfo("chrome")
    lib.new_cell("TOP").add(rectangle(0, 0, 100, 50, layer=1), ring(200, 50, 10, 30, tol=1.0, layer=1))
    return lib


def test_oasis_roundtrip_and_document_open(tmp_path):
    from lithomask.edit import Document

    lib = _library()
    path = tmp_path / "a.oas"
    lib.write_oas(str(path))
    assert gdstk.read_oas(str(path)).cells
    back = Library.read_oas(str(path)).flatten("TOP")
    # OASIS coordinates are quantised to this library's 1 nm database grid.
    assert abs(sum(p.area for p in back) - sum(p.area for p in lib.flatten("TOP"))) < 25
    assert Document.open_file(str(path)).lib.cells["TOP"]


def test_cif_dxf_and_layer_map_exports(tmp_path):
    lib = _library()
    cif, dxf, mapping = tmp_path / "a.cif", tmp_path / "a.dxf", tmp_path / "a.map"
    write_cif(lib, str(cif))
    write_dxf(lib, str(dxf))
    write_layer_map(lib, str(mapping))
    assert "DS1 1 10;" in cif.read_text() and "L chrome;" in cif.read_text() and cif.read_text().endswith("E\n")
    assert "LWPOLYLINE" in dxf.read_text() and dxf.read_text().endswith("EOF\n")
    assert "1 chrome" in mapping.read_text()
