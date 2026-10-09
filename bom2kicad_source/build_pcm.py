#!/usr/bin/env python3
"""Build a KiCad PCM-installable zip for this plugin.

Usage:  python3 build_pcm.py [output.zip]

Creates a PCM package following the KiCad addon archive layout:
    metadata.json            (inside archive: single version, NO download_* fields)
    plugins/bom2kicad/...    (plugin payload)
    resources/icon.png       (64x64 PCM icon)
"""

import json
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))

PACKAGE_VERSION = "1.0.0"

METADATA = {
    "$schema": "https://go.kicad.org/pcm/schemas/v1",
    "name": "BOM2Lib",
    "description": "Build a project-local KiCad library (symbols, footprints, 3D models) from an EasyEDA BOM",
    "description_full": (
        "BOM2Lib turns an EasyEDA BOM export (CSV) into a complete, "
        "project-local KiCad library.\n"
        "\n"
        "It prompts for a project name, extracts all LCSC part numbers from the "
        "BOM, downloads CAD data from the public EasyEDA API and writes:\n"
        "  - <project>.kicad_sym  (symbols, each pre-linked to its footprint)\n"
        "  - <project>.pretty/    (footprints, also referenced via ${KIPRJMOD})\n"
        "  - <project>.3dshapes/  (WRL + STEP 3D models)\n"
        "  - sym-lib-table / fp-lib-table  (auto-registered for the project)\n"
        "  - an optional empty .kicad_pro/.kicad_sch/.kicad_pcb skeleton\n"
        "\n"
        "If kicad-cli is available, all files are upgraded/validated into native "
        "KiCad format automatically. Re-running with the same BOM is fast: already "
        "imported parts are cached and skipped unless Overwrite is checked.\n"
        "\n"
        "Runs as an Action Plugin: Tools > External Plugins > BOM2Lib "
        "(in Pcbnew or Eeschema).\n"
        "\n"
        "EasyEDA library data remains (c) JLCEDA/EasyEDA and must be attributed "
        "when shared. Conversion uses a vendored copy of easyeda2kicad.py (MIT)."
    ),
    "identifier": "com.github.fisika1729.bom2kicad",
    "type": "plugin",
    "author": {
        "name": "fisika1729",
        "contact": {
            "web": "https://github.com/fisika1729",
            "github": "fisika1729",
        },
    },
    "maintainer": {
        "name": "fisika1729",
        "contact": {
            "web": "https://github.com/fisika1729",
            "github": "fisika1729",
        },
    },
    "license": "MIT",
    "resources": {
        "homepage": "https://github.com/fisika1729/bom2kicad",
    },
    "versions": [
        {
            "version": PACKAGE_VERSION,
            "status": "stable",
            "kicad_version": "10.0",
            # NOTE: download_url / download_sha256 / download_size / install_size
            # are added to the metadata.json that gets submitted to (or served
            # from) a PCM repository -- never inside the archive itself.
        }
    ],
}

EXCLUDE_DIRS = {"__pycache__", ".git"}
EXCLUDE_FILES = (".pyc", ".pyo", ".DS_Store")


def _excluded(name: str, full: str) -> bool:
    if name in EXCLUDE_DIRS or name.endswith(EXCLUDE_FILES):
        return True
    return False


def build(out_path: str) -> str:
    stage = out_path + ".stage"
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(os.path.join(stage, "plugins", "bom2kicad"))
    os.makedirs(os.path.join(stage, "resources"))

    payload_root = os.path.join(stage, "plugins", "bom2kicad")
    for entry in os.listdir(HERE):
        full = os.path.join(HERE, entry)
        if _excluded(entry, full) or entry.startswith(
            ("build_pcm", "LICENSE")
        ) or entry in ("metadata.json", "README_PUBLISHING.md", "icon_pcm.png"):
            continue
        # build scripts and the metadata template stay out of the payload
        shutil.move if False else None
        if os.path.isdir(full):
            shutil.copytree(full, os.path.join(payload_root, entry))
        else:
            shutil.copy2(full, payload_root)

    # clean junk from the payload copy
    for root, dirs, files in os.walk(payload_root):
        for d in list(dirs):
            if _excluded(d, root):
                shutil.rmtree(os.path.join(root, d))
                dirs.remove(d)
        for f in files:
            if _excluded(f, os.path.join(root, f)):
                os.remove(os.path.join(root, f))
    shutil.copy2(os.path.join(HERE, "LICENSE"), payload_root)

    # 64x64 PCM icon (simple upscale of the toolbar icon is ugly; ship the
    # generated 64x64 file next to this script if present, else toolbar icon)
    pcm_icon = os.path.join(HERE, "icon_pcm.png")
    if os.path.isfile(pcm_icon):
        shutil.copy2(pcm_icon, os.path.join(stage, "resources", "icon.png"))
    else:
        shutil.copy2(os.path.join(HERE, "icon.png"), os.path.join(stage, "resources", "icon.png"))

    with open(os.path.join(stage, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(METADATA, f, indent=4, ensure_ascii=False)
        f.write("\n")

    # zip it
    if os.path.isfile(out_path):
        os.remove(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(stage):
            dirs[:] = [d for d in dirs if not _excluded(d, root)]
            for f in sorted(files):
                if _excluded(f, os.path.join(root, f)):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, stage)
                zf.write(full, rel)

    shutil.rmtree(stage)
    return out_path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(HERE), f"bom2kicad-{PACKAGE_VERSION}-pcm.zip"
    )
    build(out)

    install_size = sum(
        os.path.getsize(os.path.join(r, f))
        for r, _, fs in os.walk(os.path.join(HERE))
        for f in fs
        if not _excluded(f, r)
    )
    sz = os.path.getsize(out)
    print(f"Built {out}")
    print(f"download_size: {sz}")
    print(f"install_size (approx): {install_size}")
    import hashlib

    with open(out, "rb") as fh:
        print(f"download_sha256: {hashlib.sha256(fh.read()).hexdigest()}")
