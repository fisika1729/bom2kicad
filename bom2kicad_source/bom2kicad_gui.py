"""wx dialog for the BOM2Lib plugin."""

import os

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PARENT = os.path.join(os.path.expanduser("~"), "Documents", "KiCad")

WILDCARD = "EasyEDA BOM (*.csv)|*.csv;*.CSV|All files (*.*)|*.*"


def default_project_name(bom_path: str) -> str:
    stem = os.path.splitext(os.path.basename(bom_path))[0]
    stem = stem.replace(" ", "_")
    if stem.lower().startswith("bom_"):
        stem = stem[4:]
    # strip trailing EasyEDA export date (_..._2026-10-07)
    stem = stem.rsplit("_2", 1)[0] if "_2" in stem else stem
    return stem.strip("_") or "Project"


def run_build(bom_path, project_name, parent_dir, with_3d, with_step, overwrite,
              skeleton, log_cb, progress_cb):
    """Import shim so the dialog does not import core until needed."""
    from core import build_libraries

    return build_libraries(
        bom_path=bom_path,
        project_name=project_name,
        parent_dir=parent_dir,
        with_3d=with_3d,
        with_step=with_step,
        overwrite=overwrite,
        skeleton=skeleton,
        log_cb=log_cb,
        progress_cb=progress_cb,
    )


class Bom2LibDialog(object):
    pass


def _pump_events():
    import wx

    try:
        if wx.GetApp() is not None:
            wx.GetApp().Yield(True)
    except Exception:
        pass


def run_dialog(parent=None):
    import wx

    dlg = Bom2LibDialog(parent)
    try:
        dlg.ShowModal()
    finally:
        dlg.Destroy()


try:
    import wx

    class Bom2LibDialog(wx.Dialog):
        def __init__(self, parent=None):
            wx.Dialog.__init__(
                self,
                parent,
                title="BOM2Lib - EasyEDA BOM to KiCad project library",
                size=(660, 620),
            )
            panel = wx.Panel(self)
            vbox = wx.BoxSizer(wx.VERTICAL)

            info = wx.StaticText(
                panel,
                label=(
                    "Builds a project-local KiCad library from an EasyEDA BOM export.\n"
                    "Every LCSC part number in the BOM gets its symbol, footprint and\n"
                    "3D model downloaded from EasyEDA and added to one library."
                ),
            )
            vbox.Add(info, 0, wx.ALL, 10)

            grid = wx.FlexGridSizer(3, 2, 8, 8)
            grid.AddGrowableCol(1, 1)
            grid.Add(
                wx.StaticText(panel, label="Project name:"),
                0,
                wx.ALIGN_CENTER_VERTICAL,
            )
            self.name_ctrl = wx.TextCtrl(panel, value="")
            grid.Add(self.name_ctrl, 1, wx.EXPAND)

            grid.Add(
                wx.StaticText(panel, label="EasyEDA BOM (CSV):"),
                0,
                wx.ALIGN_CENTER_VERTICAL,
            )
            self.bom_picker = wx.FilePickerCtrl(
                panel, wildcard=WILDCARD, style=wx.FLP_USE_TEXTCTRL | wx.FLP_OPEN
            )
            grid.Add(self.bom_picker, 1, wx.EXPAND)

            grid.Add(
                wx.StaticText(panel, label="Create in (parent folder):"),
                0,
                wx.ALIGN_CENTER_VERTICAL,
            )
            self.dir_picker = wx.DirPickerCtrl(
                panel, path=DEFAULT_PARENT, style=wx.DIRP_USE_TEXTCTRL
            )
            grid.Add(self.dir_picker, 1, wx.EXPAND)
            vbox.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

            opts = wx.BoxSizer(wx.HORIZONTAL)
            self.chk_3d = wx.CheckBox(panel, label="3D models")
            self.chk_3d.SetValue(True)
            self.chk_step = wx.CheckBox(panel, label="STEP files")
            self.chk_step.SetValue(True)
            self.chk_skeleton = wx.CheckBox(panel, label="Project skeleton")
            self.chk_skeleton.SetValue(True)
            self.chk_overwrite = wx.CheckBox(panel, label="Overwrite")
            for c in (self.chk_3d, self.chk_step, self.chk_skeleton, self.chk_overwrite):
                opts.Add(c, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
            vbox.Add(opts, 0, wx.ALL, 10)

            self.import_btn = wx.Button(panel, label="Build library")
            vbox.Add(self.import_btn, 0, wx.ALIGN_CENTER | wx.BOTTOM, 8)

            self.gauge = wx.Gauge(panel, range=1, style=wx.GA_SMOOTH)
            vbox.Add(self.gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

            self.log_ctrl = wx.TextCtrl(
                panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP
            )
            vbox.Add(self.log_ctrl, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

            close_btn = wx.Button(panel, wx.ID_CANCEL, label="Close")
            vbox.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

            panel.SetSizer(vbox)

            self.bom_picker.Bind(wx.EVT_FILEPICKER_CHANGED, self.on_bom_changed)
            self.import_btn.Bind(wx.EVT_BUTTON, self.on_import)
            close_btn.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))

        def on_bom_changed(self, evt):
            path = self.bom_picker.GetPath()
            if path and os.path.isfile(path):
                self.name_ctrl.SetValue(default_project_name(path))

        def on_import(self, evt):
            import wx

            project_name = self.name_ctrl.GetValue().strip()
            bom = self.bom_picker.GetPath()
            parent = self.dir_picker.GetPath()
            if not project_name:
                wx.MessageBox(
                    "Enter a project name.", "BOM2Lib", wx.OK | wx.ICON_WARNING
                )
                return
            if not bom or not os.path.isfile(bom):
                wx.MessageBox(
                    "Select the EasyEDA BOM CSV file.",
                    "BOM2Lib",
                    wx.OK | wx.ICON_WARNING,
                )
                return
            if not parent or not os.path.isdir(parent):
                wx.MessageBox(
                    "Select an existing parent folder.",
                    "BOM2Lib",
                    wx.OK | wx.ICON_WARNING,
                )
                return

            self.import_btn.Disable()
            self.gauge.SetRange(1)
            self.gauge.SetValue(0)
            try:
                summary = run_build(
                    bom_path=bom,
                    project_name=project_name,
                    parent_dir=parent,
                    with_3d=self.chk_3d.GetValue(),
                    with_step=self.chk_step.GetValue(),
                    overwrite=self.chk_overwrite.GetValue(),
                    skeleton=self.chk_skeleton.GetValue(),
                    log_cb=self.log,
                    progress_cb=self.progress,
                )
            except Exception as e:
                self.log(f"FATAL: {e}")
                wx.MessageBox(
                    f"BOM2Lib failed:\n{e}", "BOM2Lib error", wx.OK | wx.ICON_ERROR
                )
                self.import_btn.Enable()
                return

            failed = summary.get("failed", [])
            msg = (
                f"Library built in:\n{summary['project_dir']}\n\n"
                f"Parts OK: {len(summary['ok'])}"
                f" ({len(summary['cached'])} already present), failed: {len(failed)}"
            )
            if failed:
                msg += "\n\nFailed parts:\n" + "\n".join(
                    f"  {lcsc}: {err}" for lcsc, err in failed[:20]
                )
            wx.MessageBox(msg, "BOM2Lib", wx.OK | wx.ICON_INFORMATION)
            self.import_btn.Enable()

        def log(self, msg):
            self.log_ctrl.AppendText(msg + "\n")
            _pump_events()

        def progress(self, done, total):
            self.gauge.SetRange(max(total, 1))
            self.gauge.SetValue(done)
            _pump_events()

except ImportError:
    # wx not available (e.g. headless testing of core logic only)
    pass
