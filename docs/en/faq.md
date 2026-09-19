# FAQ

## Starting up

### It says Java was not found or the version is incompatible

MSTL recommends a Java version based on the MC version (see the Java requirements section in [Configuration](guide/configuration.md)). When it does not match, you get a warning and a download entry point.

To point it at Java manually, there are three ways: change `java_path` in `config.json` (global), change `java_path` in a single instance's `.server.json` (affects only that one), or add Java to the system `PATH` so it is picked up by the scan.

### The server flashes open and exits, complaining that the EULA was not accepted

This is an unavoidable first-run step. MSTL detects `eula=false` and asks; on consent it rewrites `eula.txt` and restarts. If you manually edit `eula.txt` back to `false`, the next launch asks again.

### The server will not start, and the log says out of memory

Tune `min_mem` / `max_mem` in `.server.json`. Note that `max_mem` cannot exceed a sensible share of physical memory; giving it too much can actually stop the JVM from starting.

### I want the server to run in the background without that interactive console

Set `interactive` to `false` in `config.json`. MSTL then stops owning stdin. Server output is still printed, but you can no longer type commands into the terminal.

## Downloads

### Server core downloads are slow

The default is 16 concurrent chunks. Adjust it via menu `[7] Settings` → `[2] Download threads`, or edit `download_threads` in `config.json` directly.

If the mirror does not support Range requests, MSTL falls back to a single thread, and no amount of thread tuning will help then.

### Downloads fail / the version list is empty

The MSL mirror API (`api.mslmc.cn`) may be unavailable at that moment. The version list is cached per day; on failure, delete `msl_cache.json` to force a fresh fetch.

## Plugins and mods

### CurseForge search does nothing

You need to set `curseforge_api_key` in `config.json`. Apply for one for free at the [CurseForge Console](https://console.curseforge.com/).

If you would rather not bother, use Modrinth; the same mod is very likely on both sides.

### I cannot find a mod by its Chinese name

A platform index relies on the text in a mod's description, and the names and descriptions of the vast majority of mods are English, so Chinese keywords hit rarely. Search with English keywords instead.

### A plugin download fails

Some plugins are hosted on external platforms (GitHub, for instance). MSTL detects this and goes through the GitHub API to grab the jar out of a release. If that still fails, download it manually as prompted, then use "Import a local jar" to add it.

### I disabled a plugin and want it back

Disabling only renames the file to `xxx.jar.disabled`. Just drop the `.disabled` suffix; that is exactly what "Enable" in the menu does.

## Tunneling

### The tunnel is online but friends cannot connect

Confirm the bound local port matches the `server-port` in `server.properties` (25565 by default). Also, if the server is not running yet, the tunnel has nothing to forward to.

### What is frpc, and do I have to install it myself

No. On the OpenFrp route the frpc binary is downloaded automatically by MSTL based on your system and architecture; on the local frp route the menu also has "Download frpc". These binaries are excluded by `.gitignore` and never enter the repository.

## UI and language

### How do I switch the interface to English

Main menu `[7] Settings` → `[1] Language`, then pick `English` from the list. `[A]` means follow the system language.

Whatever languages sit in `locales/` are selectable. Three ship built in today: 中文, English, Русский.

## Miscellaneous

### Where is the configuration file in a packaged build

In the same directory as `MSTL.exe`. `servers/`, `config.json`, and the various caches all live at that level, so moving the whole directory migrates everything.

### Is it safe to just delete the cache files

Yes. `java_list.json`, `msl_cache.json`, and `version.json` are all rebuildable; deleting them just makes the corresponding flow run again. Deleting `config.json` returns you to the default configuration.

### Can several servers run at the same time

Yes. Each instance has its own directory, its own configuration, and its own Java choice. The only things to watch are port collisions and total memory not exceeding physical RAM.
