# Quick start

## Prerequisites

| Item | Requirement |
|---|---|
| Python | 3.10 or newer (needed only when running from source) |
| Third-party packages | None. Everything uses the standard library |
| Java | Optional. MSTL can download a JDK automatically when Java is missing or the version does not match |

If you use the prebuilt `MSTL.exe` from a release, you do not even need Python installed.

## Install

=== "Run from source"

    ```bash
    git clone https://github.com/yzsyzdy/Minecraft-Terminal-Server-Launcher.git
    cd Minecraft-Terminal-Server-Launcher
    python start.py
    ```

=== "Use a packaged build"

    Download `MSTL-v*.zip` from [Releases](https://github.com/yzsyzdy/Minecraft-Terminal-Server-Launcher/releases), unzip it, and double-click `MSTL.exe`.

## First run

The first launch creates three things in the current directory:

```
config.json      # global configuration
servers/         # server instances, one subdirectory each
java_list.json   # Java detection cache (written after the first scan)
```

If `servers/` is empty, the main menu will walk you through importing or downloading a server first.

## Getting a server

### Option A: download a server core

Main menu → `[3] New instance` → `[1] Download a Minecraft server core`:

1. Pick a server type (Paper, Purpur, Fabric, Forge, Velocity, and 30-odd more)
2. Pick a version (the version list is cached for a day to avoid repeat requests)
3. Confirm, and the core is downloaded with multiple threads into a new server directory
4. Fill in the server name, memory, and other settings as prompted

### Option B: import an existing server

Main menu → `[2] Import server archive`:

1. Enter the path to the archive
2. MSTL unpacks it into a temporary directory and detects the core jar automatically
3. If several jars are found, it asks you to pick one
4. Confirm, and it lands in `servers/<name>/`

### Option C: create a generic instance

Main menu → `[3] New instance` → `[2] Generic instance`. It does not run Minecraft; it runs
an executable of your choosing, or a `cmd` / `powershell` command — with the same start/stop,
live console and log file. See [Generic instances](../features/web-panel.md#generic-instances).

## Starting

Main menu → `[1] Start server`, then pick a server. Before launching, MSTL does a few things in order:

1. **Resolve Java**: cache → system scan → download if necessary
2. **Validate compatibility**: compare against the Java version the MC version needs, warn and offer a download if it does not match
3. **Verify the core**: make sure both the java executable and the jar exist
4. **Assemble the command**: `-Xms` / `-Xmx`, extra JVM arguments, extra server arguments

After it starts you land in an interactive console. Type server commands directly (`stop`, `say hello`, `op <player>`, and so on); input is passed straight through to the server process.

## EULA

A Minecraft server exits immediately on first launch if the EULA has not been accepted. MSTL spots `eula=false` and asks whether you agree:

- Agree → it rewrites `eula.txt` and restarts the server
- Decline → this launch ends

## Next steps

- Tired of sitting at the terminal: main menu `[8] Web panel` — start, watch the console, manage files and frp from a browser. See [Web panel](../features/web-panel.md)
- Want to tune memory or JVM flags: [Configuration](configuration.md)
- Want to install plugins or mods: [Plugin management](../features/plugin-management.md), [Mod management](../features/mod-management.md)
- Want friends to connect: [Tunneling](../features/tunnel.md)
