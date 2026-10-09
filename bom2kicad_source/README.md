# BOM2Lib — EasyEDA BOM to KiCad project library

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![KiCad](https://img.shields.io/badge/KiCad-10.0-blue)

A KiCad plugin that builds a complete, **project-local library** from an EasyEDA
BOM export (CSV). Instead of copy-pasting LCSC part numbers into easyeda2kicad
one by one, pick the BOM, type a project name and every part — symbol, footprint
and 3D model — is downloaded into one library owned by the project.

## Install

**From file (PCM):** download `bom2kicad-<version>-pcm.zip` from
[Releases](https://github.com/fisika1729/bom2kicad/releases), then in KiCad:
**Plugin and Content Manager → Install from File...**

**Build the zip yourself:**

```
python3 build_pcm.py out/bom2kicad-1.0.0-pcm.zip
```

> If you also copied the plugin into `scripting/plugins/` manually, delete that
> copy before installing via PCM, otherwise the menu shows duplicate entries.

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
   `${KIPRJMOD}` — so the whole library moves with the project folder.
5. Creates project library tables (`sym-lib-table`, `fp-lib-table`) so the
   library is auto-registered for any KiCad project opened in that folder.
6. Optionally creates an empty KiCad project skeleton
   (`.kicad_pro`, `.kicad_sch`, `.kicad_pcb`) so you can open it right away.
7. If `kicad-cli` is available (including inside the KiCad AppImage), the
   libraries are upgraded/validated into native KiCad 10 format automatically.

Resulting folder layout:

```
~/Documents/KiCad/MyProject/
├── MyProject.kicad_pro / .kicad_sch / .kicad_pcb   (empty skeleton)
├── MyProject.kicad_sym
├── MyProject.pretty/                          (footprints)
├── MyProject.3dshapes/                        (wrl + step)
├── sym-lib-table / fp-lib-table               (auto-registered)
└── MyProject_bom_import.csv                    (BOM copy for reference)
```

## Usage

- Pcbnew (or Eeschema) → **Tools → External Plugins → BOM2Lib**
  (or click the toolbar button).
- Enter a project name (auto-filled from the BOM filename), pick the BOM CSV,
  pick the parent folder (default `~/Documents/KiCad`), hit **Build library**.
- When done, open `<parent>/<project>/<project>.kicad_pro` in KiCad and place
  symbols from the `<project>` library — footprints and 3D models follow
  automatically.

## Command line (headless)

The core also works without KiCad:

```
python3 core.py --bom BOM_xxx.csv --name MyProject --out ~/Documents/KiCad
```

## Notes

- **Bundled dependencies** (`site-packages/`): `requests`, `pydantic` and friends
  ship inside the package, so no pip installs are needed in KiCad's Python.
  The `pydantic_core` wheel targets CPython 3.11, which is what KiCad 10
  bundles (minimum tested version). The conversion layer itself is a vendored
  copy of [easyeda2kicad.py](https://github.com/uPesy/easyeda2kicad.py) (MIT),
  patched for a current User-Agent and request patience.
- **EasyEDA rate limiting**: the API allows roughly ~15 requests/minute.
  The plugin paces requests (~4 s apart) and automatically waits out HTTP 403
  blocks, so a 50-part BOM takes a few minutes — progress is shown in the
  dialog. Big BOMs may be safer to import in one pass and let the cache
  resume on failure: parts already imported are cached in
  `.bom2kicad_cache.json` and skipped on re-runs unless *Overwrite* is checked.
- **Licensing**: symbols, footprints and 3D models come from the
  EasyEDA/LCSC official library and remain (C) JLCEDA/EasyEDA — attribute
  them when sharing; use in your own commercial designs is allowed. Plugin
  code is MIT (see [LICENSE](LICENSE)).

## Publishing

See [README_PUBLISHING.md](README_PUBLISHING.md) for rebuilding the PCM
package and submitting to the official KiCad addon repository
(package id `com.github.fisika1729.bom2kicad`).

## Roadmap / ideas

- Map BOM designators onto placed symbols to pre-populate the schematic
- Optional "update library" pass that checks for newer part revisions
- Per-part exclusion flags for BOM-fitted vs personally selected alternatives
