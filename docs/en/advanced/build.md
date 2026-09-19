# Building from source

## Prerequisites

- Python 3.11 (the version CI uses; 3.10+ generally works)
- Nuitka: `pip install nuitka imageio`

`imageio` is what lets Nuitka process `icon.png`; without it the icon step fails.

## Local packaging

The repository root ships a ready-made `build.bat`:

```bat
python -m nuitka --standalone ^
  --assume-yes-for-downloads ^
  --enable-plugin=anti-bloat ^
  --windows-console-mode=force ^
  --windows-icon-from-ico=icon.png ^
  --output-dir=build ^
  --product-name=MSTL ^
  --product-version="1.1.36" ^
  --file-version="1.1.36" ^
  --file-description="Minecraft Terminal Server Launcher" ^
  --copyright="MSTL" ^
  --include-data-dir=locales=locales ^
  --output-filename=MSTL.exe ^
  start.py
```

Each flag, one by one:

| Flag | Effect |
|---|---|
| `--standalone` | Produces a directory-style build (`start.dist/`) rather than a single file. Resources sit next to the executable and startup is faster |
| `--assume-yes-for-downloads` | Skips the interactive prompt when a C compiler or dependency is missing and downloads it directly, convenient for CI |
| `--enable-plugin=anti-bloat` | Trims unused standard-library modules to shrink the output |
| `--windows-console-mode=force` | Forces the console window to stay. MSTL is a terminal program; this one is not optional |
| `--windows-icon-from-ico=icon.png` | Sets the executable icon |
| `--include-data-dir=locales=locales` | Packages the `locales/` directory into the output. **The runtime directory name has to be `locales`**; get it wrong and the whole UI shows raw keys |
| `--output-dir=build` | Writes the output to `build/` |

The artifact lands at `build/start.dist/MSTL.exe`. The entire `start.dist/` directory has to be distributed together; the executable alone is not enough.

## Version numbers

`--product-version` / `--file-version` are the version shown in the executable's properties. The values hard-coded in the local `build.bat` are illustrative only; the CI version is derived from the git tag.

There is also a runtime version file, `version.json`, written into the output directory by CI after packaging:

```json
{
  "version": "1.0.0",
  "tag_name": "v1.0.0"
}
```

`check_update.py` reads it to learn the current version, then compares it against the latest GitHub Release. Running from source there is no such file, so the update check silently skips.

## CI release

`.github/workflows/build.yml` triggers on a pushed `v*` tag, and can also be run manually from the Actions page:

1. Check out the code, install Python 3.11 plus nuitka and imageio
2. Derive the version from the tag (stripping the leading `v`) and run the Nuitka command above
3. Write `version.json` into the output directory
4. Package everything into `MSTL-<tag>.zip`
5. Upload the artifact and create or update the matching GitHub Release with the zip attached

So the complete release action is: bump the version, commit, then

```bash
git tag v1.1.0
git push origin v1.1.0
```

CI takes care of the rest.

## Docs site

The docs are built with MkDocs Material. The configuration lives at the repository root in `mkdocs.yml`, and the content is under `docs/`.

```bash
pip install mkdocs-material mkdocs-static-i18n mkdocs-minify-plugin mkdocs-git-revision-date-localized-plugin
mkdocs serve          # local preview, http://127.0.0.1:8000 by default
mkdocs build --strict # strict build; warnings become failures. Worth running before you commit
```

Chinese is the default language, and the English version lives under `docs/en/`. Pages without a translation fall back to the default language, so you can finish Chinese first and catch up on English later.
