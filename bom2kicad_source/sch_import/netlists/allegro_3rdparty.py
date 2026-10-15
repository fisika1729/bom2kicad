"""Allegro third-party netlist parser (Cadence Allegro PCB export).

Format:

    $PACKAGE
    R1 0603
    C1 0805
    U1 SOIC-8

    $NETS
    * VCC
    C1.1 U1.8
    * GND
    C1.2 U1.4
"""
from __future__ import annotations

from .base import NetlistModel, NetlistNet, NetlistPart


def parse(text: str) -> NetlistModel:
    model = NetlistModel(source_format="allegro-third-party")
    section = None
    packages: dict[str, NetlistPart] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("$PACKAGE"):
            section = "package"
            continue
        if up.startswith("$NETS"):
            section = "nets"
            continue
        if up.startswith("$END"):
            section = None
            continue
        if section == "package":
            tok = line.split()
            if len(tok) >= 2:
                refdes = tok[0].upper()
                part = packages.setdefault(refdes, NetlistPart(refdes=refdes))
                # last token that is not a plain number is the footprint
                part.footprint_hint = tok[1]
        elif section == "nets":
            if line.startswith("*"):
                model.nets.append(NetlistNet(name=line[1:].strip()))
            else:
                if not model.nets:
                    model.nets.append(NetlistNet(name="unnamed"))
                for tok in line.split():
                    if "." in tok:
                        refdes, pin = tok.split(".", 1)
                        pin = pin.upper()
                        net = model.nets[-1]
                        net.add(refdes, pin)
                        packages.setdefault(refdes, NetlistPart(refdes=refdes.upper()))

    # parts referenced only by nets
    model.parts = list(packages.values())
    for p in model.parts:
        net_positions = []
        for i, n in enumerate(model.nets):
            for pin in n.pins:
                if pin.refdes == p.refdes:
                    net_positions.append((i, pin.pin))
        net_positions.sort()
        p.props["pin_map"] = str([pin for _, pin in net_positions])
    model.parts.sort(key=lambda p: p.refdes)
    return model
