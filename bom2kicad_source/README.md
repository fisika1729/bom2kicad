# BOM2Lib - EasyEDA BOM to KiCad project library

A KiCad Action Plugin that builds a complete, **project-local library** from an
EasyEDA BOM export (CSV). Instead of copy-pasting LCSC part numbers into
easyeda2kicad one by one, pick the BOM, give a project name and everything is
downloaded and written into a single library owned by the project.

## What it does

1. Shows a dialog prompting for a **project name** and the **EasyEDA BOM file**.
2. Parses the BOM (handles UTF-16 / UTF-8, tab or comma separated) and extracts
   all unique **LCSC part numbers** (Supplier Part column).
3. For each part, downloads CAD data from the public EasyEDA API and generates:
   - **Symbol** → `<parent>/<project>/<project>.kicad_sym`
   - **Footprint** → `<parent>/<project>/<project>.pretty/<name>.kicad_mod`
   - **3D model** (optional) → `<parent>/<project>/<project>.3dshapes/*.wrl|.step`
4. Symbols automatically reference the matching footprint
   (`<project>:<footprint>`), and footprints reference their 3D model via
   `${KIPRJMOD}` - so the whole library moves with the project folder.
5. Creates project library tables (`sym-lib-table`, `fp-lib-table`) so the
   library is auto-registered for any KiCad project opened in that folder.
6. Optionally creates an empty KiCad project skeleton
   (`.kicad_pro`, `.kicad_sch`, `.kicad_pcb`) so you can open it right away.
7. If `kicad-cli` is available, the libraries are upgraded/validated into
   native KiCad 10 format automatically.

## Usage

- Open Pcbnew (or Eeschema) → **Tools → External Plugins → BOM2Lib**
  (or click the toolbar button).
- Enter a project name (auto-filled from the BOM filename), pick the BOM CSV,
  pick the parent folder (default `~/Documents/KiCad`), hit **Build library**.
- When done, open `<parent>/<project>/<project>.kicad_pro` in KiCad, place
  symbols from the `<project>` library - footprints and 3D models follow
  automatically.

## Requirements

- KiCad 10 (works in any KiCad 6+; generated libraries are validated/upgraded
  with kicad-cli when available)
- Python packages inside KiCad's Python: `requests`, `pydantic` (KiCad's
  bundled Python on most distros already has them via the LCSC Importer
  plugin; otherwise install into KiCad's Python)

## Command line (headless)

The core also works without KiCad:

    python3 core.py --bom BOM_xxx.csv --name MyProject --out ~/Documents/KiCad

## Notes

- Symbols, footprints and 3D models come from the EasyEDA/LCSC official
  library. EasyEDA library data remains (C) JLCEDA/EasyEDA and must be
  attributed when shared; commercial use in your own designs is allowed.
- Conversion is done by a vendored copy of
  [easyeda2kicad.py](https://github.com/uPesy/easyeda2kicad.py) (MIT license),
  with a fixed User-Agent so the EasyEDA API keeps responding.
- Re-running the plugin with the same BOM/project name is fast: parts already
  imported are cached in `.bom2kicad_cache.json` and skipped unless
  *Overwrite* is checked.
