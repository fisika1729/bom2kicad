"""Reuse KiCad's standard libraries when a package already exists there.

When a BOM part maps to a package KiCad ships as standard (R0402, C0603,
SOD-123, LED0805, ...), BOM2Lib skips generating its own footprint (and for
generic passives R/C/L/D the EasyEDA symbol as well) and the part references
KiCad's maintained footprint/symbol (e.g. `Resistor_SMD:R_0603_1608Metric`).
This keeps the project library small, avoids radial-pad drift from EasyEDA
and gives 3D models from KiCad itself.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# ---------------------------------------------------------------------------
# footprint packages -> KiCad standard footprint (lib nickname, name)
# ---------------------------------------------------------------------------

_FP_FOR = {
    # resistors
    ("R", "0402"): ("Resistor_SMD", "R_0402_1005Metric"),
    ("R", "0603"): ("Resistor_SMD", "R_0603_1608Metric"),
    ("R", "0805"): ("Resistor_SMD", "R_0805_2012Metric"),
    ("R", "1206"): ("Resistor_SMD", "R_1206_3216Metric"),
    ("R", "2010"): ("Resistor_SMD", "R_2010_5025Metric"),
    ("R", "2512"): ("Resistor_SMD", "R_2512_6332Metric"),
    # ceramic capacitors
    ("C", "0402"): ("Capacitor_SMD", "C_0402_1005Metric"),
    ("C", "0603"): ("Capacitor_SMD", "C_0603_1608Metric"),
    ("C", "0805"): ("Capacitor_SMD", "C_0805_2012Metric"),
    ("C", "1206"): ("Capacitor_SMD", "C_1206_3216Metric"),
    # inductors
    ("L", "0402"): ("Inductor_SMD", "L_0402_1005Metric"),
    ("L", "0603"): ("Inductor_SMD", "L_0603_1608Metric"),
    ("L", "0805"): ("Inductor_SMD", "L_0805_2012Metric"),
    ("L", "1206"): ("Inductor_SMD", "L_1206_3216Metric"),
    # diodes
    ("D", "SOD-523"): ("Diode_SMD", "D_SOD-523"),
    ("D", "SOD-323"): ("Diode_SMD", "D_SOD-323"),
    ("D", "SOD-123"): ("Diode_SMD", "D_SOD-123"),
    ("D", "SMA"): ("Diode_SMD", "D_SMA"),
    ("D", "SMB"): ("Diode_SMD", "D_SMB"),
    ("D", "SMC"): ("Diode_SMD", "D_SMC"),
    ("D", "DO-214"): ("Diode_SMD", "D_SMB"),
    # leds
    ("LED", "0402"): ("LED_SMD", "LED_0402_1005Metric"),
    ("LED", "0603"): ("LED_SMD", "LED_0603_1608Metric"),
    ("LED", "0805"): ("LED_SMD", "LED_0805_2012Metric"),
    ("LED", "1206"): ("LED_SMD", "LED_1206_3216Metric"),
    # tvs / esd share diode packages
    ("TVS", "SOD-123"): ("Diode_SMD", "D_SOD-123"),
    ("TVS", "SMA"): ("Diode_SMD", "D_SMA"),
    ("TVS", "SMB"): ("Diode_SMD", "D_SMB"),
    ("TVS", "SOT-23"): ("Package_TO_SOT_SMD", "SOT-23"),
    # generic smd transistors
    ("Q", "SOT-23"): ("Package_TO_SOT_SMD", "SOT-23"),
    ("Q", "SOT-23-3"): ("Package_TO_SOT_SMD", "SOT-23"),
    ("Q", "SOT-23-5"): ("Package_TO_SOT_SMD", "SOT-23-5"),
    ("Q", "SOT-23-6"): ("Package_TO_SOT_SMD", "SOT-23-6"),
    ("Q", "SOT-223"): ("Package_TO_SOT_SMD", "SOT-223"),
    ("U", "SOT-23"): ("Package_TO_SOT_SMD", "SOT-23"),
    ("U", "SOT-23-5"): ("Package_TO_SOT_SMD", "SOT-23-5"),
    ("U", "SOT-23-6"): ("Package_TO_SOT_SMD", "SOT-23-6"),
    ("U", "SOT-223"): ("Package_TO_SOT_SMD", "SOT-223"),
}

def standard_footprint_for(cls: str, package: str) -> Optional[Tuple[str, str]]:
    key = (cls, package)
    if key in _FP_FOR:
        return _FP_FOR[key]
    # direct raw-package name match (e.g. ("R", "R0603"))
    for (c, p), v in _FP_FOR.items():
        if package == (c + p) and c == cls:
            return v
    return None


def classify(easyeda_fp_name: str, bom_fp_name: str = "",
             mpn: str = "") -> Tuple[Optional[str], str]:
    """Extract (part_class, package_token) from the EasyEDA footprint name,
    BOM footprint column or MPN, e.g. 'R0603' -> ('R', '0603'),  'SOIC-8'
    -> ('U', 'SOIC-8')."""
    for src in (easyeda_fp_name, bom_fp_name, mpn):
        s = (src or "").strip().upper()
        if not s:
            continue
        # size packages: R0603 / C0805_SC / L1210 etc.
        m = re.match(r"^(R|C|L)(\d{4})", s)
        if m:
            return m.group(1), m.group(2)
        if re.match(r"^LED\s?0?(\d{4})", s) or "LED" in s and re.search(r"\d{4}", s):
            n = re.search(r"(\d{4})", s)
            if n:
                return "LED", n.group(1)
        # diode packages
        for pkg in ("SOD-123", "SOD-523", "SOD-323", "SMA", "SMB", "SMC",
                    "DO-214"):
            if pkg in s:
                cls = "D"
                if "LED" in s:
                    cls = "LED"
                return cls, pkg
        # sot packages
        for pkg in ("SOT-223", "SOT-23-3", "SOT-23-5", "SOT-23-6", "SOT-23-4",
                    "SOT-23"):
            if pkg in s:
                cls = "U"
                if re.match(r"^(Q|VT|TR)", s):
                    cls = "Q"
                if "TVS" in s or "ESD" in s:
                    cls = "TVS"
                return cls, pkg
        # fuse entries like F0603
        m = re.match(r"^(F|FB)(\d{4})", s)
        if m:
            return m.group(1), m.group(2)
    return None, ""


# classes where the EasyEDA symbol is a generic box and KiCad's `Device`
# symbols are a better fit; anything else keeps its EasyEDA symbol
_STANDARD_SYMBOL_CLASSES = ("R", "C", "L", "D", "LED")

# embedded Device:* symbol definitions (v9 s-expr, validated by kicad-cli)
_STOCK_SYMBOLS = {
    "Device:R": (
        '(symbol "Device:R"\n'
        '  (pin_numbers hide) (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
        '  (property "Reference" "R" (at 2.032 0 90) (effects (font (size 1.27 1.27))))\n'
        '  (property "Value" "R" (at 0 0 90) (effects (font (size 1.27 1.27))))\n'
        '  (property "Footprint" "" (at -1.778 0 90) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Datasheet" "~" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Description" "Resistor" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (symbol "R_0_1"\n'
        '    (rectangle (start -1.016 2.54) (end 1.016 -2.54) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (pin passive line (at 0 3.81 270) (length 1.27)\n'
        '      (name "~" (effects (font (size 1.27 1.27))))\n'
        '      (number "1" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '    (pin passive line (at 0 -3.81 90) (length 1.27)\n'
        '      (name "~" (effects (font (size 1.27 1.27))))\n'
        '      (number "2" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '  )\n'
        ')'
    ),
    "Device:C": (
        '(symbol "Device:C"\n'
        '  (pin_numbers hide) (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
        '  (property "Reference" "C" (at 2.794 1.524 0) (effects (font (size 1.27 1.27))))\n'
        '  (property "Value" "C" (at 2.794 -1.524 0) (effects (font (size 1.27 1.27))))\n'
        '  (property "Footprint" "" (at -1.778 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Datasheet" "~" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Description" "Unpolarized capacitor" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (symbol "C_0_1"\n'
        '    (polyline (pts (xy -2.032 -0.762) (xy 2.032 -0.762)) (stroke (width 0.508) (type default)) (fill (type none)))\n'
        '    (polyline (pts (xy -2.032 0.762) (xy 2.032 0.762)) (stroke (width 0.508) (type default)) (fill (type none)))\n'
        '    (pin passive line (at 0 3.81 270) (length 2.794)\n'
        '      (name "~" (effects (font (size 1.27 1.27))))\n'
        '      (number "1" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '    (pin passive line (at 0 -3.81 90) (length 2.794)\n'
        '      (name "~" (effects (font (size 1.27 1.27))))\n'
        '      (number "2" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '  )\n'
        ')'
    ),
    "Device:L": (
        '(symbol "Device:L"\n'
        '  (pin_numbers hide) (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
        '  (property "Reference" "L" (at -1.016 0 90) (effects (font (size 1.27 1.27))))\n'
        '  (property "Value" "L" (at 0 0 90) (effects (font (size 1.27 1.27))))\n'
        '  (property "Footprint" "" (at -1.778 0 90) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Datasheet" "~" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Description" "Inductor" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (symbol "L_0_1"\n'
        '    (arc (start 0 2.54) (mid 0.635 1.905) (end 0 1.27) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (arc (start 0 1.27) (mid 0.635 0.635) (end 0 0) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (arc (start 0 0) (mid 0.635 -0.635) (end 0 -1.27) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (arc (start 0 -1.27) (mid 0.635 -1.905) (end 0 -2.54) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (pin passive line (at 0 3.81 270) (length 1.27)\n'
        '      (name "~" (effects (font (size 1.27 1.27))))\n'
        '      (number "1" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '    (pin passive line (at 0 -3.81 90) (length 1.27)\n'
        '      (name "~" (effects (font (size 1.27 1.27))))\n'
        '      (number "2" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '  )\n'
        ')'
    ),
    "Device:D": (
        '(symbol "Device:D"\n'
        '  (pin_numbers hide) (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
        '  (property "Reference" "D" (at 0 2.54 0) (effects (font (size 1.27 1.27))))\n'
        '  (property "Value" "D" (at 0 -2.54 0) (effects (font (size 1.27 1.27))))\n'
        '  (property "Footprint" "" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Datasheet" "~" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Description" "Diode" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (symbol "D_0_1"\n'
        '    (polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27)) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (polyline (pts (xy 1.27 0) (xy -1.27 0)) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (polyline (pts (xy 1.27 1.27) (xy 1.27 -1.27) (xy -1.27 0) (xy 1.27 1.27)) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (pin passive line (at -3.81 0 0) (length 2.54)\n'
        '      (name "K" (effects (font (size 1.27 1.27))))\n'
        '      (number "1" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '    (pin passive line (at 3.81 0 180) (length 2.54)\n'
        '      (name "A" (effects (font (size 1.27 1.27))))\n'
        '      (number "2" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '  )\n'
        ')'
    ),
    "Device:LED": (
        '(symbol "Device:LED"\n'
        '  (pin_numbers hide) (pin_names (offset 1.016) hide) (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
        '  (property "Reference" "D" (at 0 3.81 0) (effects (font (size 1.27 1.27))))\n'
        '  (property "Value" "LED" (at 0 -3.81 0) (effects (font (size 1.27 1.27))))\n'
        '  (property "Footprint" "" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Datasheet" "~" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (property "Description" "Light emitting diode" (at 0 0 0) (hide yes) (effects (font (size 1.27 1.27))))\n'
        '  (symbol "LED_0_1"\n'
        '    (polyline (pts (xy -1.27 1.27) (xy -1.27 -1.27)) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (polyline (pts (xy 1.27 0) (xy -1.27 0)) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (polyline (pts (xy 1.27 1.27) (xy 1.27 -1.27) (xy -1.27 0) (xy 1.27 1.27)) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (polyline (pts (xy 1.905 1.905) (xy 1.27 2.54) (xy 2.54 1.27)) (stroke (width 0.254) (type default)) (fill (type none)))\n'
        '    (pin passive line (at -3.81 0 0) (length 2.54)\n'
        '      (name "K" (effects (font (size 1.27 1.27))))\n'
        '      (number "1" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '    (pin passive line (at 3.81 0 180) (length 2.54)\n'
        '      (name "A" (effects (font (size 1.27 1.27))))\n'
        '      (number "2" (effects (font (size 1.27 1.27))))\n'
        '    )\n'
        '  )\n'
        ')'
    ),
}


def stock_symbol_for(cls: str) -> Optional[str]:
    """'Device:R' / 'Device:C' / ... for classes that should reuse KiCad
    generic symbols instead of EasyEDA's."""
    if cls in ("R", "C", "L", "D", "LED"):
        if cls == "LED":
            return "Device:LED"
        return "Device:" + cls
    return None
