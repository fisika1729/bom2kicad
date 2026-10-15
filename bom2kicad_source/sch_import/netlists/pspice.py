"""PSpice netlist parser (Cadence OrCAD Capture / Allegro simulation nets).

Covers the classic form:

    * device R1
    R1 N001 N002 10k
    C1 N001 0 100nF

plus OrCAD's extended properties form emitted by Capture:

    $PS_R1   R1   N001 N002   value=10k   LSC=C476766   ...
    @line ...
"""
from __future__ import annotations

import re

from .base import NetlistModel, NetlistNet, NetlistPart, looks_like_lcsc

_RE = re.compile(r"[^\s]+")


def parse(text: str) -> NetlistModel:
    model = NetlistModel(source_format="pspice")
    pending = {}  # instance -> dict(value=..., props=...)
    for raw in text.splitlines():
        line = raw.split("$", 1)[0] if "$PS_" not in raw else raw
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("$PS_"):
            tok = line.split()
            # $PS_<prefix> refdes pin(s)... key=value...
            if len(tok) < 2:
                continue
            refdes = tok[1].upper()
            info = pending.setdefault(refdes, {})
            for kv in tok[3:]:
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    k = k.lower().strip(".")
                    info[k] = v.strip('"')
            continue
        if line.lower().startswith(".end"):
            break
        tok = _RE.findall(line)
        if len(tok) < 2:
            continue
        inst = tok[0]
        prefix = inst[0].upper()
        if not prefix.isalpha():
            continue
        devtype = tok[0][0].upper()
        if devtype in "RCU":           # 2-terminal
            node_a, node_b, *rest = tok[1:]
            pending.setdefault(inst, {"value": rest[0] if rest else ""})
        elif devtype == "L":
            node_a, node_b, *rest = tok[1:]
            pending.setdefault(inst, {"value": rest[0] if rest else ""})
        elif devtype == "D":           # diode nodes go via .model lines; tolerate
            node_a, node_b, node_model = tok[1], tok[2], tok[3] if len(tok) > 3 else ""
            pending.setdefault(inst, {"value": node_model})
        elif devtype in "QR":
            nodes = [t for t in tok[3:] if not t.startswith("model")]
            pending.setdefault(inst, {"nodes": nodes})
        elif devtype in "IC":
            cfg = tok[1:]
            if cfg:
                pending.setdefault(inst, {"value": " ".join(cfg)})
        else:
            pending.setdefault(inst, {"value": " ".join(tok[1:])})

    # second pass: build nets by scanning again (kept simple + robust)
    current_nets = {}
    for raw in text.splitlines():
        line = re.sub(r"\$PS_\S+.*", "", raw)
        line = re.sub(r"\$.*", "", line)
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        if line.lower().startswith(".end"):
            break
        tok = _RE.findall(line)
        if len(tok) < 3 or tok[0][0].upper() not in "RCULDQ":
            continue
        inst = tok[0].upper()
        nodes = tok[1:3]
        for n in nodes:
            net = current_nets.setdefault(n, NetlistNet(name=n))
            net.add(inst, "?")

    # pin numbers are not carried in PSpice nets; assign sequential numbering
    import math
    pin_counter = {}
    for net in current_nets.values():
        for p in net.pins:
            key = (p.refdes, p.pin)
            pin_counter.setdefault(p.refdes, 0)
            p.pin = str(pin_counter[p.refdes] + 1)
            pin_counter[p.refdes] += 1

    for refdes, info in pending.items():
        props = {k: v for k, v in info.items() if k not in ("nodes",)}
        part = NetlistPart(refdes=refdes, value=props.get("value", ""), props=props)
        # surface useful extended properties as first-class fields
        for k, v in info.items():
            key = k.lower()
            if key in ("partnumber", "mpn", "part_number") and v and not v.startswith("("):
                part.mpn = v
            elif looks_like_lcsc(v):
                part.lcsc = v
            elif key in ("footprint", "footprintspcb", "pcbfootprint"):
                part.footprint_hint = v
            elif key in ("value",) and not part.value:
                part.value = v
        model.parts.append(part)
    model.parts.sort(key=lambda p: p.refdes)
    model.nets = list(current_nets.values())
    return model
