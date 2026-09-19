# Plugin management

Entry point: main menu → `[4] Manage plugins/mods`, pick a server → `Plugins`.

## Sources

| Source | API | Notes |
|---|---|---|
| SpigotMC | Spiget API (`api.spiget.org/v2`) | A third-party read-only API for the Spigot resource site; the widest plugin coverage |
| Modrinth | `api.modrinth.com/v2` | A modern platform, no API key needed |
| Hangar | `hangar.papermc.io/api/v1` | PaperMC's official platform, where Paper / Velocity plugins concentrate |

A search hits all three sources at once, takes the top hits from each, then sorts them by download count so obscure plugins do not float to the top.

## Search and install

1. Enter a keyword; Chinese works too (depending on the platform index, see below)
2. Pick one from the merged results
3. MSTL resolves the download URL and drops it into `plugins/`
4. If the platform returns an externally hosted link, see the next section

## Externally hosted plugins

Some Spigot plugins do not host their jar on SpigotMC at all, but on GitHub Releases. In that case:

1. MSTL detects the external link
2. It calls the GitHub API to find the repository's latest release
3. It picks the jar out of the release assets and downloads it

If GitHub cannot provide it either (private repository, no jar attached to the release, and so on), you are prompted to download manually.

## Enable, disable, and delete

- `Enable` / `Disable`: done by renaming. When disabled the file becomes `xxx.jar.disabled`, the server will not load it, and you can switch it back at any time. Handy for temporarily turning off a plugin to chase a conflict without re-downloading
- `Delete`: removes the file after a second confirmation
- `Import a local jar`: copies a jar into `plugins/` from any path on disk, useful for plugins you compiled yourself or got from elsewhere

## Language and search tips

A platform index relies on the text in a plugin's description. The vast majority of plugins use English names and descriptions, so **English keywords hit noticeably more often**; a plugin can only be found by a Chinese keyword if Chinese appears in its description.

When a search comes up empty, try a more generic English word, or find the plugin on the platform's web UI and search for its name back here.

## Related

- Mods (Fabric / Forge / NeoForge side): [Mod management](mod-management.md)
- The mechanics behind the plugin directory and its enabled state: [Server management](../guide/server-management.md)
