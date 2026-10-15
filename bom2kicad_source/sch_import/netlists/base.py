"""Common netlist model + registry shared by all migration parsers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class NetlistPin:
    refdes: str          # annotation, e.g. "C12"
    pin: str             # pin number as authored, e.g. "2"


@dataclass
class NetlistNet:
    name: str
    pins: List[NetlistPin] = field(default_factory=list)

    def add(self, refdes: str, pin: str) -> None:
        self.pins.append(NetlistPin(refdes.strip().upper(), pin.strip()))


@dataclass
class NetlistPart:
    refdes: str                          # canonical annotation (upper case)
    value: str = ""                      # component value / netlist model
    footprint_hint: str = ""             # if the tool exported one
    mpn: str = ""                        # if the tool carries MPN fields
    lcsc: str = ""                       # rare, but some exports keep it
    symbol_hint: str = ""                # tool-side symbol/part type like "R", "CAP"
    props: Dict[str, str] = field(default_factory=dict)


@dataclass
class NetlistModel:
    parts: List[NetlistPart] = field(default_factory=list)
    nets: List[NetlistNet] = field(default_factory=list)
    source_format: str = ""

    def part(self, refdes: str) -> Optional[NetlistPart]:
        r = refdes.strip().upper()
        for p in self.parts:
            if p.refdes == r:
                return p
        return None

    def summary(self) -> str:
        return (
            f"{self.source_format}: {len(self.parts)} parts, "
            f"{len(self.nets)} nets, "
            f"{sum(len(n.pins) for n in self.nets)} connections"
        )


_LCSC_RE = re.compile(r"^[CS][0-9]{3,}$")


def looks_like_lcsc(token: str) -> bool:
    return bool(_LCSC_RE.match(token.strip().upper()))
