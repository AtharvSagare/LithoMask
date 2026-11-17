"""Phase 9 tests: headless workflow (settings -> OPC -> document -> verify -> export), GUI wiring, example project loading."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from lithomask import Library, rectangle
from lithomask.edit import Document
from lithomask import workflow
from lithomask.workflow import OPCCancelled, ProcessSettings, RunSettings

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples"))


def _doc():
    lib = Library("T")
    top = lib.new_cell("TOP")
    top.add(rectangle(0, 0, 1500, 180, layer=1), rectangle(1750, 0, 3250, 180, layer=1))
    return Document(lib)


def test_settings_validation():
    with pytest.raises(ValueError):
        RunSettings(stage="bogus").validate()
    with pytest.raises(ValueError):
        RunSettings(mask_layer=(1, 0)).validate()          # same as target layer
    with pytest.raises(ValueError):
        ProcessSettings(source="annular", sigma=0.4, sigma_in=0.5).make_source()
    assert set(workflow.PRESETS) and all(isinstance(p, ProcessSettings) for p in workflow.PRESETS.values())


def test_auto_anchor_cd_line():
    polys = workflow.polygons_on_layer(_doc(), (1, 0))
    assert workflow.auto_anchor_cd(polys) == pytest.approx(180.0, abs=2.0)


def test_layer_helpers():
    d = _doc()
    assert workflow.layer_counts(d) == {(1, 0): 2}
    assert len(workflow.polygons_on_layer(d, (1, 0))) == 2 and workflow.polygons_on_layer(d, (9, 9)) == []


def test_run_apply_undo_and_cancel():
    d = _doc()
    s = RunSettings(process=ProcessSettings(anchor_cd=180.0), max_iter=4)
    target = workflow.polygons_on_layer(d, s.target_layer)
    with pytest.raises(OPCCancelled):
        workflow.run_opc(target, s, cancel=lambda: True)
    run = workflow.run_opc(target, s)
    assert run.opc.epe_after["rms"] < run.opc.epe_before["rms"]          # OPC must improve EPE
    n_main, n_sraf = workflow.apply_to_document(d, run)
    assert n_main >= 2 and n_sraf == 0
    assert workflow.layer_counts(d)[(100, 0)] == n_main
    workflow.apply_to_document(d, run)                                   # second run replaces, not duplicates
    assert workflow.layer_counts(d)[(100, 0)] == n_main
    d.undo()
    d.undo()
    assert (100, 0) not in workflow.layer_counts(d)                      # each run is one undo step


def test_empty_target_and_grid_guard():
    with pytest.raises(ValueError):
        workflow.run_opc([], RunSettings())
    s = RunSettings(process=ProcessSettings(anchor_cd=180.0, pixel=1.0))
    big = [rectangle(0, 0, 100000, 180, layer=1)]
    with pytest.raises(ValueError):
        workflow.run_opc(big, s)


def test_verification_and_export(tmp_path):
    from end_to_end_demo import lineend
    lib, s = lineend()
    s.max_iter = 4
    d = Document(lib)
    run = workflow.run_opc(workflow.polygons_on_layer(d, s.target_layer), s)
    paths = workflow.write_verification(run, str(tmp_path))
    assert os.path.exists(paths["html"])


def test_example_projects_load_and_gui_actions():
    from end_to_end_demo import EXAMPLES
    for name, fn in EXAMPLES.items():
        lib, s = fn()
        assert workflow.layer_counts(Document(lib)).get((1, 0), 0) > 0, name
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from lithomask.gui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    w = MainWindow()
    assert w.act_run.text().startswith("Run OPC") and w.act_jeol.text().startswith("Export JEOL52")
    w.close()
