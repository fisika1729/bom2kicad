"""Load symbol definitions from a KiCad .kicad_sym file and derive pin maps."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import sexpr


@dataclass
class LibSymbol:
    lib_id: str          # e.g. "bom2kicad:R" (library file nickname)
    name: str            # bare name inside the file, e.g. "R"
    node: list = field(default_factory=list)
    pins: Dict[str, dict] = field(default_factory=dict)   # num -> {x,y,angle,length}


def load_library(path: str) -> Dict[str, LibSymbol]:
    with open(path, encoding="utf-8") as f:
        text_data = f.read()
    tree = sexpr.parse(text_data)
    lib_node = tree[0]
    symbols = {}
    for sym in lib_node[1:]:
        if isinstance(sym, list) and sym and sym[0] == "symbol":
            name = sexpr.sym_name(sym[1])
            symbols[name] = LibSymbol(lib_id=name, name=name)
            symbols[name].node = sym
            symbols[name].pins = _extract_pins(sym)
    return symbols


def _extract_pins(sym_node: list) -> Dict[str, dict]:
    pins = {}
    # easyeda2kicad symbols keep pins inside the "_0_1" unit wrapper;
    # native (kicad-saved) symbols use "_1_1". Handle both, only take the
    # drawing unit (multi-unit symbols are on the v2 roadmap).
    unit_nodes = sexpr.find_all(sym_node, "symbol")
    pin_count = 0
    for unit_node in unit_nodes:
        header = sexpr.sym_name(unit_node[1]) if len(unit_node) > 1 and isinstance(unit_node[1], str) else ""
        tag = header.rsplit("_", 1)[-1]
        if tag not in ("1", "0"):
            continue
        pin_list = sexpr.find_all(unit_node, "pin")
        if not pin_list:
            pin_list = sexpr.find_all(sym_node, "pin")
        for pin in pin_list:
            at = sexpr.find_first(pin, "at")
            number = sexpr.find_first(pin, "number")
            if at is None or number is None:
                continue
            length = sexpr.find_first(pin, "length")
            x, y = sexpr.fnum(at[1]), sexpr.fnum(at[2])
            rot = sexpr.fnum(at[3]) if len(at) > 3 else 0.0
            num = sexpr.unatom(number[1])
            if num in pins:
                pin_count += 0
                continue
            pins[num] = {
                "x": x, "y": y, "angle": rot,
                "length": sexpr.fnum(length[1]) if length is not None else 2.54,
            }
    return pins


def embed_node(sym_node: list, lib_name: str) -> str:
    """Re-emit a library symbol definition as a `(symbol "Lib:Name" ...)` block
    suitable for a schematic `lib_symbols` section."""
    body = list(sym_node)
    body[1] = f'"{lib_name}:{sexpr.sym_name(body[1])}"'
    return _render(body, 1)


def _render(node, indent: int) -> str:
    pad = "\t" * indent
    head = node[0]
    out = [f"{pad}({head}"]
    for item in node[1:]:
        if isinstance(item, list):
            out.append(_render(item, indent + 1))
        else:
            out.append(f"\n{pad*1}{item}" if False else f"\n{pad}{item}")
    out.append(")")
    return "\n".join(out)


def pin_tip_world(lsym: LibSymbol, pin_num: str, pos_x: float, pos_y: float) -> Tuple[float, float]:
    """World (sheet) coords of the connection point of a pin for a symbol
    placed at (pos_x, pos_y), rotation 0, no mirror.

    Symbol library space is y-up; sheet space is y-down."""
    p = lsym.pins[str(pin_num)]
    return (pos_x + p["x"], pos_y - p["y"])


def pin_outward_world(lsym: LibSymbol, pin_num: str) -> Tuple[float, float]:
    """Unit direction (world space) pointing away from the symbol body along
    the pin's connection end."""
    p = lsym.pins[str(pin_num)]
    a = math.radians(p["angle"])
    dx_lib = -math.cos(a)
    dy_lib = -math.sin(a)
    return (dx_lib, -dy_lib)
