# BOM2Lib — EasyEDA BOM to KiCad project library

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![KiCad](https://img.shields.io/badge/KiCad-10.0-blue)

A KiCad plugin that builds a complete, **project-local library** from an EasyEDA
BOM export (CSV). Instead of copy-pasting LCSC part numbers into easyeda2kicad
one by one, pick the BOM, type a project name and every part — symbol, footprint
and 3D model — is downloaded into one library owned by the project.

Needed from EasyEDA: export your project's **BOM as CSV** (the `Supplier Part`
column holds the LCSC part number, e.g. `C476766` — that is all the plugin
needs to look the part up).

## Install

**From file (PCM):** download `bom2kicad-<version>-pcm.zip` from
[Releases](https://github.com/fisika1729/bom2kicad/releases), then in KiCad:
**Plugin and Content Manager → Install from File...**

**Build the zip yourself:**

```
python3 build_pcm.py out/bom2kicad-1.0.1-pcm.zip
```

> If you also copied the plugin into `scripting/plugins/` manually, delete that
> copy before installing via PCM, otherwise the menu shows duplicate entries.

> Restart KiCad after installing — plugins are discovered at startup.

## Step-by-step usage (from an empty project to a full library)

**1. Create the project**

- Launch KiCad (AppImage → project manager).
- **File → New Project...** → give it a name (e.g. `test`) and location
  (e.g. `~/Documents/KiCad` — any folder works). KiCad creates:

  ```
  ~/Documents/KiCad/test/
  ├── test.kicad_pro
  ├── test.kicad_sch
  └── test.kicad_pcb
  ```

**2. Open the PCB editor**

- In the project manager click **PCB Editor**.
  (The plugin menu only exists in the PCB editor — the schematic editor and
  the launcher have no External Plugins menu.)

**3. Run the plugin**

- Menu: **Tools → External Plugins → BOM2Lib: create project library from
  EasyEDA BOM** (or the toolbar button).
- The dialog detects the project you have open and pre-fills:

  | Field | What to enter |
  |---|---|
  | Project name | auto-filled from your open project (`test`) — leave it so the library lands inside that project |
  | EasyEDA BOM (CSV) | pick the exported BOM file |
  | Create in (parent folder) | the project folder itself (`~/Documents/KiCad/test`) **or** its parent (`~/Documents/KiCad`) — both resolve into the project folder |
  | 3D models / STEP files | keep on (recommended) |
  | Project skeleton | only newly created folders get empty `.kicad_pro/.kicad_sch/.kicad_pcb`; existing project files are never touched |
  | Overwrite | re-fetch parts already in the library |

- Click **Build library**. The import runs in the background — the gauge and
  the log update per part. Expect **~4 s per part** (EasyEDA rate-limits to
  roughly 15 requests/minute). A 50-part BOM takes a few minutes; see
  [Troubleshooting](#troubleshooting) for the 403-throttle behavior.

- **Stop** button: interrupts the run at any time (confirmation dialog).
  Parts already downloaded are cached in the project folder
  (`.bom2kicad_cache.json`) — run the same BOM + project name again to resume
  exactly where it stopped.

**4. Reload and design**

- When the build finishes you get a summary (parts OK / failed / already
  present). **Close and reopen the project** (or restart KiCad) — KiCad reads
  project library tables only at project open.
- Reopen the project: the `test` library is registered automatically via the
  project-local `sym-lib-table` / `fp-lib-table` written next to
  `test.kicad_pro`.
- Place symbols in the schematic (`A` → search parts from the `test`
  library). Each symbol already carries Value / Footprint / Datasheet /
  LCSC Part fields, and points at `test:<footprint>`; footprints and 3D
  models resolve through `${KIPRJMOD}` — the whole project folder is portable
  (zip it, share it, it works).

Resulting project layout:

```
~/Documents/KiCad/test/
├── test.kicad_pro / .kicad_sch / .kicad_pcb     (your project - untouched)
├── test.kicad_sym                               (project symbols)
├── test.pretty/                                 (project footprints)
├── test.3dshapes/                               (wrl + step)
├── sym-lib-table / fp-lib-table                 (auto-registered)
├── test_bom_import.csv                          (BOM copy for reference)
└── .bom2kicad_cache.json                        (resume cache)
```

## Command line (headless)

The core also works without KiCad (faster, no rate-limit UI caps):

```
python3 core.py --bom BOM_xxx.csv --name MyProject --out ~/Documents/KiCad
```

## Troubleshooting

- **"Could not connect... SSL module is not available"** — normal with the
  KiCad AppImage: its bundled Python has no `ssl` module. The plugin detects
  this and routes all EasyEDA downloads through your system Python
  automatically; conversion still happens inside KiCad.
- **`HTTP 403` / "EasyEDA is rate-limiting this connection"** — you hit the
  per-minute request cap. The build parks for 60 s once, then fails
  remaining parts fast with the honest reason. Wait a few minutes and run
  the same BOM again — cached parts are skipped instantly and only the
  missing ones download.
- **"The current configuration does not include the symbol library ..."** —
  the project lib tables are written/updated *while the project is open*,
  but KiCad loads them only at project open. Fully close the project
  (or restart KiCad) and reopen — nothing else needed.
- **Missing packages (pydantic / requests) prompt** — choose **Yes** to let
  the plugin install them into its own folder using your system Python, or
  **No** to get the exact manual command. Restart KiCad afterwards.
- **Two BOM2Lib entries in the menu** — you have the manual copy AND the PCM
  copy installed; delete the manual one
  (`~/.local/share/kicad/10.0/scripting/plugins/bom2kicad`) and restart.

## Notes

- **Bundled dependencies** (`site-packages/`): `requests`, `pydantic` and
  friends ship inside the package. The `pydantic_core` wheels target
  CPython 3.11, which is what KiCad 10 bundles (minimum tested version).
  Binaries for Linux (AppImage), Windows (win_amd64 — works on any Intel or
  AMD 64-bit CPU) and macOS (darwin; an Intel-Mac binary is bundled — on
  Apple-silicon Macs the plugin auto-fetches the matching wheel on first
  run via KiCad's bundled pip) are inside the zip, so the plugin installs
  on any OS as-is.
  The conversion layer is a vendored copy of
  [easyeda2kicad.py](https://github.com/uPesy/easyeda2kicad.py) (MIT),
  patched for a current User-Agent, request patience and AppImage workarounds.
- **Licensing**: symbols, footprints and 3D models come from the
  EasyEDA/LCSC official library and remain (C) JLCEDA/EasyEDA — attribute
  them when sharing; use in your own commercial designs is allowed. Plugin
  code is MIT (see [LICENSE](LICENSE)).

## Publishing

See [bom2kicad_source/README_PUBLISHING.md](bom2kicad_source/README_PUBLISHING.md)
for rebuilding the PCM package and submitting to the official KiCad addon
repository (package id `com.github.fisika1729.bom2kicad`).

## Credits

All the heavy lifting — EasyEDA API access, symbol/footprint/3D model parsing
and the KiCad library exporters — comes from
**[easyeda2kicad.py](https://github.com/uPesy/easyeda2kicad.py)** by
[uPesy Electronics](https://www.upesy.com/) (MIT license). A vendored copy
ships in `bom2kicad_source/vendor/easyeda2kicad/` with its MIT license text
preserved at `vendor/easyeda2kicad/LICENSE`. If this plugin saves you time,
go star/support the original project — without it, BOM2Lib would not exist.

What BOM2Lib itself adds:

- BOM parsing (UTF-16/UTF-8, tab/comma) and LCSC part-number extraction
- Batch import loop with progress, caching, per-part error recovery and a
  worker thread with Stop/interrupt support
- Project-scoped library layout + `sym-lib-table` / `fp-lib-table` generation
- SSL-less AppImage bridge (fetches via the system Python when needed)
- `${KIPRJMOD}`-relative 3D model references and empty project skeletons
- Fixes to the vendored converter: current User-Agent (the old one gets
  blocked by the API), retry/backoff with timeouts, and a 3D-model export
  path fix

## Next version (2.0) — schematic import from foreign EDA tools

`bom2kicad_next/` (kept out of this repo for now, lives one folder up in the
working tree) extends the idea: feed it the **netlist/schematic** of a design
from OrCAD, Allegro, Xpedition/Expedition, Altium (+ the BOM) and get a
KiCad project with parts placed under their original annotations, matched
footprints and connectivity preserved via net labels. All four formats pass
kicad-cli round-trip verification — see its `DESIGN.md`.

## Roadmap / ideas

- Map BOM designators onto placed symbols to pre-populate the schematic
- Optional "update library" pass that checks for newer part revisions
- Per-part exclusion flags for BOM-fitted vs personally selected alternatives

## Acknowledgements

- [easyeda2kicad.py](https://github.com/uPesy/easyeda2kicad.py) by uPesy (MIT) —
  the conversion engine this plugin runs on
- [pydantic](https://docs.pydantic.dev/) (MIT) and
  [requests](https://requests.readthedocs.io/) (Apache-2.0), bundled in
  `site-packages/`
- EasyEDA/LCSC for providing public CAD data of their official component
  library
