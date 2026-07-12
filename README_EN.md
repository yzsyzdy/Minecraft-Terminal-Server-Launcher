# Minecraft Server Launcher

A lightweight, zero-dependency Minecraft server manager. Manage multiple server instances with version isolation, automatic Java detection and download, plugin and mod management. Supports Windows and Linux.

## Features

### Core Management

- **Multi-server instances** — Each server lives in its own directory under `servers/`. Supports 30+ server types including Paper, Purpur, Fabric, Forge, NeoForge, Velocity, and more
- **Version isolation** — Each server has its own MC version, server jar, Java version, memory allocation, and JVM arguments
- **Automatic Java detection** — Scans environment variables and standard installation paths to discover installed JDK/JRE (Windows and Linux)
- **JDK auto-download** — Automatically downloads GraalVM JDK (17 / 21 / 25) when Java is missing or incompatible
- **Java version validation** — Matches required Java version against MC version, warns and offers auto-download when incompatible
- **Multithreaded download** — Server cores and plugins are downloaded with 16 concurrent threads using chunked Range requests, with automatic fallback to single-thread
- **EULA handling** — Detects `eula=false` on server exit, prompts the user, and auto-restarts after acceptance
- **Interactive console** — Send server commands (stop, say, etc.) directly from the terminal
- **Cross-platform** — Runs on both Windows and Linux

### Plugin Management

- Search plugins across **SpigotMC** (via Spiget API), **Modrinth**, and **Hangar**
- Results sorted by download count
- Direct search and download with multithreaded acceleration
- Import local `.jar` files
- Enable/disable plugins (via `.jar.disabled`)
- Automatic handling of externally hosted plugins (e.g., GitHub-hosted plugins resolved via GitHub API)

### Mod Management

- Search mods on **Modrinth** (no API key required) and **CurseForge** (API key optional)
- Automatically filters by the current server's MC version and mod loader (Fabric / Forge / NeoForge / Quilt)
- Chinese search support (Modrinth natively handles Unicode queries)
- Smart fallback — automatically broadens search when filtered results are scarce
- One-click download of version-matched mods
- Local jar import and enable/disable

### Server Core Download

- 30+ server core types via **MSL Mirror** (`api.mslmc.cn/v4`)
- Daily cached version lists
- Interactive type and version selection, automatic config generation

### Archive Import

- Import existing server zip archives with automatic extraction and core jar detection
- Interactive selection when multiple jars are found

## Quick Start

```bash
python start.py
```

The first run automatically creates `config.json` and the `servers/` directory.

### Prerequisites

- **Python 3.10+** (stdlib only, **zero third-party dependencies**)
- **Java** (optional — the launcher can download JDK 21 automatically)

## Configuration

### Global Config `config.json`

```json
{
  "java_path": null,
  "interactive": true,
  "curseforge_api_key": ""
}
```

| Field | Description |
|---|---|
| `java_path` | Explicit Java executable path, `null` for auto-detection |
| `interactive` | Enable interactive console mode |
| `curseforge_api_key` | CurseForge API key (optional, enables CurseForge mod search) |

### Server Config `servers/<server-name>/.server.json`

```json
{
  "name": "survival1",
  "mc_version": "1.21.1",
  "jar": "leaves.jar",
  "java_path": null,
  "min_mem": "2G",
  "max_mem": "8G",
  "extra_jvm_args": [],
  "extra_server_args": []
}
```

When `java_path` is `null`, the launcher auto-detects Java. Memory values support `G` and `M` suffixes.

## Project Structure

```
mc-server-launcher/
├── start.py              # Entry point
├── main.py               # Re-export module (backward compatible)
├── config.py             # Global config read/write
├── clear.py              # Terminal screen clear utility
├── server_manager.py     # Server directory management, zip import
├── download_msl.py       # MSL API server download + multithreaded download engine
├── java_tools.py         # Java detection, JDK download, version compatibility (Win/Linux)
├── server_launcher.py    # Server process launcher
├── menu.py               # Interactive menus (server selection, plugin/mod management)
├── plugin_sources.py     # Plugin search & download (SpigotMC, Modrinth, Hangar)
├── mod_sources.py        # Mod search & download (Modrinth, CurseForge)
├── config.json           # Global config (auto-generated)
├── java_list.json        # Java detection cache (auto-generated)
├── msl_cache.json        # MSL mirror cache (auto-generated)
└── servers/              # Server instances directory (auto-created)
```

## Runtime Flow

```
start.py
  │
  ├─ Clear screen
  ├─ Load config.json
  ├─ Create servers/ directory
  │
  ├─ No servers → Menu: [1]Import  [2]Download  [0]Exit
  │
  └─ Has servers → List + [Import] [Download] [Manage plugins/mods] [Exit]
                       │
                       ▼ Select server
                       │
                       ├─ Resolve Java (cache → scan → download)
                       ├─ Java version compatibility check
                       └─ Start server (interactive console)
```

## MSL Mirror Source

Supports 30+ server core types via `https://api.mslmc.cn/v4`:
[MSL Mirror Documentation](https://www.mslmc.cn/docs/msl/msl-mirrors/)

Supported types include: Paper, Purpur, Leaf, Leaves, Spigot, Folia, Fabric, Forge, NeoForge, Quilt, Vanilla, Velocity, BungeeCord, and more.

## Java Version Compatibility

| MC Version | Min Java | Recommended Java | Notes |
|---|---|---|---|
| ≤ 1.12.2 | 8 | 8 | Many legacy mods require Java 8 |
| 1.13 – 1.16.5 | 8 | 11 | Java 11 offers better performance |
| 1.17 – 1.17.1 | 16 | 17 | Java 8 no longer compatible |
| 1.18 – 1.20.4 | 17 | 17 | |
| ≥ 1.20.5 | 21 | 21 | Official requirement |
| ≥ 1.25 | 25 | 25 | Latest snapshot |

When incompatible, the launcher offers automatic JDK download (17 / 21 / 25).

## Dependencies

- **Python 3.10+**
- **Standard library only** (zero third-party dependencies)
- **JDK 21+** (optional, for running Minecraft servers — the launcher can download it automatically)

## FAQ

### CurseForge search not working?

Set a `curseforge_api_key` in `config.json`. You can get one for free at [CurseForge Console](https://console.curseforge.com/).

### Plugin download from SpigotMC fails?

Some plugins are hosted externally (e.g., on GitHub). The launcher detects this and uses the GitHub API to find actual jar files. If it still fails, download manually from the URL provided.

### Can't find Chinese-named mods?

Modrinth's search index depends on mod descriptions. Most mods use English names and descriptions. Search with English keywords for best results.

### Slow downloads?

The launcher uses 16 concurrent threads by default. Call `set_download_threads(n)` to adjust. If the mirror doesn't support Range requests, it falls back to single-threaded download automatically.
