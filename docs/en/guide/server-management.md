# Server management

## Directory layout

Every subdirectory of `servers/` is one server instance, fully isolated from the others:

```
servers/
├── survival/
│   ├── .server.json        # configuration for this server
│   ├── paper.jar           # the core
│   ├── eula.txt
│   ├── server.properties
│   ├── plugins/            # plugins
│   ├── mods/               # mods (Fabric / Forge, etc.)
│   └── world/              # world data
└── skyblock/
    └── ...
```

The directory name is the instance identifier. Non-ASCII characters are fine (everything is handled as UTF-8 internally), but paths containing `\ / : * ? " < > |` will misbehave on Windows.

## Version isolation

Each instance records its own MC version, core jar, Java path, memory limit, and JVM arguments. A 1.20.1 Forge server and a 1.21.1 Paper server can coexist, each on its own Java.

At launch the required Java is derived from `mc_version` in `.server.json`, so a single `MSTL.exe` can serve both old and new versions.

## Import

Main menu → `[2] Import server archive`.

Behaviour details:

- `.zip` is supported. Extraction goes through a temporary directory that is cleaned up afterwards, so `servers/` is never polluted
- Only jars in the **top level** of the archive are considered as core candidates, so jars inside `plugins/` are not mistaken for cores
- When more than one candidate exists, you pick interactively
- If the archive already contains a `.server.json`, its settings are read in
- If the archive has an extra wrapping directory (for example, a zipped folder rather than its contents), MSTL handles the nesting

## Export

Main menu → `[5] Export server` packages an existing instance by category, for migration or backup.

Available categories:

| Category | Contents |
|---|---|
| `world` | `world`, `world_nether`, `world_the_end` |
| `datapacks` | `world/datapacks` |
| `plugins` | `plugins/*.jar` |
| `plugindata` | `plugins/*/`, the data directories of each plugin |
| `playerdata` | Player data, statistics, advancements, `ops.json`, `whitelist.json`, ban lists, and so on |
| `icon` | `server-icon.png` |
| `config` | `server.properties`, `bukkit.yml`, `paper.yml`, `purpur.yml`, `eula.txt`, `.server.json`, and so on |

`playerdata` involves player privacy, so it is **unchecked** by default; turn it on manually when you need it.

Once you have made your selection, MSTL collects the files by category into a zip. Note that the core jar is not part of any category (a version upgrade usually means a new core), so add it back after importing.

## Day-to-day plugin and mod handling

Main menu → `[4] Manage plugins/mods`, then pick a server:

- Lists the jars in the directory with their enabled/disabled state
- `Enable` / `Disable`: implemented by renaming. Disabling turns the file into `.jar.disabled`, which the server will not load
- `Delete`: removes the file after confirmation
- `Import a local jar`: copies an existing jar from disk into the folder
- `Search and download`: searches the online sources and installs

See [Plugin management](../features/plugin-management.md) and [Mod management](../features/mod-management.md) for details.
