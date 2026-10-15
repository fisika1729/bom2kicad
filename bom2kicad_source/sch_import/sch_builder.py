"""Generate a KiCad schematic (.kicad_sch) from a matched netlist.

Strategy: every part is placed on a grid; each pin gets a short wire stub
ending in a **net label carrying the original net name**, so connectivity is
reconstructed exactly without wire routing.
"""
from __future__ import annotations

import math
import os
import uuid
from typing import Dict, List, Optional

from . import kicad_sym
from .netlists.base import NetlistModel


HEADER = """(
kicad_sch
\t(version 20250114)
\t(generator "bom2kicad_next")
\t(generator_version "9.0")
\t(uuid "{root_uuid}")
\t(paper "{paper}")
\t(lib_symbols)
\t(embedded_fonts no)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
)
"""


def _uuid() -> str:
    return str(uuid.uuid4())


class SchBuilder:
    FIELDS = ("Reference", "Value", "Footprint", "Datasheet", "LCSC Part")

    def __init__(self, library_path: str, lib_nickname: str = "b2k"):
        self.lib_nickname = lib_nickname
        self.root_uuid = _uuid()
        self._lanes = {}
        self.symbols = kicad_sym.load_library(library_path)
        self.pins_by_refdes: Dict[str, dict] = {}
        self.body = []
        # KiCad stock symbols used (Device:R / Device:C / ...) - their
        # definitions get embedded in lib_symbols from STOCK_SYMBOLS text
        self._stock_defs: Dict[str, str] = {}
        self._stock_pins: Dict[str, dict] = {}

    # ---------------------------------------------------------------- public
    def add_part(self, refdes: str, value: str, symbol: str, footprint: str,
                 lcsc: str = "", x: float = 50.0, y: float = 50.0,
                 extra_properties: Optional[dict] = None,
                 stock_lib: str = "", stock_fp: str = "") -> bool:
        stock_id = ""
        if symbol and ":" in symbol:
            stock_id = symbol
            import standard_libs as _sl

            if stock_id not in self._stock_defs:
                node_sexpr = _sl._STOCK_SYMBOLS.get(stock_id)
                if node_sexpr is None:
                    return False
            # normalize pins via the same extractor used for project libs
            lsym = kicad_sym.LibSymbol(
                lib_id=stock_lib or stock_id.split(":")[0],
                name=stock_id.split(":")[1],
            )
            lsym.node = self._stock_node(stock_id)
            if lsym.pins is None or not lsym.pins:
                lsym.pins = kicad_sym._extract_pins(lsym.node)
            key = stock_id if stock_id in _sl._STOCK_SYMBOLS else None
            if key and stock_id not in self._stock_defs:
                self._stock_defs[stock_id] = _sl._STOCK_SYMBOLS[stock_id]
            self._stock_pins[stock_id] = lsym.pins
        else:
            lsym = self.symbols.get(symbol)
            if lsym is None:
                return False
        uid = _uuid()
        placed = self._place(refdes, lsym, value, footprint, lcsc, x, y, uid,
                             extra_properties,
                             stock_id=stock_id,
                             stock_fp=stock_fp or None)
        self.pins_by_refdes[refdes] = {
            "uid": uid, "x": x, "y": y, "sym": symbol,
            "pins": lsym.pins, "node": lsym,
        }
        return True

    def _stock_node(self, stock_id: str):
        import standard_libs as _sl
        from . import sexpr as _sexpr

        if stock_id not in self._stock_node_cache:
            text = _sl._STOCK_SYMBOLS[stock_id]
            tree = _sexpr.parse(text)
            node = tree[0]
            # embed_node later needs (symbol "Lib:Name" ...) with header listing
            node[1] = f'"{stock_id}"'
            self._stock_node_cache[stock_id] = node
        return self._stock_node_cache[stock_id]

    _stock_node_cache: dict = {}

    def _prop_dy(self, i: int) -> float:
        return i * 5.08

    def connect_net(self, net_name: str, pins: list) -> List[dict]:
        """Draw wire stubs + labels for every (refdes, pinnum) in this net.

        Auto-placement with collision-avoiding offsets."""
        updates = []
        label_rows = []
        lane = {}          # (refdes) -> arrow Poke offsets per pin
        for refdes, pinnum in pins:
            info = self.pins_by_refdes.get(refdes)
            if info is None or pinnum not in info["pins"]:
                # unresolved refdes: keep as marker for verification diff
                updates.append({"ref": None})
                continue
            lsym = info["node"]
            tip = kicad_sym.pin_tip_world(lsym, pinnum, info["x"], info["y"])
            dx, dy = kicad_sym.pin_outward_world(lsym, pinnum)
            lane_key = (refdes, tip)
            # lane spacing: pins on the same side get separated lane indices
            idx = self._lane_index(refdes, tip, dx)
            len_stub = 2.54 + 1.27 * idx
            lx, ly = tip[0] + dx * len_stub, tip[1] + dy * len_stub
            # wire from tip to (lx, ly)
            self.body.append(self._wire(tip[0], tip[1], lx, ly))
            # terminate the stub with a net label
            self.body.append(self._label(net_name, lx, ly, dx > 0.5, (-dx, -dy)))
            label_rows.append({"ref": refdes, "pin": pinnum, "net": net_name})
        return label_rows

    # ------------------------------------------------------------- internals
    def _lane_index(self, refdes: str, tip, dx: float) -> int:
        key = (refdes, round(dx, 1))
        store = self._lanes.setdefault(key, {})
        tipk = (round(tip[0], 3), round(tip[1], 3))
        if tipk not in store:
            store[tipk] = len(store)
        return store[tipk]

    _lanes: dict = {}

    def _wire(self, x0, y0, x1, y1) -> str:
        return (
            f"\t(wire (pts (xy {x0:.4f} {y0:.4f}) (xy {x1:.4f} {y1:.4f}))\n"
            f"\t\t(stroke (width 0) (type default))\n"
            f"\t\t(uuid \"{_uuid()}\"))\n"
        )

    def _label(self, net: str, x, y, anchor_right: bool, inward=(0, 0)) -> str:
        jy = "(justify right)" if anchor_right else "(justify left)"
        return (
            f'\t(global_label "{net}"\n'
            f'\t\t(shape passive)\n'
            f'\t\t(at {x:.4f} {y:.4f} 0)\n'
            f'\t\t(effects (font (size 1.27 1.27)) {jy})\n'
            f'\t\t(uuid "{_uuid()}")\n'
            f'\t\t(property "Intersheetrefs" "@INTERSHEET_REFS"\n'
            f'\t\t\t(at {x:.4f} {y:.4f} 0)\n'
            f'\t\t\t(effects (font (size 1.27 1.27)) (hide yes))\n'
            f'\t\t)\n'
            f'\t)\n'
        )

    def _place(self, refdes, lsym, value, footprint, lcsc, x, y, uid, extra,
               stock_id: str = "", stock_fp: Optional[str] = None):
        if stock_id:
            name = stock_id          # e.g. "Device:R" for stock symbols
        else:
            name = f"{self.lib_nickname}:{lsym.name}"
        body = lsym.node

        property_lines = []
        prop_vals = {
            "Reference": refdes,
            "Value": value,
            "Footprint": stock_fp if stock_fp else
            (f"{self.lib_nickname}:{footprint}" if footprint else ""),
            "Datasheet": "",
        }
        if lcsc:
            prop_vals["LCSC Part"] = lcsc
        if extra:
            prop_vals.update(extra)

        # keep order: Reference, Value, then the rest
        ordered = ["Reference", "Value", "Footprint", "Datasheet"]
        keys = ordered + [k for k in prop_vals if k not in ordered]

        dy = 0.0
        property_lines_out = []
        for key in keys:
            if key == "Reference":
                py = -self._prop_dy(1)
            elif key == "Value":
                py = self._prop_dy(1)
            else:
                py = self._prop_dy(2)
            shown = prop_vals.get(key, "")
            hide = key in ("Footprint", "Datasheet", "LCSC Part")
            hide_kw = "(hide yes)\n\t\t\t" if hide else ""
            property_lines_out.append(
                f"\t\t(property \"{key}\" \"{shown}\"\n"
                f"\t\t\t(at 0 {py:.4f} 0)\n"
                f"\t\t\t{hide_kw}"
                f"(effects (font (size 1.27 1.27)))\n"
                f"\t\t)"
            )
            dy += 2.54

        pins_s = "".join(
            f'\n\t\t(pin "{num}" (uuid "{_uuid()}"))'
            for num in lsym.pins
        )
        inst = (
            f"\n\t(symbol\n"
            f"\t\t(lib_id \"{name}\")\n"
            f"\t\t(at {x:.4f} {y:.4f} 0)\n"
            f"\t\t(unit 1)\n"
            f"\t\t(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)\n"
            f"\t\t(uuid \"{uid}\")\n"
            + "\n".join(property_lines_out)
            + pins_s
            + f"\n\t\t(instances (project \"bom2kicad\" (path \"/{self.root_uuid}\" "
            f"(reference \"{refdes}\") (unit 1))))\n"
            f"\t)\n"
        )
        self.body.append(inst)
        return uid

    # ---------------------------------------------------------------- output
    def render(self) -> str:
        lib_symbols = "\n".join(
            kicad_sym.embed_node(s.node, self.lib_nickname)
            for s in self.symbols.values()
            if s.pins
        )
        stock_blocks = list(self._stock_defs.values())
        all_defs = ([lib_symbols] if lib_symbols.strip() else []) + stock_blocks
        libs_text = "\n".join(all_defs)
        text = (
            "(kicad_sch\n"
            "\t(version 20250114)\n"
            "\t(generator \"bom2kicad_next\")\n"
            "\t(generator_version \"9.0\")\n"
            f"\t(uuid \"{self.root_uuid}\")\n"
            "\t(paper \"A3\")\n"
            f"\t(lib_symbols\n{libs_text}\n\t)\n"
            "\t(embedded_fonts no)\n"
            + "\n".join(self.body)
            + "\n\t(sheet_instances\n"
            "\t\t(path \"/\"\n"
            "\t\t\t(page \"1\")\n"
            "\t\t)\n"
            "\t)\n"
            ")\n"
        )
        return text
