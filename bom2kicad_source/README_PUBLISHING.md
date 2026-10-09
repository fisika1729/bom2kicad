# Publishing BOM2Lib to KiCad's PCM

## Install from file (already built)

`~/Documents/bom2kicad/bom2kicad-1.0.0-pcm.zip` is a valid KiCad PCM package
(metadata validated against the official schema-v1.json).

In KiCad: **Plugin and Content Manager → Install from File...** and select the zip.

> If you previously copied the plugin into `scripting/plugins/` manually,
> delete that copy first, otherwise the plugin menu shows two identical
> entries (one manual, one PCM-managed).

## Rebuilding the package

Any time the plugin source changes:

    python3 build_pcm.py build/out/bom2kicad-<version>-pcm.zip

The script:
- writes `metadata.json` (single version, **no download_\* fields** - required)
- packs only the plugin payload (`plugins/bom2kicad/`) + `resources/icon.png` (64x64)
- excludes `__pycache__`, `.pyc`, `.DS_Store` and this Packaging doc
- prints `download_size`, general `install_size` and the `download_sha256`

Bump `PACKAGE_VERSION` in `build_pcm.py` and the version object
inside `METADATA` before every release.

## Publish to the official KiCad addon repository (optional)

1. Create a GitHub repo `fisika1729/bom2kicad` and push this folder.
2. Create a GitHub **release** (tag `v1.0.0`) and upload the zip built above.
3. Use `metadata-repository.json` (already prefilled with the sha256 and a
   download URL template) as the package metadata you submit.
4. Submit a merge request at https://gitlab.com/kicad/addons/metadata
   (directory name = package identifier `com.github.fisika1729.bom2kicad`).

Full requirements: https://dev-docs.kicad.org/en/addons/

## Notes

- `identifier` and `resources.homepage` assume the GitHub namespace
  `fisika1729/bom2kicad`; update both in `build_pcm.py` if the repo name changes.
- `kicad_version: 10.0` is the tested minimum: the bundled `pydantic_core`
  wheel targets KiCad 10's bundled CPython 3.11.
- The `resources/icon.png` (64x64) is shown in the PCM dialog;
  `plugins/bom2kicad/icon.png` (24x24) is the action-plugin toolbar icon -
  KiCad requires it to live inside the plugins directory.
- All PNGs are written clean (IHDR/sRGB/gAMA/IDAT/IEND only) so libpng does
  not emit iCCP/cHRM warnings during install.
