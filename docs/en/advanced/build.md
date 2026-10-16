# Building from source

## Running the tests

The tests use only the standard-library `unittest` and **need no dependencies installed**
(the project itself has zero runtime dependencies). From the repository root:

```bash
python -m unittest discover -s tests -t . -v
```

CI (`.github/workflows/tests.yml`) runs them on every push / PR against Python 3.11 and
3.13; `build.yml` also runs them before packaging, so a failing suite can't ship.

The tests target *contracts* rather than implementation details, concentrating on the
things that break quietly:

| File | Covers |
| --- | --- |
| `test_web_api.py` | Panel auth (all three token channels), `/api/state` values must equal disk, settings writes |
| `test_config.py` | `config.json` round-trip, unknown keys must survive, atomic writes |
| `test_server_manager.py` | Instance scanning, type normalization, the parse cache and its isolation |
| `test_paths.py` | Packaged-build path resolution (simulated with a fake `__compiled__`, no real build) |
| `test_runtime_plan.py` | Launch plans, process priority, runtime snapshots |
| `test_frp_toml.py` | `frpc.toml` round-trips stay valid TOML (checked with `tomllib`) |
| `test_frpc_files.py` / `test_tunnel_install.py` | Shared frpc detection, extraction and validation for both tunnels |
| `test_archive.py` | Zip-slip and archive-bomb protection for online extraction |
| `test_i18n.py` | Fallback to Chinese for missing translations, placeholder parity |
| `test_static.py` | Undefined-name sweep, panel id/string consistency, JS syntax |

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
  --product-version="1.1.37" ^
  --file-version="1.1.37" ^
  --file-description="Minecraft Terminal Server Launcher" ^
  --copyright="MSTL" ^
  --include-data-dir=locales=locales ^
  --include-data-dir=webui=webui ^
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
| `--include-data-dir=webui=webui` | Packages `webui/`. **Omit it and the web panel is dead**: it opens to `webui/index.html missing` |
| `--output-dir=build` | Writes the output to `build/` |

The artifact lands at `build/start.dist/MSTL.exe`. The entire `start.dist/` directory has to be distributed together; the executable alone is not enough.

### Bundled assets the exe reads at runtime

These are hard-coded paths into the bundle. **Miss one and that feature is broken:**

| Asset | Read by | If missing |
|---|---|---|
| `locales/*.json` | `i18n` | The whole UI renders as raw i18n keys |
| `webui/index.html` | `web/server.py` | The web panel will not open (HTTP 500) |
| `version.json` | `check_update.py` | The update check silently skips (written by CI after packaging; absent in source mode anyway) |

CI's `build.yml` has a `Verify bundled assets` step that checks the first two and fails the build if either is missing. In source mode or a hand-rolled build, starting the panel also runs the same self-check and prints `[warning] missing bundled asset(s): …`, so you find out before the browser does.

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

> **This file must be written as UTF-8 without a BOM.** CI uses Windows PowerShell 5.1,
> whose `Set-Content -Encoding UTF8` prepends a BOM; Python's `json.load` then raises
> `JSONDecodeError` — which really did ship once: the update check went permanently dumb,
> showing "you are up to date" forever with no error anywhere. The write now uses
> `[IO.File]::WriteAllText(..., UTF8Encoding $false)`, the read uses `utf-8-sig`, and CI
> has an assertion that fails the build on a BOM.

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
