"""
BOM2Lib core logic.

Parses an EasyEDA BOM (CSV export) and builds a self-contained KiCad project
library from the LCSC part numbers it contains:
  <project>/<project>.kicad_sym        (project symbol library)
  <project>/<project>.pretty/          (project footprint library)
  <project>/<project>.3dshapes/        (3D models: .wrl + optional .step)
  <project>/sym-lib-table              (project symbol library table)
  <project>/fp-lib-table               (project footprint library table)

Conversion is done by the vendored easyeda2kicad package (upPesy/uPesy
easyeda2kicad.py), which talks to the public EasyEDA/LCSC CAD data API.
Symbols/footprints/3D models from the EasyEDA official library remain
(C) JLCEDA/EasyEDA - see the licence note in their API responses; commercial
use in circuit designs is allowed with attribution.
"""

import csv
import io
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(PLUGIN_DIR, "vendor")

LCSC_RE = re.compile(r"^[CS][0-9]{3,}$")
UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")

log = logging.getLogger("bom2kicad")


def _setup_vendor_path() -> None:
    if VENDOR_DIR not in sys.path:
        sys.path.insert(0, VENDOR_DIR)
    if PLUGIN_DIR not in sys.path:
        sys.path.insert(0, PLUGIN_DIR)


def check_dependencies() -> List[str]:
    """Return list of missing third party deps (empty == ok)."""
    missing = []
    for mod in ("requests", "pydantic"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return missing


def decode_escapes(s: str) -> str:
    if "\\u" in s:
        return UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), s)
    return s


@dataclass
class BomPart:
    row: int
    name: str = ""
    designators: str = ""
    footprint_name: str = ""
    mpn: str = ""
    manufacturer: str = ""
    lcsc_id: str = ""
    quantity: int = 0


def _read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    return text


def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:5])
    for delim in ("\t", ","):
        counts = [line.count(delim) for line in text.splitlines()[:5] if line.strip()]
        if counts and max(counts) > 0 and min(counts) == max(counts):
            return delim
    return "\t"


def parse_bom(path: str) -> Tuple[List[BomPart], List[str]]:
    """Parse an EasyEDA BOM export. Returns (parts, warnings)."""
    warnings: List[str] = []
    text = _read_text(path)
    delim = _sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("BOM file appears to be empty")

    header = [h.strip().strip('"').lower() for h in rows[0]]

    def col(*names: str) -> Optional[int]:
        for n in names:
            for i, h in enumerate(header):
                if h == n or h.replace(" ", "") == n:
                    return i
        return None

    idx = {
        "name": col("name", "value"),
        "designator": col("designator", "designators", "reference"),
        "footprint": col("footprint"),
        "mpn": col("manufacturer part", "mpn", "part number"),
        "manufacturer": col("manufacturer"),
        "supplier_part": col("supplier part", "lcsc part", "lcsc", "supplier"),
        "qty": col("quantity", "qty"),
    }
    if idx["supplier_part"] is None:
        raise ValueError(
            "Could not find a 'Supplier Part' / 'LCSC Part' column in the BOM header"
        )

    parts: List[BomPart] = []
    for r_i, row in enumerate(rows[1:], start=2):
        if len(row) <= idx["supplier_part"]:
            warnings.append(f"row {r_i}: too few columns, skipped")
            continue
        raw_id = row[idx["supplier_part"]].strip().strip('"').upper()
        # multiple codes or suffixes like "C1713,C15850"
        codes = [m for m in re.findall(r"[CS][0-9]{3,}", raw_id)]
        if not codes:
            warnings.append(f"row {r_i}: no LCSC part number ('{raw_id}'), skipped")
            continue
        if len(codes) > 1:
            warnings.append(
                f"row {r_i}: multiple part numbers {codes}, using the first"
            )

        def _get(key: str) -> str:
            i = idx.get(key)
            if i is not None and i < len(row):
                return decode_escapes(row[i].strip().strip('"'))
            return ""

        qty = 0
        try:
            qty = int(float(_get("qty")))
        except ValueError:
            pass

        parts.append(
            BomPart(
                row=r_i,
                name=_get("name"),
                designators=_get("designator"),
                footprint_name=_get("footprint"),
                mpn=_get("mpn"),
                manufacturer=_get("manufacturer"),
                lcsc_id=codes[0],
                quantity=qty,
            )
        )
    if not parts:
        raise ValueError("No rows with LCSC part numbers found in the BOM")
    return parts, warnings


def sanitize_name(name: str) -> str:
    name = name.strip().replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9_.+-]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return name or "BOM_Library"


# ----------------------------------------------------------------------------
# Symbol / footprint / 3D generation via vendored easyeda2kicad
# ----------------------------------------------------------------------------


class LibBuilder:
    def __init__(
        self,
        project_name: str,
        parent_dir: str,
        with_3d: bool = True,
        with_step: bool = True,
        overwrite: bool = False,
        skeleton: bool = True,
        log_cb: Optional[Callable[[str], None]] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ):
        self.project_name = sanitize_name(project_name)
        self.parent_dir = os.path.abspath(parent_dir)
        self.project_dir = os.path.join(self.parent_dir, self.project_name)
        self.pretty_dir = os.path.join(self.project_dir, self.project_name + ".pretty")
        self.shapes_dir = os.path.join(self.project_dir, self.project_name + ".3dshapes")
        self.sym_lib_path = os.path.join(self.project_dir, self.project_name + ".kicad_sym")
        self.cache_path = os.path.join(self.project_dir, ".bom2kicad_cache.json")
        self.with_3d = with_3d
        self.with_step = with_step
        self.overwrite = overwrite
        self.skeleton = skeleton
        self.log_cb = log_cb or (lambda msg: None)
        self.progress_cb = progress_cb or (lambda done, total: None)

        self._symbol_blocks: Dict[str, Tuple[str, str]] = {}  # name -> (block, lcsc)
        self._footprints: Dict[str, str] = {}  # name -> lcsc
        self.errors: List[Tuple[str, str]] = []

        self._api = None
        self._cache: Dict[str, Dict] = {}

    # -- logging helpers -----------------------------------------------------
    def log(self, msg: str) -> None:
        log.info(msg)
        self.log_cb(msg)

    # -- API access ----------------------------------------------------------
    @property
    def api(self):
        if self._api is None:
            from easyeda2kicad.easyeda.easyeda_api import EasyedaApi

            self._api = EasyedaApi()
        return self._api

    def _get_cad_data(self, lcsc_id: str) -> dict:
        return self.api.get_cad_data_of_component(lcsc_id=lcsc_id)

    # -- file tree -----------------------------------------------------------
    def create_tree(self) -> None:
        os.makedirs(self.pretty_dir, exist_ok=True)
        os.makedirs(self.shapes_dir, exist_ok=True)
        if not os.path.isfile(self.sym_lib_path):
            with open(self.sym_lib_path, "w", encoding="utf-8") as f:
                f.write(
                    "(kicad_symbol_lib\n"
                    "  (version 20211014)\n"
                    '  (generator "bom2kicad")\n'
                    ")\n"
                )
            self.log(f"Created symbol library: {self.sym_lib_path}")

    # -- cache ----------------------------------------------------------------
    def _load_cache(self) -> None:
        if os.path.isfile(self.cache_path):
            import json

            try:
                with open(self.cache_path, encoding="utf-8") as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        import json

        with open(self.cache_path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, indent=2, ensure_ascii=False)

    # -- part import ----------------------------------------------------------
    def add_part(self, lcsc_id: str) -> str:
        """Import one LCSC part into the project libraries. Returns status."""
        if not self.overwrite and lcsc_id in self._cache:
            cached = self._cache[lcsc_id]
            if os.path.isfile(os.path.join(self.pretty_dir, cached["fp"] + ".kicad_mod")):
                return "cached"

        cad_data = self._get_cad_data(lcsc_id)
        if not cad_data:
            raise RuntimeError("EasyEDA API returned no data for this part")

        status = ""

        # ---- symbol ----
        from easyeda2kicad.easyeda.easyeda_importer import EasyedaSymbolImporter
        from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad
        from easyeda2kicad.kicad.parameters_kicad_symbol import KicadVersion

        symbol = EasyedaSymbolImporter(easyeda_cp_cad_data=cad_data).get_symbol()
        symbol_name = re.sub(r"\s+", "_", symbol.info.name)

        # collision handling: same name, different LCSC id
        existing = self._symbol_blocks.get(symbol_name)
        if existing and existing[1] != lcsc_id:
            symbol.info.name = symbol_name + "_" + lcsc_id
            symbol_name = symbol.info.name

        exporter = ExporterSymbolKicad(
            symbol=symbol, kicad_version=KicadVersion.v6
        )
        block = exporter.export(footprint_lib_name=self.project_name)

        already_in_lib = self._symbol_in_lib(symbol_name)
        if already_in_lib and not self.overwrite:
            status = "exists"
        else:
            self._append_symbol_to_lib(symbol_name, block)
            status = "ok" if status == "" else status

        self._symbol_blocks[symbol_name] = (block, lcsc_id)

        # ---- footprint ----
        from easyeda2kicad.easyeda.easyeda_importer import EasyedaFootprintImporter
        from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad

        footprint = EasyedaFootprintImporter(easyeda_cp_cad_data=cad_data).get_footprint()
        fp_name = footprint.info.name
        fp_path = os.path.join(self.pretty_dir, fp_name + ".kicad_mod")
        if os.path.isfile(fp_path) and not self.overwrite:
            self.log(f"  footprint {fp_name}.kicad_mod already exists, skipped")
        else:
            ExporterFootprintKicad(footprint=footprint).export(
                footprint_full_path=fp_path,
                model_3d_path="${KIPRJMOD}/" + self.project_name + ".3dshapes",
            )

        # ---- 3D model ----
        if self.with_3d:
            self._add_3d_model(cad_data, fp_name)

        # ---- cache ----
        self._cache[lcsc_id] = {"symbol": symbol_name, "fp": fp_name}
        self._save_cache()
        return status

    def _symbol_in_lib(self, name: str) -> bool:
        if not os.path.isfile(self.sym_lib_path):
            return False
        with open(self.sym_lib_path, encoding="utf-8") as f:
            content = f.read()
        return re.search(rf'\n  \(symbol "{re.escape(name)}".*?\n  \)', content, re.DOTALL)

    def _append_symbol_to_lib(self, name: str, block: str) -> None:
        """Append or replace a symbol block in the .kicad_sym file."""
        with open(self.sym_lib_path, encoding="utf-8") as f:
            content = f.read()
        pattern = rf'\n  \(symbol "{re.escape(name)}".*?\n  \)'
        new_content, n = re.subn(pattern, block, content, flags=re.DOTALL)
        if n == 0:
            # append before the final closing paren of the library
            content = content.rstrip()
            if content.endswith(")"):
                content = content[:-1].rstrip()
            new_content = content + "\n\n" + block.rstrip() + "\n)"
        with open(self.sym_lib_path, "w", encoding="utf-8") as f:
            f.write(new_content)

    def _add_3d_model(self, cad_data: dict, fp_name: str) -> None:
        from easyeda2kicad.easyeda.easyeda_importer import Easyeda3dModelImporter
        from easyeda2kicad.kicad.export_kicad_3d_model import Exporter3dModelKicad

        model = Easyeda3dModelImporter(
            easyeda_cp_cad_data=cad_data, download_raw_3d_model=True
        ).output
        if model is None:
            self.log("  no 3D model available for this part")
            return
        exporter = Exporter3dModelKicad(model_3d=model)
        # upstream exporter appends ".3dshapes" to lib_path itself,
        # so lib_path must be <project_dir>/<project_name> (without .3dshapes)
        exporter.export(lib_path=os.path.join(self.project_dir, self.project_name))
        wrl = os.path.join(self.shapes_dir, exporter.output.name + ".wrl")
        step = os.path.join(self.shapes_dir, exporter.output.name + ".step")
        self.log(
            "  3D model: "
            + (os.path.basename(wrl) if os.path.isfile(wrl) else "-")
            + (" + " + os.path.basename(step) if self.with_step and os.path.isfile(step) else "")
        )

    # -- lib tables ------------------------------------------------------------
    def write_lib_tables(self) -> None:
        self._write_lib_table(
            path=os.path.join(self.project_dir, "sym-lib-table"),
            table="sym_lib_table",
            uri="${KIPRJMOD}/" + os.path.basename(self.sym_lib_path),
            descr=f"Project symbols imported from EasyEDA BOM (via bom2kicad)",
        )
        self._write_lib_table(
            path=os.path.join(self.project_dir, "fp-lib-table"),
            table="fp_lib_table",
            uri="${KIPRJMOD}/" + self.project_name + ".pretty",
            descr=f"Project footprints imported from EasyEDA BOM (via bom2kicad)",
        )

    def _write_lib_table(self, path: str, table: str, uri: str, descr: str) -> None:
        entry = (
            f'  (lib (name "{self.project_name}")'
            f'(type "KiCad")(uri "{uri}")'
            f'(options "")(descr "{descr}"))\n'
        )
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                content = f.read()
            if f'(name "{self.project_name}")' in content:
                return
            if content.rstrip().endswith(")"):
                content = content.rstrip()[:-1].rstrip("\n") + "\n" + entry + ")\n"
            else:
                content += "\n" + entry + ")\n"
        else:
            content = f"({table}\n  (version 7)\n{entry})\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.log(f"Created {os.path.basename(path)}: {path}")

    # -- project skeleton --------------------------------------------------------
    def write_project_skeleton(self, bom_path: Optional[str]) -> None:
        has_pro = os.path.isfile(
            os.path.join(self.project_dir, self.project_name + ".kicad_pro")
        )
        has_sch = os.path.isfile(
            os.path.join(self.project_dir, self.project_name + ".kicad_sch")
        )
        has_pcb = os.path.isfile(
            os.path.join(self.project_dir, self.project_name + ".kicad_pcb")
        )
        if not has_pro:
            import json

            pro = {
                "meta": {
                    "filename": self.project_name + ".kicad_pro",
                    "version": 3,
                },
                "libraries": {
                    "pinned_symbol_libs": [],
                    "pinned_footprint_libs": [],
                },
                "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
                "sheets": [],
                "text_variables": {},
            }
            with open(
                os.path.join(self.project_dir, self.project_name + ".kicad_pro"),
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(pro, f, indent=2)
        if not has_sch:
            with open(
                os.path.join(self.project_dir, self.project_name + ".kicad_sch"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(
                    "(kicad_sch\n"
                    "\t(version 20250114)\n"
                    '\t(generator "bom2kicad")\n'
                    '\t(generator_version "9.0")\n'
                    f'\t(uuid "{uuid.uuid4()}")\n'
                    '\t(paper "A4")\n'
                    "\t(lib_symbols)\n"
                    "\t(embedded_fonts no)\n"
                    "\t(sheet_instances\n"
                    '\t\t(path "/"\n'
                    '\t\t\t(page "1")\n'
                    "\t\t)\n"
                    "\t)\n"
                    ")\n"
                )
        if not has_pcb:
            with open(
                os.path.join(self.project_dir, self.project_name + ".kicad_pcb"),
                "w",
                encoding="utf-8",
            ) as f:
                f.write(_EMPTY_PCB_TEMPLATE)
        if not (has_pro and has_sch and has_pcb):
            self.log(
                "Created KiCad project skeleton in "
                + self.project_dir
                + " (schematic/pcb were empty placeholders)"
            )

    # -- optional kicad-cli upgrade (validates + upgrades file format) -----------
    def upgrade_with_kicad_cli(self) -> None:
        exe = _find_kicad_cli()
        if not exe:
            self.log("kicad-cli not found, skipping format upgrade (files still valid)")
            return
        try:
            r = subprocess.run(
                [exe, "sym", "upgrade", self.sym_lib_path],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if r.returncode == 0:
                self.log("Symbol library upgraded to native KiCad format")
            else:
                self.log(f"sym upgrade warning: {(r.stderr or '').strip()[:200]}")
            r = subprocess.run(
                [exe, "fp", "upgrade", self.pretty_dir],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if r.returncode == 0:
                self.log("Footprints upgraded to native KiCad format")
            else:
                self.log(f"fp upgrade warning: {(r.stderr or '').strip()[:200]}")
        except Exception as e:
            self.log(f"kicad-cli upgrade skipped ({e})")


def _find_kicad_cli() -> Optional[str]:
    candidates = [shutil.which("kicad-cli")]
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(os.path.join(appdir, "usr", "bin", "kicad-cli"))
    # KiCad AppImages extract to /tmp/.mount_* when run with --appimage-extract
    import glob as _glob

    for pattern in (
        "/tmp/.mount_kicad*/usr/bin/kicad-cli",
        "/tmp/.mount_*/usr/bin/kicad-cli",
    ):
        candidates.extend(sorted(_glob.glob(pattern)))
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    # last resort: extract a KiCad AppImage found in ~/Downloads (cached)
    dl = os.path.join(os.path.expanduser("~"), "Downloads")
    for appimage in sorted(_glob.glob(os.path.join(dl, "kicad-*.AppImage"))):
        squash = appimage + ".squashfs-root"
        cli = os.path.join(squash, "usr", "bin", "kicad-cli")
        if os.path.isfile(cli):
            return cli
        try:
            env = dict(os.environ)
            env["APPIMAGE_EXTRACT_AND_RUN"] = "1"
            subprocess.run(
                [appimage, "--appimage-extract"],
                cwd=dl,
                capture_output=True,
                timeout=600,
                env=env,
            )
            for root in (
                os.path.join(dl, "squashfs-root"),
                squash,
            ):
                c = os.path.join(root, "usr", "bin", "kicad-cli")
                if os.path.isfile(c):
                    return c
        except Exception:
            continue
    return None


def find_kicad_cli() -> Optional[str]:
    return _find_kicad_cli()


_EMPTY_PCB_TEMPLATE = """\
(kicad_pcb
\t(version 20250114)
\t(generator "bom2kicad")
\t(generator_version "9.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(1 "In1.Cu" signal)
\t\t(2 "In2.Cu" signal)
\t\t(3 "In3.Cu" signal)
\t\t(4 "In4.Cu" signal)
\t\t(5 "In5.Cu" signal)
\t\t(6 "In6.Cu" signal)
\t\t(31 "B.Cu" signal)
\t\t(32 "B.Adhes" user "B.Adhesive")
\t\t(33 "F.Adhes" user "F.Adhesive")
\t\t(34 "B.Paste" user)
\t\t(35 "F.Paste" user)
\t\t(36 "B.SilkS" user "B.Silkscreen")
\t\t(37 "F.SilkS" user "F.Silkscreen")
\t\t(38 "B.Mask" user)
\t\t(39 "F.Mask" user)
\t\t(40 "Dwgs.User" user "User.Drawings")
\t\t(41 "Cmts.User" user "User.Comments")
\t\t(42 "Eco1.User" user "User.Eco1")
\t\t(43 "Eco2.User" user "User.Eco2")
\t\t(44 "Edge.Cuts" user)
\t\t(45 "Margin" user)
\t\t(46 "B.CrtYd" user "B.Courtyard")
\t\t(47 "F.CrtYd" user "F.Courtyard")
\t\t(48 "B.Fab" user)
\t\t(49 "F.Fab" user)
\t)
\t(net 0 "")
\t(embedded_fonts no)
)
"""


# ----------------------------------------------------------------------------
# top level orchestration
# ----------------------------------------------------------------------------


def build_libraries(
    bom_path: str,
    project_name: str,
    parent_dir: str,
    with_3d: bool = True,
    with_step: bool = True,
    overwrite: bool = False,
    skeleton: bool = True,
    log_cb: Optional[Callable[[str], None]] = None,
    progress_cb: Optional[Callable[[int], None]] = None,
) -> Dict:
    """Build the project library from an EasyEDA BOM.

    Returns a summary dict:
      { ok: [...], cached: [...], failed: [(lcsc_id, err)], project_dir, ... }
    """
    _setup_vendor_path()
    missing = check_dependencies()
    if missing:
        raise RuntimeError(
            "Missing dependencies: "
            + ", ".join(missing)
            + "\nInstall them into KiCad's Python (see plugin README)."
        )

    parts, warnings = parse_bom(bom_path)
    if log_cb:
        for w in warnings:
            log_cb("BOM: " + w)

    # route easyeda2kicad warnings (e.g. rate limiting) into the visible log
    class _Relay(logging.Handler):
        def emit(self, record):
            try:
                msg = record.getMessage()
                if record.levelno >= logging.WARNING and log_cb:
                    log_cb(msg)
            except Exception:
                pass

    relay = _Relay()
    logging.getLogger("easyeda2kicad").addHandler(relay)

    # dedupe LCSC ids, keep first occurrence info
    unique: Dict[str, BomPart] = {}
    for p in parts:
        unique.setdefault(p.lcsc_id, p)

    builder = LibBuilder(
        project_name=project_name,
        parent_dir=parent_dir,
        with_3d=with_3d,
        with_step=with_step,
        overwrite=overwrite,
        skeleton=skeleton,
        log_cb=log_cb,
        progress_cb=progress_cb,
    )
    builder.log(f"Project directory: {builder.project_dir}")
    builder.log(f"Parts in BOM: {len(parts)} rows, {len(unique)} unique LCSC parts")
    builder.create_tree()
    builder._load_cache()

    ok, cached, failed = [], [], []
    import time

    for i, (lcsc_id, part) in enumerate(sorted(unique.items())):
        try:
            builder.log(f"[{i+1}/{len(unique)}] {lcsc_id} - {part.name or part.mpn}")
            status = builder.add_part(lcsc_id)
            if status == "cached":
                cached.append(lcsc_id)
                ok.append(lcsc_id)
            elif status == "exists":
                cached.append(lcsc_id)
                ok.append(lcsc_id)
            else:
                ok.append(lcsc_id)
        except Exception as e:
            failed.append((lcsc_id, str(e)))
            log.error(f"{lcsc_id}: {e}")
            builder.log(f"  ERROR {lcsc_id}: {e}")
        builder.progress_cb(i + 1, len(unique))
        # gentle pacing to stay under the EasyEDA API rate limit
        time.sleep(4.0 + random.uniform(0, 1.5))

    builder.write_lib_tables()
    if skeleton:
        builder.write_project_skeleton(bom_path=bom_path)

    # copy BOM into project for reference
    try:
        shutil.copy2(
            bom_path,
            os.path.join(
                builder.project_dir, builder.project_name + "_bom_import.csv"
            ),
        )
    except Exception:
        pass

    summary = {
        "ok": ok,
        "cached": cached,
        "failed": failed,
        "parts": parts,
        "project_dir": builder.project_dir,
        "project_name": builder.project_name,
        "sym_lib": builder.sym_lib_path,
        "pretty_dir": builder.pretty_dir,
        "shapes_dir": builder.shapes_dir,
    }
    builder.log(
        f"Done: {len(ok)} imported ({len(cached)} already present), {len(failed)} failed"
    )
    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build a KiCad project library from an EasyEDA BOM")
    ap.add_argument("--bom", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-3d", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    summary = build_libraries(
        bom_path=args.bom,
        project_name=args.name,
        parent_dir=args.out,
        with_3d=not args.no_3d,
        overwrite=args.overwrite,
        log_cb=print,
        progress_cb=lambda done, total: None,
    )
    print(summary)
