"""bom2kicad_next — 2.0 development package.

Ingests foreign netlists (OrCAD, Allegro, Xpedition/Expedition, Altium) and an
EasyEDA BOM, then generates a fully-annotated KiCad project: matching symbol +
footprint per annotation, net connectivity preserved via net labels.
"""

__version__ = "2.0.0-dev"

from . import netlists  # noqa: F401
