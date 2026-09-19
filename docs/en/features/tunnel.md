# Tunneling

Entry point: main menu → `[6] Tunneling`.

A server at home or on a campus network has no public address by default, so nobody outside can reach it. Tunneling means running a client on your own machine that keeps a long-lived connection to a provider's public node and forwards outside traffic to a local port.

MSTL offers two independent routes; pick one as needed.

## Route one: OpenFrp cloud tunnels

For when you do not want to write a config file yourself and just want a ready-made public address.

1. Register at the [OpenFrp console](https://console.openfrp.net) and grab your **user token**
2. `[6] Tunneling` → `OpenFrp` → view the tunnel list, then enter the token
3. Pick a tunnel from the list, or create one right in the menu (choose a node, a region, and a local port)
4. Use `Bind tunnel to server` to attach the tunnel to an instance; this writes the `openfrp` field into that server's `.server.json`
5. From then on, starting the server brings frpc up with it

`openfrp.auto_start` controls whether frpc starts alongside the server; binding asks you once.

**You do not need to install frpc yourself.** MSTL detects the current operating system and CPU architecture, fetches the matching binary from OpenFrp's release API, and downloads it. The resulting `frpc.exe` is excluded by `.gitignore` and never enters the repository.

## Route two: local frp (frpc.toml)

For when you already have your own frp server (self-hosted, or an `frpc.toml` template handed to you by a provider) and want full manual control.

Things the menu lets you do:

- `Download frpc`: fetch and unzip the archive for the current platform from frp's official releases
- `Edit config`: read and write `frp/frpc.toml`, covering `serverAddr`, `serverPort`, `auth.token`, and each proxy
- `Proxy list` / `Add proxy` / `Delete proxy`: maintain the `[[proxies]]` sections, specifying `localPort` and `remotePort`
- `Start frpc` / `Stop frpc`: bring the process up or take it down by hand

`frpc.toml` normally contains `auth.token`, so it and the frpc binary are excluded by `.gitignore`; only the `frp/*.py` source is tracked.

## How the two routes differ

| | OpenFrp | Local frp |
|---|---|---|
| Public node | Provided by OpenFrp | Your own, or your provider's |
| Token | User token, tunnels managed in the cloud | Written into `frpc.toml` |
| Config location | Stored in the server's `.server.json` | A standalone file, `frp/frpc.toml` |
| Start with the server | Supported | Manual |

In code these really are two separate implementations (`openfrp/` and `frp/`) that do not override each other, so both can be installed at once.

## Troubleshooting

- The tunnel shows as online but nobody can connect: confirm the bound local port matches the `server-port` in `server.properties` (25565 by default) and not something else
- frpc will not start: check the error in the window; it is usually an expired token or a `remotePort` already in use
- To keep the server internal-only: simply do not bind a tunnel, and leave `config.json` alone
