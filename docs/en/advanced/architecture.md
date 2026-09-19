# Architecture

## Design constraints

Three constraints shape most of the code:

1. **Zero third-party dependencies.** Only the Python standard library: `urllib.request` as the HTTP client, `json` for configuration, `zipfile` for extraction. That means no `requests`, and therefore no automatic retries or connection pooling; every bit of network fault tolerance is hand-written
2. **Terminal UI.** No GUI framework. Menus are `print` plus `input`, and every string has to go through i18n
3. **It has to package into a single-file executable.** After Nuitka packaging, the meaning of `__file__`, `sys.executable`, and `sys.argv[0]` all change, which is why path logic is isolated in `paths.py`

## Modules

### Entry point and configuration

| Module | Responsibility |
|---|---|
| `start.py` | Program entry; strings together the update check, config loading, menus, the launch flow, the EULA loop, and frpc cleanup |
| `main.py` | Public API surface; re-exports each submodule's interface for old-style `from main import ...` |
| `config.py` | Reads and writes `config.json`, merging in defaults |
| `constants.py` | Central definition of all external API endpoints and the User-Agent |
| `i18n.py` | Translation lookup; `t("key", **kwargs)` fetches a string |
| `paths.py` | Cross-mode path resolution, distinguishing the "user data directory" from the "packaged resource directory" |
| `clear.py` | Cross-platform screen clearing |

### Business logic

| Module | Responsibility |
|---|---|
| `server_manager.py` | Server directory scanning, `.server.json` I/O, archive import, category-based export, server type detection |
| `download_msl.py` | Queries the MSL API for server types and versions, daily caching, and the multithreaded download engine |
| `java_tools.py` | Java detection (environment variables plus common install paths), version parsing, compatibility checks, JDK download and extraction |
| `server_launcher.py` | Assembles the JVM command, spawns the subprocess, runs the interactive console, sets process priority, handles SIGINT |
| `menu.py` | Every interactive menu; the largest module in the project |

### Data sources and surrounding pieces

| Module | Responsibility |
|---|---|
| `plugin_sources.py` | Search and download for SpigotMC (Spiget), Modrinth, and Hangar, including externally hosted resolution |
| `mod_sources.py` | Search and download for Modrinth and CurseForge, with version and loader filtering |
| `http_utils.py` | The unified `fetch_json`, shared by GET and POST, returning `None` on failure |
| `check_update.py` | Reads `version.json` and compares against the latest GitHub Release |
| `startup.py` | Windows registry auto-start; returns unavailable on non-Windows |
| `openfrp/` | OpenFrp cloud tunnels: token auth, tunnel CRUD, automatic frpc download, the `FrpcManager` singleton |
| `frp/` | Local frp: `frpc.toml` parsing and generation, proxy management, frpc launching |

`openfrp/` and `frp/` are two parallel implementations serving two different use cases, "cloud-managed tunnels" and "bring your own frpc config". Neither inherits from or reuses the other.

## Startup sequence

```
start.py  main()
  ├─ clear_screen()
  ├─ check_for_update()              silent check, prints one line if a new version exists
  ├─ load_config() / load_language()  infers from the system language when empty
  ├─ ensure_servers_folder()
  ├─ auto_start_server matched → skip the menu and select that instance
  │  otherwise show_main_menu()
  └─ _start_server()
       ├─ resolve_java()             cache → scan → download if necessary
       ├─ verify the core jar exists
       ├─ openfrp.auto_start true → launch_frpc()
       ├─ print the instance summary
       └─ while True:
            ├─ start_server_interactive() or start_minecraft_server()
            ├─ non-EULA exit → break
            └─ exited because eula=false → ask → rewrite and restart on consent
```

Whatever the exit code, once the server exits, `main()` calls `stop_frpc()` to take down the tunnel process.

## Two engineering details that are easy to trip on

### Paths after packaging

In Nuitka onefile mode, the executable unpacks itself into `%TEMP%/onefile_xxxx` at runtime. At that point `sys.executable` points at the temporary directory, which is deleted when the program exits. Writing user configuration there would mean losing it on every run.

`paths.py` splits the two cases like this:

- **User data directory** (`config.json`, `servers/`, caches): the project root when running from source; in onefile mode it is located via the `MSTL_DATA_DIR` environment variable, or by resolving the parent process from the `NUITKA_ONEFILE_PARENT` PID back to the original executable path
- **Packaged resource directory** (`locales/`): follows `__file__`, that is, the unpacked temporary directory, because these files are read-only resources

When `i18n.py` looks for `locales/`, it explicitly notes not to use `sys.executable`, and this is why.

### Multithreaded downloads

`download_msl.py` first probes whether the target supports Range requests. If it does, it fetches the file in `download_threads` chunks concurrently and reassembles them; if not, it falls back to a single-threaded sequential download. The relationship between chunk count and thread count, along with retry on failure, lives in this module. `set_download_threads()` is the single adjustment point, and the "Download threads" setting in the menu eventually lands there too.

## Extending

- **Add a server type**: types come from the MSL API in `download_msl.py`, so usually no code change is needed; a new type on the mirror is immediately usable. When extra handling is required, touch `_server_display_name()` and the categorisation logic
- **Add a plugin source**: model a function on `hangar_search` in `plugin_sources.py`, returning a list of `PluginResult`, then hook it into `search_all()`
- **Add a language**: drop a `<code>.json` into `locales/` with keys aligned to `zh.json`; `available_languages()` discovers it automatically
- **Add a menu entry**: add the branch in `show_main_menu()` in `menu.py`, and add the string to every locale file at the same time
