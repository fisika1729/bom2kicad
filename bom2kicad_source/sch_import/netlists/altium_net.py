"""Altium `.Net` netlist document parser (experimental).

Altium Designer netlist documents look like:

    $Component
    Power_Supply_1		power_supply.schdoc
    	C1		SMD_CAP
    $EndComponent
    $Net
    Net_1
    	C1-2		R3-1		...
    $EndNet
"""
from __future__ import annotations

import re

from .base import NetlistModel, NetlistNet, NetlistPart

_TAB_SPLIT = re.compile(r"\s+")


def parse(text: str) -> NetlistModel:
    model = NetlistModel(source_format="altium-net")
    section = None
    current_net: NetlistNet | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("$COMPONENT"):
            section = "component"
            continue
        if up.startswith("$NET"):
            section = "net"
            continue
        if up.startswith("$END"):
            section = None
            continue
        if section == "component":
            tok = _TAB_SPLIT.split(line)
            # refdes lines: "C1 CAP_0805"
            if re.match(r"^[A-Z]+\d+\b", line, re.IGNORECASE) and not line.startswith("\t"):
                tok = line.split()
                model.parts.append(
                    NetlistPart(refdes=tok[0].upper(),
                                footprint_hint=tok[1] if len(tok) > 1 else "")
                )
        elif section == "net":
            if current_net is not None and "\t" in raw:
                for tok in line.split():
                    if "-" in tok:
                        refdes, _, pin = tok.rpartition("-")
                        current_net.add(refdes, pin)
            else:
                current_net = NetlistNet(name=line)
                model.nets.append(current_net)

    return model
