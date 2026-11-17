# CleWin-compatible interchange export

The supplied CleWin 5 samples were inspected: `atharv12.gds` and `atharv12.oas`
both contain the `MainSymbol` cell with nine layer 0/datatype 0 polygons.  The
`.map` file is a readable CleWin layer-name/color sidecar; `.cif` is text CIF 2.0
with CleWin comments and private extensions.

## LithoMask exports

| Extension | LithoMask behavior | Handoff note |
| --- | --- | --- |
| `.gds` | Native hierarchical GDSII | Offer a matching `.map` layer-name sidecar. |
| `.oas` | Native hierarchical OASIS | Preferred compact interchange where the facility supports OASIS. |
| `.cif` | Standard, flattened CIF 2.0 at 1 nm resolution | Polygons are written as hole-free pieces; no CleWin-private objects are emitted. |
| `.dxf` | Flattened, closed outlines in um | DXF does not portably preserve mask fill or hole semantics; validate in the receiving CAD tool. |
| `.map` | Readable layer-name/datatype sidecar | Not a JEOL writer input. |

The File menu has **Export layout...** for GDSII, OASIS, CIF, and DXF.  CIF opens
the four choices corresponding to the CleWin dialog: private extensions, flatten,
remove empty symbols, and remove unused symbols.  LithoMask currently exports
standard flattened CIF, so it never emits CleWin private extensions; this is the safe
interchange mode for non-CleWin recipients.

OASIS coordinates may retain a finer grid than the 1 nm GDS database unit.  In the
provided sample, the OASIS maximum x coordinate is 0.5 nm below the GDS value,
which is expected quantisation rather than a topology change.

Validation: full regression suite, 134 passed.
