# Web panel

Entry point: `python start.py web`, on `http://127.0.0.1:8765` by default.

The terminal menu suits someone sitting at the machine. The web panel suits everyone else: away from home, on a phone, or simply tired of that black window.

## What it does

The panel revolves around one loop — see instances → start → watch the console live → send commands / stop — with a file manager and instance creation alongside it.

- **See instances**: lists everything under `servers/`, with version, core, and running state
- **Instance details**: selecting one shows a strip above the console with memory, PID or exit code, Java, working directory, and the assembled launch command
- **Start / stop**: stopping is graceful. It sends `stop` to the console first, and only force-kills after a timeout
- **Live console**: pushed over SSE, line by line, and appended to `logs/mstl-console.log` inside the instance directory at the same time
- **Send commands**: whatever you type in the box goes straight to the server process, equivalent to the interactive console in the terminal. There are also one-click `list` / `save-all` / `help` chips, while `say` just fills in the prefix
- **Console controls**: clear, toggle auto-scroll, colour `WARN` and `ERROR` separately, and pause following automatically when you scroll up
- **File manager**: browse an instance's tree and edit text files such as `server.properties` in place; create files and folders, rename, delete, upload, download
- **New instance**: download a server core from the MSL mirror, use a local jar, or import an existing server zip archive

The console and the file manager are two tabs of the right-hand pane; switching tabs does not drop the SSE connection.

## File manager

Select an instance and switch to the **Files** tab:

- Click a folder to enter it; click a text file (`server.properties`, `ops.json`, the various `.yml` files) to open the editor; click anything else to download it
- Each row has four actions: download, edit, rename, delete. Deleting a folder removes everything inside it
- The toolbar goes up a level, jumps to the instance root, refreshes, creates a file or folder, and uploads

Limits on paths and sizes:

- Every path is locked inside the selected instance. A relative path containing `..` or a drive letter is rejected, and a symlink or junction pointing outside the instance is rejected after being resolved. A leading `/` means "from the instance root", not an absolute filesystem path
- A single file can be edited up to **2 MB**. Anything larger, anything binary (containing a NUL byte), or anything that is not UTF-8 text is refused — download it instead
- A single upload is capped at **512 MB** and is streamed to disk rather than held in memory
- Writes go through "temp file + atomic replace", so a failure halfway cannot truncate the original
- The instance root itself cannot be deleted

## New instance

The `＋` in the top bar opens a dialog with one tab per approach:

**Download core** — pick a server core (Paper, Purpur, Fabric, Forge, Velocity, …) and a version from the MSL mirror. The core version *is* the MC version, so it is filled into the MC version field automatically and stays editable. If the download fails, the empty directory created moments earlier is rolled back rather than left as a half-built instance.

**Local jar** — pick a local `.jar`. The panel creates the instance first (with the jar name already in its config) and then uploads the file. If the upload fails the instance is kept, and you can retry through the file manager.

**Import archive** — give a zip path **on the machine running the panel** (not an upload from the browser), press *Scan archive* to list the candidate core jars inside, pick one, then press *Import*. Scanning and committing are two separate steps, mirroring `plan_import` / `commit_import` in `core/`; asking the user when there is more than one candidate is the interface layer's job.

Instance name rules: non-empty, at most 64 characters, no `\ / : * ? " < > |`, and not a Windows reserved device name (`con`, `nul`, …). A duplicate name is an error rather than a silent rename to `name_1` — you chose the name, and renaming it would only make your instance harder to find.

## Signing in

The first run generates a random token, writes it into `web.token` under `config.json`, and prints a URL carrying it:

```
Web panel started: http://127.0.0.1:8765/?token=xxxxxxxx
Access token: xxxxxxxxxxxxxxxx
```

Open that once and the token lands in a cookie; after that `http://127.0.0.1:8765/` is enough. Verification uses a constant-time comparison and only accepts the value of the `mstl_token` cookie itself.

## Security boundary

**The panel can send commands to a server console and rewrite files inside the instance directory, so it hands both a command channel and the instance tree to a browser.** The daemon runs as your user, so one click can `stop` a server, change ops, edit `server.properties`, or delete files. Therefore:

- It listens on `127.0.0.1` only, unreachable from outside the machine
- Every `/api` route requires the token
- To reach it through frp, put a reverse proxy and HTTPS in front. Do not leave the panel bare, and think twice before changing `web.host`
- Holding the token means holding read/write access to the instance directory; there is no switch that turns the file manager off

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

The entire contract between the front end and the back end:

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/state` | Instance list plus a batch of UI strings in the current language |
| `GET` | `/api/stream` | SSE event stream; one subscription covers every instance. Add `?name=<instance>` to narrow it to one |
| `POST` | `/api/start` | Body `{"name": "..."}` |
| `POST` | `/api/stop` | Body `{"name": "..."}` |
| `POST` | `/api/command` | Body `{"name": "...", "command": "..."}` |
| `GET` | `/api/files` | `?name=<instance>&path=<relative>` — list a folder |
| `GET` | `/api/file` | `?name=&path=` — read a text file |
| `GET` | `/api/file/raw` | `?name=&path=` — download raw bytes |
| `POST` | `/api/file/write` | `{"name": "...", "path": "...", "content": "..."}` |
| `POST` | `/api/file/mkdir` | `{"name": "...", "path": "..."}` |
| `POST` | `/api/file/rename` | `{"name": "...", "path": "...", "new_name": "..."}` |
| `POST` | `/api/file/delete` | `{"name": "...", "path": "..."}` |
| `POST` | `/api/file/upload` | `?name=&path=`; the request body *is* the file, read to exactly `Content-Length` |
| `GET` | `/api/cores` | Server core list; add `?type=<core>` for that core's versions |
| `POST` | `/api/instances` | `{"name", "mc_version", "min_mem", "max_mem", "core_type"?, "core_version"?, "jar_name"?}` |
| `POST` | `/api/instances/import/scan` | `{"zip_path": "..."}` — returns the candidate cores |
| `POST` | `/api/instances/import/commit` | `{"zip_path": "...", "jar": "...", "name": "..."}` |
| `POST` | `/api/instances/import/abort` | `{"zip_path": "..."}` — discards the extracted temp folder |

Conventions:

- A failure always returns `{"ok": false, "key": "<i18n key>", "message": "<explanation in the current language>"}` with HTTP 400; an unexpected exception returns 500. The front end simply shows `message`
- There are two event shapes: `{"type": "line", ...}` for a console line and `{"type": "state", ...}` for a state change. Every event carries an `instance` field saying where it came from, plus a per-instance monotonic `seq` — on reconnect the server replays the whole history, and the front end uses `seq` to skip lines it has already drawn instead of duplicating them
- Lines with `kind: "note"` are produced by MSTL itself, already translated by the server before being pushed

## Known limits

This is the closed loop plus two everyday panels, not a full operations panel. Not yet done: plugin and mod management, tunnel management, and restoring "which servers were running" after a restart. The file manager keeps deliberate limits too: one file at a time (no multi-select or bulk delete), no whole-folder upload (browsers cannot provide folder contents), no disk-usage column, and no archive extraction.

Each of those would plug into the same `core/` surface.
