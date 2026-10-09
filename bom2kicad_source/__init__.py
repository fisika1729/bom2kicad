"""
BOM2Lib - KiCad Action Plugin.

Builds a complete, project-local KiCad symbol/footprint/3D library from an
EasyEDA BOM export (CSV). Prompts for a project name, parses the LCSC part
numbers in the BOM, downloads CAD data from the public EasyEDA API and
writes a project library:

    <parent>/<project>/<project>.kicad_sym
    <parent>/<project>/<project>.pretty/...
    <parent>/<project>/<project>.3dshapes/...
    <parent>/<project>/sym-lib-table + fp-lib-table   (auto-registered)
    <parent>/<project>/<project>.kicad_pro/.kicad_sch/.kicad_pcb (optional)

Runs as an Action Plugin (Tools > External Plugins) in Pcbnew and Eeschema.
"""

import os
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

# bundled pure wheels for KiCad's python (requests, pydantic, ...)
SITE_PKG = os.path.join(PLUGIN_DIR, "site-packages")
if os.path.isdir(SITE_PKG) and SITE_PKG not in sys.path:
    sys.path.append(SITE_PKG)
for _pkg in ("requests", "pydantic"):
    try:
        __import__(_pkg)
    except ImportError:
        pass

try:
    import pcbnew as _pcbnew
    _PLUGIN_BASE = _pcbnew.ActionPlugin
    _HAS_PCBNEW = True
except Exception:  # running outside KiCad (tests)
    _PLUGIN_BASE = object
    _HAS_PCBNEW = False

PLUGIN_NAME = "BOM2Lib: create project library from EasyEDA BOM"
PLUGIN_ICON = os.path.join(PLUGIN_DIR, "icon.png")


def _missing_deps() -> list:
    missing = []
    for mod in ("requests", "pydantic"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


if _HAS_PCBNEW:

    class Bom2LibPlugin(_PLUGIN_BASE):
        def defaults(self):
            self.name = PLUGIN_NAME
            self.category = "Import"
            self.description = (
                "Reads an EasyEDA BOM (CSV), prompts for a project name and builds"
                " a project-local KiCad library (symbols, footprints, 3D models)"
                " for every LCSC part number in the BOM."
            )
            self.show_toolbar_button = True
            self.icon_file_name = PLUGIN_ICON
            self.dark_icon_file_name = PLUGIN_ICON

        def Run(self):
            import wx

            try:
                from bom2kicad_gui import run_dialog

                run_dialog()
            except Exception:
                import logging
                import traceback

                logging.exception("BOM2Lib failed")
                err = traceback.format_exc()
                wx.MessageBox(
                    "BOM2Lib failed:\n\n" + err[-2000:],
                    "BOM2Lib error",
                    wx.OK | wx.ICON_ERROR,
                )

    _instance = Bom2LibPlugin()
    try:
        _instance.register()
    except Exception:
        pass


# ---- fallback when loaded outside KiCad (manual testing / CLI) ----------------
def run_gui():
    import wx

    from bom2kicad_gui import Bom2LibDialog

    app = wx.App(False)
    dlg = Bom2LibDialog(None)
    dlg.ShowModal()
    dlg.Destroy()


if __name__ == "__main__":
    run_gui()
