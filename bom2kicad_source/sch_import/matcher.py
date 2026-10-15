"""Annotation-based matcher: netlist refdes <-> BOM row <-> LCSC part number.

Primary key is the design annotation (R1, C12, U5...). Because the BOM may
come from a *different* project/session than the netlist (EasyEDA renumbers,
CAD tools rename parts), value/MPN/footprint hints are fallbacks with
conflict detection - ambiguous matches stay unresolved and are reported,
never guessed silently.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .netlists.base import NetlistModel

# v1 plugin sits next door, kept isolated; imported lazily by the demo tools
V1_PLUGIN_DIR = "/home/nerd/.local/share/kicad/10.0/scripting/plugins/bom2kicad"


@dataclass
class Match:
    refdes: str
    lcsc: str = ""                       # resolved LCSC part number ("" = unresolved)
    value: str = ""
    source: str = ""                     # explicit | designator | value | mpn | footprint
    row: Optional[object] = None         # matched v1 BomPart
    candidates: List[str] = field(default_factory=list)
    note: str = ""


@dataclass
class MatchResult:
    matches: Dict[str, Match] = field(default_factory=dict)
    unresolved: List[Match] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def get(self, refdes: str) -> Optional[Match]:
        return self.matches.get(refdes.strip().upper())


# ---------------------------------------------------------------------------
# engineering-notation normalization ('4k7' == 4.7k == 4700, 100nF == 0.1uF)
# ---------------------------------------------------------------------------

_LETTERS = {
    "k": 1e3, "K": 1e3,
    "M": 1e6,            # uppercase M == mega ('10M')
    "m": 1e-3,           # lowercase m == milli ('4m7'; farad/BOM values)
    "n": 1e-9, "N": 1e-9,
    "u": 1e-6, "U": 1e-6,
    "p": 1e-12, "P": 1e-12,
    "r": 1.0, "R": 1.0,  # 4R7 == 4.7 (resistor decimal shorthand)
    "V": 1.0, "v": 1.0,  # voltage suffix ('3V3' == 3.3V == 3.3)
}
_LETTERS["µ"] = 1e-6
_LETTERS["μ"] = 1e-6


def norm_value(v: str) -> str:
    return (v or "").strip().lower()


def parse_value(v: str) -> Optional[float]:
    """'4k7' / '4.7k' / '100nF' / '10kΩ' / '2R2' / '3V3' -> float base
    value (unit-agnostic). Non-numeric strings ('LED') -> None."""
    s = (v or "").strip()
    if not s:
        return None
    s = s.replace("Ω", "").replace("Ω", "").replace("ohm", "").replace("OHm", "")
    low = s.lower()
    if low.endswith("h"):
        s = s[:-1]
    low = s.lower()
    if low.endswith("f"):
        s = s[:-1]
    low = s.lower()
    if not s:
        return None
    # mid-shorthand: 4k7 / 4M7 / 2R2 / 3V3
    m = re.match(r"^(\d+(?:\.\d+)?)([A-Za-zµμ])(\d*)$", s)
    if m:
        num, letter, tail = m.group(1), m.group(2), m.group(3)
        mult = _LETTERS.get(letter)
        if mult is None:
            return None
        base = float(num) * mult
        if tail:
            base += float("0." + tail) * mult
        return base
    # suffix style: 4.7k / 10k / 100n / 10M / 4.7 / 10meg
    m = re.match(r"^(\d+(?:\.\d+)?)(meg|mega)([A-Za-z]*)$", s, re.IGNORECASE)
    if m:
        return float(m.group(1)) * 1e6
    m = re.match(r"^(\d+(?:\.\d+)?)([A-Za-zµμ]{0,1})$", s)
    if m:
        num, letter = m.group(1), m.group(2)
        if not letter:
            return float(num)
        mult = _LETTERS.get(letter)
        return float(num) * mult if mult is not None else None
    try:
        return float(s)
    except ValueError:
        return None
        return None


def values_equal(a: str, b: str, rel_tol: float = 0.02) -> bool:
    va, vb = parse_value(a), parse_value(b)
    if va is not None and vb is not None:
        big = max(abs(va), abs(vb))
        if big == 0.0:
            return True
        return abs(va - vb) <= rel_tol * big
    return norm_value(a).replace("ω", "") == norm_value(b).replace("ω", "")


def norm_mpn(m: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (m or "").upper())


def footprint_overlap(hint: str, fp_name: str) -> bool:
    """True if the source-CAD footprint hint and the EasyEDA footprint name
    plausibly describe the same package (0603, SOIC-8, SOT-23-5, ...)."""
    if not hint or not fp_name:
        return False
    nums_h = set(re.findall(r"\d+", hint.upper()))
    fp_up = fp_name.upper()
    interesting = [n for n in nums_h if len(n) >= 2]
    if not interesting:
        return False
    return all(n in fp_up for n in interesting)


# ---------------------------------------------------------------------------
# BOM indexing
# ---------------------------------------------------------------------------


def build_refdes_index(bom_parts: list) -> Dict[str, dict]:
    """BOM rows -> {'C1': row, ...} via the Designator column."""
    index: Dict[str, dict] = {}
    for row in bom_parts:
        for des in (getattr(row, "designators", "") or "").split(","):
            des = des.strip().upper()
            if des:
                index.setdefault(des, {
                    "lcsc": getattr(row, "lcsc_id", ""),
                    "name": getattr(row, "name", ""),
                    "footprint": getattr(row, "footprint_name", ""),
                    "mpn": getattr(row, "mpn", ""),
                    "row": row,
                })
    return index


def match(netlist: NetlistModel, bom_parts: list) -> MatchResult:
    """Match every netlist part against BOM rows.

    Resolution order (never silently guessed):
      1. explicit LCSC code carried in the netlist (OrCAD/Altium exports
         survive here as a Supplier Part property)
      2. exact design annotation hit in the BOM Designator column
      3. value match, engineering-notation aware
      4. MPN match (exact, then token-prefix with length guard)
      5. footprint hint: either disambiguates 3/4 or is a lone unique match

    Multiple surviving candidates -> unresolved with the candidate list;
    netlist parts absent from the BOM are reported too.
    """
    res = MatchResult()
    index = build_refdes_index(bom_parts)
    bom_by_lcsc: Dict[str, object] = {}
    for p in bom_parts:
        if getattr(p, "lcsc_id", ""):
            bom_by_lcsc.setdefault(p.lcsc_id, p)

    for part in netlist.parts:
        m = Match(refdes=part.refdes, value=getattr(part, "value", ""))
        explicit = (part.lcsc or part.props.get("lcsc")
                    or part.props.get("lsc") or "").strip().upper()

        if explicit and explicit in bom_by_lcsc:
            row = bom_by_lcsc[explicit]
            m.lcsc, m.source, m.row = row.lcsc_id, "explicit", row
        elif part.refdes in index:
            tgt = index[part.refdes]
            m.lcsc, m.source, m.row = tgt["lcsc"], "designator", tgt["row"]
        else:
            # ---- value match (annotation may differ between projects) ----
            value_hits = []
            probe_value = part.value or part.mpn
            if probe_value:
                for r in bom_parts:
                    if values_equal(probe_value, r.name):
                        value_hits.append(r)
            # ---- MPN match ----
            mpn_exact, mpn_partial = [], []
            if part.mpn:
                nm = norm_mpn(part.mpn)
                for r in bom_parts:
                    rm = norm_mpn(getattr(r, "mpn", ""))
                    if not nm or not rm:
                        continue
                    if nm == rm:
                        mpn_exact.append(r)
                    elif (nm.startswith(rm) or rm.startswith(nm)) and min(len(nm), len(rm)) >= 6:
                        mpn_partial.append(r)
            rows = value_hits or mpn_exact or mpn_partial
            hit_src = "value" if rows else ""

            # several rows share a value -> a matching exact MPN pins it down
            if len(value_hits) > 1 and mpn_exact:
                inter_set = set(id(r) for r in mpn_exact)
                inter = [r for r in value_hits if id(r) in inter_set]
                if len(inter) == 1:
                    rows, hit_src = inter, "value+mpn"

            if len(rows) == 1:
                m.lcsc, m.source, m.row = rows[0].lcsc_id, hit_src or "mpn", rows[0]
            elif len(rows) > 1:
                narrowed = []
                if part.footprint_hint:
                    narrowed = [r for r in rows
                                if footprint_overlap(part.footprint_hint, r.footprint_name)]
                if len(narrowed) == 1:
                    m.lcsc, m.source, m.row = narrowed[0].lcsc_id, "footprint", narrowed[0]
                else:
                    dedup = [r.lcsc_id for r in rows]
                    m.candidates = dedup
            else:
                # last resort: exactly one BOM row shares the footprint
                fp_hits = [r for r in bom_parts
                           if footprint_overlap(part.footprint_hint, r.footprint_name)]
                if len(set(r.lcsc_id for r in fp_hits)) == 1 and fp_hits:
                    m.lcsc, m.source, m.row = fp_hits[0].lcsc_id, "footprint", fp_hits[0]
        res.matches[part.refdes] = m

    # ---- post loop: candidate-hit notes and unresolved bookkeeping --------
    for refdes in sorted(res.matches):
        m = res.matches[refdes]
        if not m.lcsc:
            if m.candidates:
                m.note = f"value '{m.value}' ambiguous between {'/'.join(m.candidates)}"
            else:
                m.note = "no BOM row matched"
        if m.lcsc:
            m.value = getattr(m.row, "name", "") or m.value
        else:
            res.unresolved.append(m)

    # parts whose annotation is entirely off-BOM (different project)
    for p in netlist.parts:
        if p.refdes not in index:
            m = res.matches[p.refdes]
            if m.lcsc:
                res.notes.append(
                    f"{p.refdes}: annotation not in BOM; resolved via {m.source}"
                )
    for m in res.unresolved:
        res.notes.append(f"{m.refdes}: {m.note}")
    return res
