"""wx dialog for the BOM2Lib plugin."""

import logging
import os
import subprocess
import sys
import threading
import traceback

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PARENT = os.path.join(os.path.expanduser("~"), "Documents", "KiCad")
SITE_PKG = os.path.join(PLUGIN_DIR, "site-packages")

WILDCARD = "EasyEDA BOM (*.csv)|*.csv;*.CSV|All files (*.*)|*.*"
NETLIST_WILDCARD = (
    "Netlist / schematic export (*.net;*.cir;*.xml;*.Net)|"
    "*.net;*.cir;*.xml;*.Net|"
    "All files (*.*)|*.*"
)


def default_project_name(bom_path: str) -> str:
    stem = os.path.splitext(os.path.basename(bom_path))[0]
    stem = stem.replace(" ", "_")
    if stem.lower().startswith("bom_"):
        stem = stem[4:]
    # strip trailing EasyEDA export date (_..._2026-10-07)
    stem = stem.rsplit("_2", 1)[0] if "_2" in stem else stem
    return stem.strip("_") or "Project"


def check_missing() -> list:
    """Third-party modules BOM2Lib needs that are absent right now.

    The plugin pushes its bundled site-packages to the FRONT of sys.path
    (see __init__). Probing imports inside the KiCad process inherits
    whatever PYTHON* env the AppRun set, so if a PYTHONPATH-loaded copy of
    pydantic with a mismatched pydantic_core exists, it gets shadowed."""
    missing = []
    import importlib

    importlib.invalidate_caches()
    if SITE_PKG not in sys.path:
        sys.path.insert(0, SITE_PKG)
    for mod in ("requests", "pydantic"):
        try:
            __import__(mod)
        except Exception as e:
            import logging

            logging.exception("check_missing import failed for %s", mod)
            missing.append(mod)
    return missing


def _manual_install_message(missing) -> str:
    mods = " ".join(missing)
    site = SITE_PKG
    return (
        "BOM2Lib needs missing Python package(s):\n"
        "    " + ", ".join(missing) + "\n\n"
        "KiCad's bundled Python cannot run pip itself (it ships without pip/ssl),\n"
        "so install into BOM2Lib's own package folder from any Python that HAS pip:\n\n"
        f'    python3 -m pip install --only-binary=:all: --implementation cp '
        f'--python-version 3.11 --abi cp311 \\\n'
        f'        --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 '
        f'--platform manylinux_2_17_x86_64 \\\n'
        f'        --target "{site}" {mods}\n\n'
        "(matches KiCad's bundled CPython 3.11; the plugin folder becomes "
        "self-contained again). After installing, restart KiCad and run BOM2Lib."
    )


def _clean_env() -> dict:
    """Environment for child processes: drop PYTHON* leftovers that KiCad's
    AppRun may set, otherwise spawned host pythons inherit a poisoned
    search path and even `pip` fails to start."""
    return {
        k: v for k, v in os.environ.items()
        if not k.upper().startswith("PYTHON")
    }


def _pip_probe(python: str) -> bool:
    cmd = [python, "-m", "pip", "--version"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                           env=_clean_env())
        return r.returncode == 0 and "pip" in (r.stdout or "").lower()
    except Exception:
        return False


def _pip_install_cmd(python: str, mods: list) -> list:
    import platform

    cmd = [python, "-m", "pip", "install", "--no-warn-script-location",
           "--upgrade", "--target", SITE_PKG, "--only-binary=:all:"]
    system = platform.system()
    if system == "Linux":
        # target KiCad's bundled CPython 3.11 ABI regardless of host python
        cmd += ["--implementation", "cp", "--python-version", "3.11",
                "--abi", "cp311"]
        for plat in ("manylinux2014_x86_64", "manylinux_2_28_x86_64",
                     "manylinux_2_17_x86_64"):
            cmd += ["--platform", plat]
    elif system == "Windows":
        cmd += ["--implementation", "cp", "--python-version", "3.11",
                "--abi", "cp311"]
        for plat in ("windows_amd64", "win_amd64"):
            cmd += ["--platform", plat]
    return cmd + mods


def _host_pips() -> list:
    import platform as _platform
    import shutil as _shutil

    out = []
    seen = set()
    if _platform.system() == "Windows":
        cands = [sys.executable, "python", "py", "python3"]
    else:
        cands = [sys.executable, "python3", "python3.11"]
    for c in cands:
        path = c if os.path.isabs(c) else (_shutil.which(c) or "")
        if not path or path in seen:
            continue
        seen.add(path)
        if _pip_probe(path):
            out.append(path)
    return out


def _install_for_me(parent, missing) -> str:
    """Install missing packages into the plugin folder using a host pip.

    KiCad's own bundled Python has neither pip nor ssl, so we ask the system
    Python to install version-matched wheels (--python-version 3.11 --abi
    cp311 on Linux) with --target into the plugin's site-packages."""
    import wx
    import importlib

    hosts = _host_pips()
    if not hosts:
        return (
            "Could not find a Python with pip on this machine "
            "(KiCad's bundled Python has no pip).\n\n"
            + _manual_install_message(missing)
        )

    prog = wx.ProgressDialog(
        "BOM2Lib - installing packages",
        f"Installing {', '.join(missing)} via pip...",
        maximum=100,
        parent=parent,
        style=wx.PD_APP_MODAL | wx.PD_ELAPSED_TIME,
    )
    _pump_events()
    clean = _clean_env()
    try:
        logs = []
        ok = False
        for host in hosts:
            cmd = _pip_install_cmd(host, missing)
            logs.append("$ " + " ".join(cmd))
            _pump_events()
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1200,
                               env=clean)
            if r.stdout:
                logs.append((r.stdout or "").strip()[-400:])
            if r.stderr:
                logs.append((r.stderr or "").strip()[-400:])
            if r.returncode == 0:
                importlib.invalidate_caches()
                still = check_missing()
                if not still:
                    ok = True
                    break
        prog.Destroy()
        _pump_events()
        if ok:
            return "ok"
        return "\n".join(logs)[-900:] + "\n\n" + _manual_install_message(missing)
    except Exception as e:
        try:
            prog.Destroy()
        except Exception:
            pass
        _pump_events()
        return f"Automatic install failed: {e}\n\n" + _manual_install_message(missing)


def prompt_missing_packages(parent=None):
    """If dependencies are missing, offer Yes (auto-install) / No (manual
    instructions). Returns True when the main dialog may be shown."""
    import wx

    missing = check_missing()
    if not missing:
        return True

    mods = ", ".join(missing)
    dlg = wx.MessageDialog(
        parent,
        f"BOM2Lib needs {mods} which are not installed.\n\n"
        "Do you want me to install them automatically for you?",
        "BOM2Lib - missing packages",
        wx.ICON_QUESTION | wx.YES_NO,
    )
    try:
        answer = dlg.ShowModal()
    finally:
        dlg.Destroy()

    if answer == wx.ID_YES:
        result = _install_for_me(parent, missing)
        if result == "ok":
            wx.MessageBox(
                "Packages installed successfully. Starting BOM2Lib...",
                "BOM2Lib", wx.OK | wx.ICON_INFORMATION,
            )
            return prompt_missing_packages(parent)
        wx.MessageBox(result, "BOM2Lib - install needed", wx.OK | wx.ICON_ERROR)
        return False

    # No -> leave a reminder of what to install and how
    wx.MessageBox(
        _manual_install_message(missing),
        "BOM2Lib - installation required",
        wx.OK | wx.ICON_INFORMATION,
    )
    return False


def run_build(bom_path, project_name, parent_dir, with_3d, with_step, overwrite,
              skeleton, log_cb, progress_cb, pump_cb=None, cancel_event=None):
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
        pump_cb=pump_cb,
        cancel_event=cancel_event,
    )


def _pump_events():
    """Keeps the dialog responsive WITHOUT a nested event loop.

    wx.App.Yield from inside a modal handler in KiCad's embedded wx tends to
    hit the recursion guard and silently abort, so the gauge/log repaints get
    swallowed - use ProcessPendingEvents + explicit widget updates instead."""
    import wx

    try:
        if wx.GetApp() is not None:
            wx.GetApp().ProcessPendingEvents()
    except Exception:
        pass


def compute_project_dir(parent: str, project_name: str):
    """Same selection rule core uses: when the parent itself contains
    `<name>.kicad_pro`, the parent IS the project folder."""
    import os as _os

    parent = _os.path.abspath(parent)
    direct = _os.path.join(parent, project_name + ".kicad_pro")
    if _os.path.isfile(direct):
        return parent
    return _os.path.join(parent, project_name)


def _find_kicad_cli_safe():
    try:
        from core import _find_kicad_cli

        return _find_kicad_cli()
    except Exception:
        return None


def run_dialog(parent=None, project_name="", project_dir=""):
    import wx

    if not prompt_missing_packages(parent):
        return
    dlg = Bom2LibDialog(parent, project_name=project_name, project_dir=project_dir)
    try:
        dlg.ShowModal()
    finally:
        dlg.Destroy()


class Bom2LibDialog(object):
    pass


try:
    import wx

    class Bom2LibDialog(wx.Dialog):
        def __init__(self, parent=None, project_name="", project_dir=""):
            wx.Dialog.__init__(
                self,
                parent,
                title="BOM2Lib - EasyEDA BOM to KiCad project library",
                size=(660, 640),
            )
            panel = wx.Panel(self)
            vbox = wx.BoxSizer(wx.VERTICAL)

            has_project = bool(project_name and project_dir)
            top_label = "Running against the project you have open:" \
                if has_project else \
                "Builds a project-local KiCad library from an EasyEDA BOM export."
            info = wx.StaticText(panel, label=top_label)
            vbox.Add(info, 0, wx.ALL, 10)
            if has_project:
                info2 = wx.StaticText(
                    panel,
                    label=f"    {os.path.join(project_dir, project_name)}.kicad_pro",
                )
                vbox.Add(info2, 0, wx.LEFT | wx.BOTTOM, 10)

            grid = wx.FlexGridSizer(3, 2, 8, 8)
            grid.AddGrowableCol(1, 1)
            grid.Add(
                wx.StaticText(panel, label="Project name:"),
                0,
                wx.ALIGN_CENTER_VERTICAL,
            )
            self.name_ctrl = wx.TextCtrl(panel, value=project_name or "")
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
                panel,
                path=project_dir or DEFAULT_PARENT,
                style=wx.DIRP_USE_TEXTCTRL,
            )
            grid.Add(self.dir_picker, 1, wx.EXPAND)

            grid.Add(
                wx.StaticText(panel,
                              label="Netlist / schematic export\n(optional, for annotation migration):"),
                0,
                wx.ALIGN_CENTER_VERTICAL,
            )
            self.net_picker = wx.FilePickerCtrl(
                panel, wildcard=NETLIST_WILDCARD,
                style=wx.FLP_USE_TEXTCTRL | wx.FLP_OPEN,
            )
            grid.Add(self.net_picker, 1, wx.EXPAND)
            netlist_hint = wx.StaticText(
                panel,
                label=(
                    "BOM only  -> symbols + footprints.\n"
                    "BOM + netlist -> also places parts in the schematic with\n"
                    "their annotations (OrCAD .net, Allegro 3rd-party,\n"
                    "Xpedition/Allegro IPC-2581 .xml, Altium .Net)."
                ),
            )
            netlist_hint.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                         wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
            vbox.Add(netlist_hint, 0, wx.LEFT | wx.RIGHT, 10)
            vbox.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

            if has_project:
                keep = wx.CheckBox(panel, label="Keep these name/folder (use the open project)")
                keep.SetValue(True)
                self.keep_proj = keep
                vbox.Add(keep, 0, wx.LEFT | wx.RIGHT, 10)

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

            btn_row = wx.BoxSizer(wx.HORIZONTAL)
            self.import_btn = wx.Button(panel, label="Build library")
            btn_row.Add(self.import_btn, 0, wx.RIGHT, 12)
            self.stop_btn = wx.Button(panel, label="Stop")
            self.stop_btn.Disable()
            btn_row.Add(self.stop_btn, 0, wx.RIGHT, 12)
            btn_row.AddStretchSpacer()
            vbox.Add(btn_row, 0, wx.EXPAND | wx.ALL, 10)

            self.gauge = wx.Gauge(panel, range=1, style=wx.GA_SMOOTH)
            vbox.Add(self.gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

            self.log_ctrl = wx.TextCtrl(
                panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP
            )
            vbox.Add(self.log_ctrl, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

            close_btn = wx.Button(panel, wx.ID_CANCEL, label="Close")
            vbox.Add(close_btn, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

            panel.SetSizer(vbox)

            self._thread = None
            self._cancel = None
            self._running = False
            self.close_btn = close_btn

            self.bom_picker.Bind(wx.EVT_FILEPICKER_CHANGED, self.on_bom_changed)
            self.import_btn.Bind(wx.EVT_BUTTON, self.on_import)
            self.stop_btn.Bind(wx.EVT_BUTTON, self.on_stop)
            close_btn.Bind(wx.EVT_BUTTON, self.on_close_clicked)
            self.Bind(wx.EVT_CLOSE, self.on_window_close)

        # ------------------------------------------------- run / stop plumbing
        def _busy(self) -> bool:
            return self._running and self._thread is not None \
                and self._thread.is_alive()

        def on_close_clicked(self, evt):
            if self._busy():
                self._ask_stop()
                return
            self.EndModal(wx.ID_CANCEL)

        def on_window_close(self, evt):
            if self._busy():
                self._ask_stop()
                evt.Veto()
                return
            evt.Skip()

        def on_stop(self, evt):
            self._ask_stop()

        def _ask_stop(self):
            import wx

            if not self._busy():
                return
            if self._cancel is not None and self._cancel.is_set():
                return
            dlg = wx.MessageDialog(
                self,
                "Interrupt the BOM import?\n\nAlready imported parts remain in "
                "the library; the cache lets you resume later exactly where "
                "you stopped.",
                "BOM2Lib - interrupt?",
                wx.ICON_QUESTION | wx.YES_NO,
            )
            try:
                if dlg.ShowModal() == wx.ID_YES:
                    self._cancel.set()
                    self.log("Stop requested - finishing current part, "
                             "then interrupting...")
            finally:
                dlg.Destroy()

        def on_bom_changed(self, evt):
            path = self.bom_picker.GetPath()
            if path and os.path.isfile(path) and not self.name_ctrl.GetValue().strip():
                self.name_ctrl.SetValue(default_project_name(path))

        def on_import(self, evt):
            import wx

            project_name = self.name_ctrl.GetValue().strip()
            bom = self.bom_picker.GetPath()
            parent = self.dir_picker.GetPath()
            netlist_path = self.net_picker.GetPath() if \
                hasattr(self, "net_picker") else ""
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
            if netlist_path and not os.path.isfile(netlist_path):
                wx.MessageBox(
                    "The netlist/schematic export file does not exist.",
                    "BOM2Lib", wx.OK | wx.ICON_WARNING,
                )
                return

            self.import_btn.Disable()
            self.stop_btn.Enable()
            self.gauge.SetRange(1)
            self.gauge.SetValue(0)
            self._cancel = threading.Event()
            self._running = True
            self.log("Starting build in the background...")

            args = (
                bom, project_name, parent,
                self.chk_3d.GetValue(), self.chk_step.GetValue(),
                self.chk_overwrite.GetValue(), self.chk_skeleton.GetValue(),
                netlist_path,
            )
            self._thread = threading.Thread(
                target=self._build_worker, args=args, daemon=True
            )
            self._thread.start()

        def _build_worker(self, bom, project_name, parent, with_3d, with_step,
                          overwrite, skeleton, netlist_path=""):
            """Runs in a background thread; all GUI interaction goes through
            wx.CallAfter so updates paint reliably from KiCad's event loop."""
            import traceback as _tb

            log_cb = lambda msg: wx.CallAfter(self.log, msg)
            progress_cb = lambda done, total: wx.CallAfter(
                self.progress, done, total
            )
            try:
                summary = run_build(
                    bom_path=bom,
                    project_name=project_name,
                    parent_dir=parent,
                    with_3d=with_3d,
                    with_step=with_step,
                    overwrite=overwrite,
                    skeleton=skeleton,
                    log_cb=log_cb,
                    progress_cb=progress_cb,
                    cancel_event=self._cancel,
                )
                # optional 2.0 step: migrate the netlist into the schematic
                # with matched symbols/footprints per annotation
                if netlist_path and not self._cancel.is_set():
                    if compute_project_dir(parent, project_name) is not None:
                        pass
                    wx.CallAfter(self.log, "--- Migrating netlist to schematic...")
                    migrate_summary = self._migrate_worker(
                        netlist_path, project_name, parent, log_cb
                    )
                    summary["migration"] = migrate_summary
                wx.CallAfter(self._build_done, summary, None)
            except Exception as e:
                wx.CallAfter(self._build_done, None, e)
                logging.exception("BOM2Lib worker failed")
            finally:
                wx.CallAfter(self._worker_finished)

        def _migrate_worker(self, netlist_path, project_name, parent, log_cb):
            """Runs in the worker thread after the library build; returns a
            small summary dict for the final dialog."""
            sys.path.insert(0, PLUGIN_DIR)
            try:
                from sch_import import migrate as sch_migrate

                if not parent or not project_name:
                    log_cb("migration skipped: project not resolved")
                    return {"ok": False, "error": "project not resolved"}
                # resolve the actual project dir the library was written to
                proj_dir = compute_project_dir(parent, project_name)
                kcli = _find_kicad_cli_safe()
                result = sch_migrate.migrate(
                    netlist_path=netlist_path,
                    project_dir=proj_dir,
                    project_name=project_name,
                    log_cb=lambda m: log_cb("  " + m),
                    kicad_cli=kcli,
                )
                return result
            except Exception as e:
                logging.exception("schematic migration failed")
                log_cb(f"SCHEMATIC MIGRATION ERROR: {e}")
                return {"ok": False, "error": str(e)[:300]}

        def _worker_finished(self):
            self._running = False
            self.stop_btn.Disable()

        def _build_done(self, summary, exc):
            if exc is not None:
                err = "".join(traceback.format_exception(
                    type(exc), exc, exc.__traceback__))[-1500:]
                self.log("FATAL: %s" % exc)
                wx.MessageBox(
                    "BOM2Lib failed:\n%s" % err,
                    "BOM2Lib error", wx.OK | wx.ICON_ERROR,
                )
                self.import_btn.Enable()
                return

            if summary.get("cancelled"):
                self.log("Build interrupted by user - resume anytime with "
                         "the same BOM and project name.")
                wx.MessageBox(
                    "Import interrupted.\n\n"
                    "Parts already fetched are stored (cache) in the project "
                    "folder - run the same BOM + project name again to resume "
                    "from where you stopped.",
                    "BOM2Lib", wx.OK | wx.ICON_INFORMATION,
                )
                self.import_btn.Enable()
                return

            failed = summary.get("failed", [])
            msg = (
                "Library built in:\n%s\n\nParts OK: %d (%d already present), "
                "failed: %d" % (
                    summary["project_dir"], len(summary["ok"]),
                    len(summary["cached"]), len(failed),
                )
            )
            if failed:
                msg += "\n\nFailed parts:\n" + "\n".join(
                    f"  {lcsc}: {err}" for lcsc, err in failed[:20]
                )
            mig = summary.get("migration")
            if mig:
                if mig.get("ok"):
                    msg += (
                        "\n\nSchematic migrated:\n"
                        f"{mig.get('target')}\n"
                        f"Parts placed: {mig.get('parts_placed')}, "
                        f"nets: {mig.get('nets_connected')}"
                    )
                    v = mig.get("verify")
                    if v:
                        msg += (
                            f"\nVerification: {v.get('status')}"
                            + (f" ({v.get('detail', '')[:120]})"
                               if v.get("status") == "error" else "")
                        )
                else:
                    msg += ("\n\nSchematic migration failed: "
                            + str(mig.get('error', ''))[:200])
            msg += (
                "\n\nTip: fully close and reopen this project (or restart "
                "KiCad) so its editors pick up the freshly written "
                "sym-lib-table / fp-lib-table."
            )
            wx.MessageBox(msg, "BOM2Lib", wx.OK | wx.ICON_INFORMATION)
            self.import_btn.Enable()

        def log(self, msg):
            self.log_ctrl.AppendText(msg + "\n")
            try:
                self.log_ctrl.Update()
            except Exception:
                pass

        def progress(self, done, total):
            self.gauge.SetRange(max(total, 1))
            self.gauge.SetValue(done)
            try:
                self.gauge.Update()
            except Exception:
                pass

except ImportError:
    # wx not available (e.g. headless testing of core logic only)
    pass
