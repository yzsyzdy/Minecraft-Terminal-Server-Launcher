<div class="mstl-band">
  <span class="mstl-band__mark">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" aria-hidden="true">
      <defs><linearGradient id="mstl-band-mark" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6B78E6"/><stop offset="1" stop-color="#4C5BD4"/></linearGradient></defs>
      <rect width="48" height="48" rx="11" fill="url(#mstl-band-mark)"/>
      <path d="M14 16.5 L22 24 L14 31.5" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M26 31.5 L35 31.5" fill="none" stroke="#FFFFFF" stroke-width="3" stroke-linecap="round"/>
    </svg>
  </span>
  <span class="mstl-band__name">MINECRAFT TERMINAL SERVER LAUNCHER</span>
  <span class="mstl-band__chips"><i>Windows</i><i>Linux</i><i>macOS</i></span>
</div>

# MSTL

**Minecraft Terminal Server Launcher** — a lightweight, zero-dependency Minecraft server manager.

A terminal tool written entirely with the Python standard library. It folds the whole chain of "download a core → set up Java → allocate memory → start the server → manage plugins and mods" into a single interactive menu. Windows, Linux and macOS all work.

## What it solves

Bringing a server online by hand means bouncing back and forth: hunting for a core on a mirror, checking whether your Java version matches, digging through the wiki for launch flags, then downloading plugins one at a time. MSTL chains these steps together in dependency order, giving every server an isolated directory of its own.

## Features at a glance

| Area | Capability |
|---|---|
| Server cores | 30+ cores (Paper / Purpur / Leaf / Fabric / Forge / NeoForge / Velocity …), fetched through the MSL mirror |
| Java | Detects system JDKs, validates against the MC version, auto-downloads GraalVM (17 / 21 / 25) when missing or mismatched |
| Plugins | Search and download across SpigotMC (Spiget), Modrinth, and Hangar |
| Mods | Modrinth (no key) and CurseForge (key required), filtered by MC version and loader |
| Instances | Multiple servers, version isolation, zip import, category-based export |
| Tunneling | Local frpc (`frpc.toml`) and OpenFrp cloud tunnels, two independent routes |
| UI | Interactive terminal menu with built-in 中文 / English / Русский |

## Getting started

- [Quick start](guide/quickstart.md): from nothing to your first running server
- [Configuration](guide/configuration.md): the full field reference for `config.json` and `.server.json`
- [Architecture](advanced/architecture.md): start here if you want to read or modify the source
- [FAQ](faq.md): failed downloads, empty search results, slow speeds, and so on

## License

Released under the **GNU General Public License v3.0**. The full text lives in the [LICENSE](https://github.com/yzsyzdy/Minecraft-Terminal-Server-Launcher/blob/main/LICENSE) file at the repository root.
