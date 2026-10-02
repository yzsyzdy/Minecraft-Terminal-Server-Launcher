# Configuration

MSTL has two layers of configuration: the global `config.json`, and a `.server.json` for each instance.

## Global configuration `config.json`

It lives at the project root (next to the executable in a packaged build) and is generated from the default values when missing.

```json
{
  "java_path": null,
  "interactive": true,
  "language": "",
  "curseforge_api_key": "",
  "download_threads": 16,
  "auto_start_server": "",
  "web": {
    "host": "127.0.0.1",
    "port": 8765,
    "token": ""
  }
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `java_path` | string \| null | `null` | Path to a Java executable. `null` means auto-detect |
| `interactive` | bool | `true` | Whether to enter the interactive console after the server starts, i.e. type server commands straight into the terminal |
| `language` | string | `""` | UI language. An empty string follows the system (Chinese environments get `zh`, everything else `en`); `zh` / `en` / `ru` can also be set explicitly |
| `curseforge_api_key` | string | `""` | CurseForge API key. Left empty, CurseForge mod search is unavailable |
| `download_threads` | int | `16` | Number of concurrent download chunks. Falls back to a single thread when the mirror does not support Range requests |
| `auto_start_server` | string | `""` | Name of the server that "Settings" has marked as the default to launch; empty means no auto-start. That one is launched **in the foreground** and skips the menu |
| `web.host` | string | `"127.0.0.1"` | Web panel bind address; `0.0.0.0` means all interfaces |
| `web.port` | int | `8765` | Web panel bind port; `0` means "pick a free one" |
| `web.token` | string | `""` | Web panel access token; generated on first start when empty |
| `web.auto_start` | bool | `false` | Whether the panel starts with the app. It runs on a background thread and does not block the terminal menu |

The `language`, `download_threads`, and `auto_start_server` fields normally do not need hand-editing; use menu `[7] Settings` instead:

- `[1] Language`: lists the languages available under `locales/`, with an `[A]` option to go back to following the system
- `[2] Download threads`: adjust the concurrency live
- `[3] Check for updates`: reads the current version from `version.json` and compares it against the latest GitHub Release
- `[4] Auto-start server`: choose which server the main menu launches by default
- `[5] Run at startup`: writes the Windows registry key `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`; unavailable on non-Windows platforms

The bind address, port, token and "start with the app" flag all live in the main menu's `[8] Web panel` submenu, so `config.json` normally needs no hand-editing. Usage and the security boundary are covered in [Web panel](../features/web-panel.md).

!!! tip "Two different \"auto-start\" switches"
    - **`auto_start_server` (global, one server)**: the app skips the main menu and runs that instance **in the foreground** — the terminal belongs to it, Ctrl+C ends it.
    - **An instance's `auto_start` (one per instance)**: the app raises it **in the background**, so the terminal stays on the menu; its console is visible in the panel, and it is stopped when the app exits.

    When both point at the same instance, only the foreground path runs — it is never started twice.

!!! note "About config.json and version control"
    `config.json` holds machine-specific settings, so the repository ships only `config.example.json` as a template. After you clone the project, `config.json` is generated from the defaults and is not tracked by git.

## Per-server configuration `.server.json`

It lives at `servers/<server name>/.server.json`.

```json
{
  "name": "survival",
  "mc_version": "1.21.1",
  "jar": "paper.jar",
  "java_path": null,
  "min_mem": "2G",
  "max_mem": "8G",
  "extra_jvm_args": [],
  "extra_server_args": [],
  "process_priority": "normal",
  "openfrp": {
    "token": "",
    "proxy_id": 0,
    "auto_start": false
  }
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | `""` | Display name, used to identify the instance in menus |
| `mc_version` | string | `""` | Minecraft version, used to derive the required Java version |
| `jar` | string | `"server.jar"` | Core jar file name, relative to the server directory |
| `java_path` | string \| null | `null` | Overrides the global Java setting; `null` keeps the auto-detected result |
| `min_mem` | string | `"1G"` | `-Xms`; `G` / `M` units are supported |
| `max_mem` | string | `"4G"` | `-Xmx` |
| `extra_jvm_args` | list[str] | `[]` | Extra JVM arguments, such as `-XX:+UseG1GC` |
| `extra_server_args` | list[str] | `[]` | Extra server arguments, such as `--nogui` |
| `process_priority` | string | `"normal"` | Process priority: `normal` / `below` (below normal) / `low` |
| `kind` | string | `"minecraft"` | Instance kind: `minecraft` or `generic`. Cannot be changed after creation |
| `auto_start` | bool | `false` | Start with the app: raised **in the background** when the app starts, manageable from the panel |
| `launch` | object | see below | The **generic instance** launch spec; ignored when `kind` is `minecraft` |
| `openfrp.token` | string | `""` | OpenFrp user token bound to this instance |
| `openfrp.proxy_id` | int | `0` | Bound tunnel ID; `0` means unbound |
| `openfrp.auto_start` | bool | `false` | Whether to start frpc automatically when the server launches |

A generic instance's `launch` block:

| Field | Type | Default | Description |
|---|---|---|---|
| `mode` | string | `"exec"` | `exec` (run an executable directly, no shell) / `cmd` / `powershell` |
| `program` | string | `""` | With `mode=exec`, the executable; relative paths resolve inside the instance |
| `args` | list[str] | `[]` | With `mode=exec`, one argument per entry |
| `command` | string | `""` | With `mode=cmd` / `powershell`, the command string |
| `workdir` | string | `""` | Working directory, relative to the instance; empty means the instance itself |

`process_priority`, `extra_jvm_args`, `extra_server_args`, `auto_start` (and a generic instance's whole `launch` block) can be changed from the configuration menu that appears after selecting an instance under main menu `[1] Start server` — no hand-editing needed. In the web panel, select the instance and press `⚙` in the pane header.

## Auto-generated files

| File | Description | Tracked by git |
|---|---|---|
| `config.json` | Global configuration | No |
| `java_list.json` | Java detection results and the last selection, to avoid a full scan every time | No |
| `msl_cache.json` | Server types and version lists from the MSL mirror, cached per day | No |
| `version.json` | Written by CI at packaging time, holds the version number used by the update check | No |

All of these are safe to delete; MSTL rebuilds them on the next run.

## Java requirements

| MC version | Minimum Java | Recommended Java | Notes |
|---|---|---|---|
| ≤ 1.12.2 | 8 | 8 | Many older mods require Java 8 |
| 1.13 – 1.16.5 | 8 | 11 | Java 11 performs better |
| 1.17 – 1.17.1 | 16 | 17 | Java 8 is no longer compatible |
| 1.18 – 1.20.4 | 17 | 17 | |
| ≥ 1.20.5 | 21 | 21 | Mandated upstream |
| ≥ 1.25 | 25 | 25 | Latest snapshots |

When the requirement is not met, startup prints a warning and offers an automatic JDK download (17 / 21 / 25, from GraalVM).
