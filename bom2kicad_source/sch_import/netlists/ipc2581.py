"""IPC-2581 XML parser — Siemens Xpedition/Expedition and Cadence Allegro handoff.

Parses the *logical netlist* view only: RefDefs (parts, with footprints and
pin counts) and Net/PinNet (connectivity). This is the industry-standard
interchange both Xpedition and Allegro export natively.

    <IPC-2581>
      <Content>
        <LogicalNetlist>
          <Net name="VCC">
            <PinNet name="...">
              <PinIndicators>
                <SetPhyDesNode><PhyDesRefID refDes="C1"/><RefDesPinIndicators ...>
              < ... -->
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from .base import NetlistModel, NetlistNet, NetlistPart


def _lcsc(node, *names) -> str:
    for n in names:
        v = node.get(n)
        if v:
            return v
    return ""


def parse(path: str) -> NetlistModel:
    model = NetlistModel(source_format="ipc2581")
    tree = ET.parse(path)
    root = tree.getroot()

    parts: dict[str, NetlistPart] = {}

    # --- parts: LogicalNetlist/RefDefs/RefDef ---
    for refdef in root.iter("RefDef"):
        des = refdef.get("des") or ""
        if not des:
            continue
        part = parts.setdefault(des.upper(), NetlistPart(refdes=des.upper()))
        part.symbol_hint = _lcsc(refdef, "RefDefType") or part.symbol_hint
        for el in refdef.iter():
            tag = el.tag.lower()
            if "footprint" in tag and not part.footprint_hint:
                for probe in ("value", "name", "decalname"):
                    if el.get(probe):
                        part.footprint_hint = el.get(probe)
                        break
            for probe in ("value", "partNumber", "MfrPN", "LCSC"):
                v = el.get(probe)
                if v and probe == "value":
                    part.value = v
                if v and probe.lower() in ("partnumber", "mfrpn"):
                    part.mpn = v
                if v and probe == "LCSC" and v.startswith(("C", "S")):
                    part.lcsc = v

    # --- nets: Net/PinNet/PinIndicators/SetPhyDesNode ---
    for net_parent in root.iter("Net"):
        net_name = net_parent.get("name") or f"net{len(model.nets)}"
        net = NetlistNet(name=net_name)
        for pinn in net_parent.iter("PinNet"):
            for pinset in pinn.iter("SetPhyDesNode"):
                # spec shape: <SetPhyDesNode><PhyDesRefID refDes="R1"/><PinNum.../>
                # simplified shape tolerated: <SetPhyDesNode refDes="R1" pinNumber="2"/>
                refdes = ""
                for probe_el in ("PhyDesRefID", "RefDes"):
                    el = pinset.find(probe_el)
                    if el is not None:
                        refdes = (el.get("refDes") or el.get("des") or "")
                        break
                if not refdes:
                    refdes = pinset.get("refDes") or pinset.get("des") or ""
                if not refdes:
                    continue
                num = ""
                for probe in ("pinNumber", "pinNum", "pinNumName"):
                    num = pinset.get(probe) or ""
                    if num:
                        break
                if not num:
                    el = pinset.find(".//PinNum") or pinset.find(".//PinNumber")
                    num = (el.text or "").strip() if el is not None else ""
                net.add(refdes, num or "0")
        if net.pins:
            model.nets.append(net)

    model.parts = list(parts.values())
    model.parts.sort(key=lambda p: p.refdes)
    return model
