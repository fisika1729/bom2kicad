"""Netlist registry: pick the right parser by file signature."""

from __future__ import annotations

from typing import Tuple

from .base import NetlistModel
from . import allegro_3rdparty, altium_net, ipc2581, pspice


def sniff(text: str) -> str:
    up = "\n".join(text.splitlines()[:40]).upper()
    if "<IPC-2581" in up or "LOGICALNETLIST" in up:
        return "ipc2581"
    if "$PACKAGE" in up and "$NETS" in up:
        return "allegro-third-party"
    if "$COMPONENT" in up or ("$NET" in up and "$END" in up):
        return "altium-net"
    if up.lstrip().startswith("$"):
        return "altium-net"
    # default: classic netlist-ish (PSpice/PADS)
    return "pspice"


def parse_file(path: str, fmt: str = None) -> NetlistModel:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    if fmt is None:
        if path.lower().endswith(".xml") or path.lower().endswith(
            ("ipc", "2581")
        ):
            fmt = "ipc2581"
        else:
            fmt = sniff(text)
    if fmt == "pspice":
        return pspice.parse(text)
    if fmt == "allegro-third-party":
        return allegro_3rdparty.parse(text)
    if fmt == "ipc2581":
        return ipc2581.parse(path)
    if fmt == "altium-net":
        return altium_net.parse(text)
    raise ValueError(f"Unknown netlist format: {fmt}")


def supported() -> Tuple[str, ...]:
    return ("pspice", "allegro-third-party", "ipc2581", "altium-net")
