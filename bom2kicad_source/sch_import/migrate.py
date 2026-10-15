"""BOM2Lib 2.0 - schematic migration facade.

Called after `core.build_libraries` when the user supplied BOTH a BOM and a
netlist/schematic export (OrCAD PS pice, Allegro third-party, IPC-2581 from
Xpedition/Allegro, Altium .Net):

  netlist  +  BOM  ->  annotated KiCad schematic with matched symbols

Writes into <project>/<name>.kicad_sch when that file is empty (the skeleton
this plugin itself created), otherwise to <project>/<name>_migrated.kicad_sch.
Optionally verifies connectivity with kicad-cli.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
import sys

if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from . import netlists
from .sch_builder import SchBuilder
from .kicad_sym import sexpr

SYMBOL_FALLBACK_ORDER = ("R", "C", "L", "Q", "D", "U", "J", "Y")


def schematic_is_empty(path: str) -> bool:
    """True when a .kicad_sch exists and has no placed symbols (i.e. our
    skeleton or a fresh KiCad schematic - safe to write into)."""
    if not os.path.isfile(path):
        return True
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return False
    tree = sexpr.parse(text)
    if not tree or not isinstance(tree[0], list):
        return False
    node = tree[0]
    gen = ""
    for item in node[1:]:
        if isinstance(item, list) and item and item[0] == "generator":
            gen = sexpr.unatom(item[1]) if len(item) > 1 else ""
            break
    if "bom2kicad" in gen:
        return True
    # a schematic written by KiCad itself with no placements is still fine
    placed = [i for i in node[1:]
              if isinstance(i, list) and i and i[0] == "symbol"]
    return len(placed) == 0


def choose_target(project_dir: str, project_name: str) -> str:
    primary = os.path.join(project_dir, project_name + ".kicad_sch")
    if schematic_is_empty(primary):
        return primary
    return os.path.join(project_dir, project_name + "_migrated.kicad_sch")


def _symbol_for(lcsc: str, cache: Dict[str, Dict],
                symbols: Dict[str, object], part) -> Optional[str]:
    """Map a matched LCSC id to a symbol name inside the generated library.

    Priority: the 1.0 import cache (lcsc -> EasyEDA symbol name), then BOM
    footprint/MPN hints, then a safe generic part-class fallback that is
    guaranteed to exist in our internal demo lib."""
    cached = cache.get(lcsc)
    if cached and cached.get("symbol") in symbols:
        return cached["symbol"]
    # footprint hint often identical to easyeda name for passives
    # (e.g. "R0603"), not reliable for symbol - fall back by refdes class
    ref = getattr(part, "refdes", "") or ""
    prefix = os.path.basename(ref).lstrip("~")
    while prefix and prefix[0].isdigit():
        prefix = prefix[1:]
    cls = (prefix[:1] or "R").upper()
    for cand in (cls,) + tuple(cls[0] for _ in [0]):
        if cand in symbols:
            return cand
    if cls in symbols:
        return cls
    # passives/leds share 2-pin shapes in the fallback library
    for cand in SYMBOL_FALLBACK_ORDER:
        if cand in symbols:
            return cand
    # first any symbol in the library
    for key in symbols:
        if symbols[key].pins:
            return key
    return None


def load_import_cache(project_dir: str, project_name: str) -> Dict[str, Dict]:
    import json

    path = os.path.join(project_dir, ".bom2kicad_cache.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def migrate(
    netlist_path: str,
    project_dir: str,
    project_name: str,
    log_cb=None,
    kicad_cli: Optional[str] = None,
    columns: int = 3,
) -> Dict:
    """Build an annotated schematic for the netlist + BOM pair.

    Returns { ok, target, parts_placed, nets_connected, verify } where
    verify holds the kicad-cli round-trip verdict (None when unavailable)."""
    _log = log_cb or (lambda m: None)
    lib_path = os.path.join(project_dir, project_name + ".kicad_sym")
    if not os.path.isfile(lib_path):
        raise RuntimeError(
            f"Symbol library {lib_path} not found - build the library first "
            "(BOM import) before migrating a schematic."
        )

    bom_path_csv = os.path.join(project_dir, project_name + "_bom_import.csv")
    bom_parts = []
    if os.path.isfile(bom_path_csv):
        try:
            from core import parse_bom

            bom_parts, _warn = parse_bom(bom_path_csv)
        except Exception:
            bom_parts = []

    netlist = netlists.parse_file(netlist_path)
    _log(f"Netlist parsed ({netlist.source_format}): "
         f"{len(netlist.parts)} parts / {len(netlist.nets)} nets")

    if bom_parts:
        from . import matcher

        res = matcher.match(netlist, bom_parts)
        _log(f"Annotation match: {len(res.matches) - len(res.unresolved)}"
             f"/{len(res.matches)} matched"
             + (f", {len(res.unresolved)} unresolved" if res.unresolved else ""))
        for note in res.notes[:12]:
            _log("  note: " + note)
    else:
        from . import matcher

        res = matcher.MatchResult()

    from . import sexpr as _sexpr
    from . import kicad_sym as _ks

    tree = _sexpr.parse(open(lib_path, encoding="utf-8").read())
    node = tree[0]
    symbols = {}
    for sym in node[1:]:
        if isinstance(sym, list) and sym and sym[0] == "symbol":
            name = _sexpr.sym_name(sym[1])
            symbols[name] = _ks.LibSymbol(lib_id=name, name=name)
            symbols[name].node = sym
            symbols[name].pins = _ks._extract_pins(sym)

    target = choose_target(project_dir, project_name)
    _log(f"Writing schematic to: {target}")

    builder = SchBuilder(library_path=lib_path, lib_nickname=project_name)
    if not symbols:
        raise RuntimeError("Imported library contains no symbols")

    # ---- placement (A3 canvas aware) ----
    # vertical extent of each symbol (worst case across all used symbols)
    def _sym_height(lsym) -> float:
        pins = lsym.pins
        if not pins:
            return 10.0
        ys = [abs(p["y"]) + p.get("length", 2.54) for p in pins.values()]
        return max(max(ys), 8.0)

    row_h = max(_sym_height(s) for s in symbols.values()) + 6.0
    placed: Dict[str, Tuple[float, float]] = {}
    y0 = 20.0
    x0 = 35.0
    x_step = 105.0
    y = y0
    i = 0
    col = 0
    max_rows = 14
    for part in netlist.parts:
        m = res.matches.get(part.refdes)
        lcsc = m.lcsc if m else ""
        cache = load_import_cache(project_dir, project_name)
        cached = cache.get(lcsc) or {}
        cached_fp = cached.get("fp", "")
        std_lib_tag = ""
        sym_name = None
        footprint = ""
        # standard-library reuse: cached fp references a KiCad stock library
        if cached_fp and ":" in cached_fp and not cached_fp.startswith(
            project_name + ":"
        ):
            std_lib_tag, stock_fp_name = cached_fp.split(":", 1)
            cls_pkg_name = (stock_fp_name or "").split("_", 1)[0]
            rmatch = None
            import re as _re2
            if _re2.match(r"^(R|C|L)_", stock_fp_name):
                rmatch = _re2.match(r"^([RCL])_", stock_fp_name)
            if rmatch:
                sym_name = "Device:" + rmatch.group(1)
            elif stock_fp_name.startswith("LED_"):
                sym_name = "Device:LED"
        if sym_name is None:
            sym_name = _symbol_for(lcsc, cache, symbols, part)
        if sym_name is None:
            _log(f"  skip {part.refdes}: no symbol in library for LCSC '{lcsc}'")
            continue
        if m and m.row is not None:
            footprint = getattr(m.row, "footprint_name", "") or ""
        x = x0 + col * x_step
        ok = builder.add_part(
            refdes=part.refdes,
            value=part.value or (getattr(m.row, 'name', '') if m else ""),
            symbol=sym_name,
            footprint=footprint,
            lcsc=lcsc,
            x=x, y=y,
            stock_lib=std_lib_tag if (sym_name and ':' in sym_name) else "",
            stock_fp=(f"{std_lib_tag}:{stock_fp_name}" if std_lib_tag else ""),
        )
        placed[part.refdes] = (x, y)
        desc = sym_name.split(':')[-1]
        _log(f"  placed {part.refdes} ({desc}"
             f"{', ' + lcsc if lcsc else ''})")
        i += 1
        col += 1
        if col >= columns:
            col = 0
            y += row_h
        if y > 270.0:
            _log("  canvas reached - remaining parts skipped this sheet")
            break

    # ---- connectivity via net labels ----
    nets_done = 0
    skipped_refs: List[str] = []
    for net in netlist.nets:
        pins = [(p.refdes, p.pin) for p in net.pins if p.refdes in placed]
        if len(pins) != len(net.pins):
            for p in net.pins:
                if p.refdes not in placed:
                    skipped_refs.append(f"{p.refdes}.{p.pin}")
        if pins:
            builder.connect_net(net.name, pins)
            nets_done += 1
    if skipped_refs:
        uniq = sorted(set(skipped_refs))
        _log(f"  {len(uniq)} pins skipped (parts not placed): "
             + ", ".join(uniq[:8]) + ("..." if len(uniq) > 8 else ""))

    with open(target, "w", encoding="utf-8") as f:
        f.write(builder.render())
    _log(f"Written: {target} ({i} placed / {nets_done} nets labelled)")

    # ---- verify with kicad-cli when available ----
    verify = None
    if kicad_cli and os.path.isfile(kicad_cli):
        try:
            verify = _verify_with_cli(kicad_cli, target, netlist, _log)
        except Exception as e:
            verify = {"status": "error", "detail": str(e)[:200]}
            _log(f"kicad-cli verification failed: {e}")
    return {
        "ok": i > 0,
        "target": target,
        "parts_placed": i,
        "nets_connected": nets_done,
        "verify": verify,
    }


def _verify_with_cli(kicad_cli: str, sch_path: str, netlist, log_cb) -> Dict:
    """Export a netlist with kicad-cli and compare connectivity against the
    source netlist (net name -> set of refdes)."""
    import re as _re
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        out_net = os.path.join(td, "check.net")
        r = subprocess.run(
            [kicad_cli, "sch", "export", "netlist", sch_path, "-o", out_net],
            capture_output=True, text=True, timeout=240,
        )
        if r.returncode != 0:
            return {"status": "error",
                    "detail": (r.stderr or r.stdout or "")[-300:]}
        txt = open(out_net, encoding="utf-8", errors="replace").read()

    got: Dict[str, set] = {}
    current = None
    in_hdr = False
    in_node = False
    in_nets = False
    for line in txt.splitlines():
        if line.strip() == "(nets":
            in_nets = True
            continue
        if not in_nets:
            continue
        if line.strip() == "(net":
            in_hdr = True
            continue
        if in_hdr:
            nm = _re.search(r'\(name "([^"]*)"\)', line)
            if nm:
                current = nm.group(1)
                in_hdr = False
            continue
        if _re.search(r"\(node\b", line.strip()) is not None and \
                line.strip().startswith("(node"):
            in_node = True
            continue
        if in_node:
            ref = _re.search(r'\(ref "([^"]+)"\)', line)
            if ref:
                got.setdefault(current, set()).add(ref.group(1))
                in_node = False

    want: Dict[str, set] = {}
    for net in netlist.nets:
        want.setdefault(net.name, set()).update(
            p.refdes for p in net.pins if p.refdes
        )
    missing = [n for n in want if n not in got]
    mismatched = [
        n for n, refs in got.items()
        if n in want and refs != want[n] and not n.startswith("unconnected-")
    ]
    extra = [
        n for n in got
        if n not in want and not n.startswith("unconnected-")
    ]
    ok = not missing and not mismatched and not extra
    status = "NETS MATCH" if ok else "MISMATCH"
    log_cb(f"Verification: {status} "
           f"({len(got)} exported nets vs {len(want)} expected)")
    if missing:
        log_cb("  missing nets: " + ", ".join(missing[:8]))
    if mismatched:
        log_cb("  mismatched nets: " + ", ".join(mismatched[:8]))
    result = {"status": status, "missing": missing,
              "mismatched": mismatched, "extra": extra}
    return result
