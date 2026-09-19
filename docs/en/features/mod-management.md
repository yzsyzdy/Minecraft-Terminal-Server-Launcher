# Mod management

Entry point: main menu → `[4] Manage plugins/mods`, pick a server → `Mods`.

Mod management only means anything for modded platforms (Fabric / Forge / NeoForge / Quilt). Pure plugin platforms (Paper / Purpur / Velocity and the like) have no concept of a `mods/` directory.

## Sources

| Source | API key needed | Notes |
|---|---|---|
| Modrinth | No | Filters by MC version and loader, and supports Unicode queries natively |
| CurseForge | Yes | Set it in `curseforge_api_key` under `config.json`. When unset, this source is skipped automatically |

CurseForge goes through the official API (`api.curseforge.com/v1`) first, falling back to a community proxy (`api.curse.tools/v1/cf`) on failure.

## Filtering

A search request carries two parameters from the current server:

- **MC version**: taken from `mc_version` in `.server.json`
- **Mod loader**: inferred from the core jar's file name, recognising `fabric` / `forge` / `neoforge` / `quilt`

So when you search for mods on a modern server, you will not be handed 1.16-era builds.

If applying both conditions leaves too few results, MSTL automatically loosens the scope and falls back to a fuzzy search, so you at least get hits first and can then judge for yourself whether the version fits.

## Install

1. The mod you pick is matched to a file that fits the current MC version and loader
2. It is downloaded into the server's `mods/` directory
3. Restart the server when you are ready

A single mod may have several fitting files (different loaders, different MC versions). MSTL picks the closest match, and anything that does not match is called out clearly rather than force-installed.

## Enable, disable, and delete

Same as plugins:

- `Enable` / `Disable`: toggle by renaming `.jar` ↔ `.jar.disabled`
- `Delete`: remove after a second confirmation
- `Import a local jar`: copy an existing mod file into `mods/`

## Common issues

A CurseForge search that returns nothing is almost always a missing key. Apply for one for free at the [CurseForge Console](https://console.curseforge.com/) and drop it into `config.json`. Modrinth needs no configuration at all; start there.

## Related

- Server plugins: [Plugin management](plugin-management.md)
- Configuration field reference: [Configuration](../guide/configuration.md)
