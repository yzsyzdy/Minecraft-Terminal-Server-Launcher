# Web panel

Entry point: `python start.py web`, on `http://127.0.0.1:8765` by default.

The terminal menu suits someone sitting at the machine. The web panel suits everyone else: away from home, on a phone, or simply tired of that black window.

## What it does

The panel is deliberately small and revolves around one loop: see instances → start → watch the console live → send commands / stop.

- **See instances**: lists everything under `servers/`, with version, core, and running state
- **Instance details**: selecting one shows a strip above the console with memory, PID or exit code, Java, working directory, and the assembled launch command
- **Start / stop**: stopping is graceful. It sends `stop` to the console first, and only force-kills after a timeout
- **Live console**: pushed over SSE, line by line, and appended to `logs/mstl-console.log` inside the instance directory at the same time
- **Send commands**: whatever you type in the box goes straight to the server process, equivalent to the interactive console in the terminal. There are also one-click `list` / `save-all` / `help` chips, while `say` just fills in the prefix
- **Console controls**: clear, toggle auto-scroll, colour `WARN` and `ERROR` separately, and pause following automatically when you scroll up

## Signing in

The first run generates a random token, writes it into `web.token` under `config.json`, and prints a URL carrying it:

```
Web panel started: http://127.0.0.1:8765/?token=xxxxxxxx
Access token: xxxxxxxxxxxxxxxx
```

Open that once and the token lands in a cookie; after that `http://127.0.0.1:8765/` is enough.

## Security boundary

**The panel can send commands to a server console, which makes it a remote command execution button.** The daemon runs as your user, so one click in a browser can `stop` a server, change ops, or touch files. Therefore:

- It listens on `127.0.0.1` only, unreachable from outside the machine
- Every `/api` route requires the token
- To reach it through frp, put a reverse proxy and HTTPS in front. Do not leave the panel bare, and think twice before changing `web.host`

## Configuration

| Field | Default | Description |
|---|---|---|
| `web.host` | `"127.0.0.1"` | Bind address; `0.0.0.0` means all interfaces |
| `web.port` | `8765` | Bind port |
| `web.token` | `""` | Access token; generated on first start when empty |

Command-line flags can override host and port for one run:

```bash
python start.py web --host 0.0.0.0 --port 9000
```

## API surface

The entire contract between the front end and the back end is this:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/state` | Instance list plus a batch of UI strings in the current language |
| `GET` | `/api/stream` | SSE event stream; one subscription covers every instance. Add `?name=<instance>` to narrow it to one |
| `POST` | `/api/start` | Body `{"name": "..."}` |
| `POST` | `/api/stop` | Body `{"name": "..."}` |
| `POST` | `/api/command` | Body `{"name": "...", "command": "..."}` |

There are only two event shapes: `{"type": "line", ...}` for a console line and `{"type": "state", ...}` for a state change. Every event carries an `instance` field saying where it came from. Lines with `kind: "note"` are produced by MSTL itself, already translated by the server before being pushed.

## Known limits

This is the closed loop, not a full operations panel. Not yet done: importing or downloading instances from the browser, plugin and mod management, tunnel management, and restoring "which servers were running" after a restart. Each of those would plug into the same `core/` surface.
