# Web panel

Three ways in, pick whichever suits you:

- **Main menu `[8] Web panel`** — visible as soon as you double-click the exe or run `python start.py`; nothing to memorise
- **`python start.py web`** — starts the panel directly, on `http://127.0.0.1:8765` by default
- **`python start.py --help`** — for when you have forgotten the options

The panel submenu also lets you change the bind address / port, regenerate the access token, and **make the panel start with the app**; on start it prints a URL carrying that token, and opening it logs you in.

With "start with the app" enabled, the panel runs on a background thread as soon as the app starts — the terminal still reaches its menu, and any server you start still gets the terminal.

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
- **New instance**: download a server core from the MSL mirror, use a local jar, import an existing server zip archive, or create a **generic instance**
- **Instance settings**: memory, extra JVM arguments, process priority, **start with the app**, a generic instance's launch spec, and the OpenFrp tunnel binding
- **frp tunneling**: edit `frpc.toml`, add and remove tunnels, start and stop frpc, download or update the frpc binary (with a manual upload fallback)
- **Visible progress**: downloading a server core, downloading frpc and uploading files all show a progress bar along the bottom — a tens-of-megabytes core no longer means staring at a spinning button
- **Dark mode**: follows the system by default; the `◐` button in the top bar cycles auto → dark → light and the choice is remembered in the browser

The console and the file manager are two tabs of the right-hand pane; switching tabs does not drop the SSE connection. Instance settings (`⚙`) and frp (`frp` in the top bar) are separate dialogs.

## File manager

Select an instance and switch to the **Files** tab:

- Click a folder to enter it; click a text file (`server.properties`, `ops.json`, the various `.yml` files) to open the editor; click anything else to download it
- Each row carries action buttons: download, edit, rename, delete; archives get an extra **Extract**
- The toolbar goes up a level, jumps to the instance root, refreshes, creates a file or folder, uploads a file, and **uploads a folder**

### Uploading a folder

The toolbar's *Upload folder* uses the browser's directory picker. The whole directory comes up with its
relative paths intact: pick `world` and you get `world/level.dat`, `world/region/…`. Missing intermediate
directories are created as needed (the browser does not promise an order — `world/level.dat` may well
arrive before `world/`). The progress bar shows `Uploading 12/340`.

One browser limitation: **empty directories cannot be uploaded** — the directory picker simply never
reports them, so they are not created.

### Extracting archives

An archive row has an *Extract* button (`⇲`). After a confirmation it unpacks **in place** (into the
archive's own directory). zip and the tar family (`.tar` / `.tar.gz` / `.tgz`) are supported, detected by
**content** rather than by extension — browsers rename things.

- **Nothing is overwritten by default**: existing files are skipped. Extraction usually means topping up
  an instance that already exists, and silently clobbering `server.properties` is far worse than
  refusing. The result reports how many were skipped
- Extracted entries obey the same path rules: `..`, absolute paths and drive letters in member names
  are refused, and symlinks / hard links inside the archive are skipped outright (a link pointing
  outside the instance would sidestep the sandbox)
- Two more gates stop zip bombs: at most 200,000 members, and at most 4 GiB unpacked

Limits on paths and sizes:

- Every path is locked inside the selected instance. A relative path containing `..` or a drive letter is rejected, and a symlink or junction pointing outside the instance is rejected after being resolved. A leading `/` means "from the instance root", not an absolute filesystem path
- A single file can be edited up to **2 MB**. Anything larger, anything binary (containing a NUL byte), or anything that is not UTF-8 text is refused — download it instead
- A single upload is capped at **512 MB** and is streamed to disk rather than held in memory; folder uploads go file by file, each under the same cap
- Writes go through "temp file + atomic replace", so a failure halfway cannot truncate the original
- The instance root itself cannot be deleted; deleting a folder removes everything inside it

## New instance

The `＋` in the top bar opens a dialog with one tab per approach:

**Download core** — pick a server core (Paper, Purpur, Fabric, Forge, Velocity, …) and a version from the MSL mirror. The core version *is* the MC version, so it is filled into the MC version field automatically and stays editable. If the download fails, the empty directory created moments earlier is rolled back rather than left as a half-built instance.

**Local jar** — pick a local `.jar`. The panel creates the instance first (with the jar name already in its config) and then uploads the file. If the upload fails the instance is kept, and you can retry through the file manager.

**Import archive** — give a zip path **on the machine running the panel** (not an upload from the browser), press *Scan archive* to list the candidate core jars inside, pick one, then press *Import*. Scanning and committing are two separate steps, mirroring `plan_import` / `commit_import` in `core/`; asking the user when there is more than one candidate is the interface layer's job.

**Generic instance** — see the next section. It does not run Minecraft; it runs a program or command of your choosing.

Instance name rules: non-empty, at most 64 characters, no `\ / : * ? " < > |`, and not a Windows reserved device name (`con`, `nul`, …). A duplicate name is an error rather than a silent rename to `name_1` — you chose the name, and renaming it would only make your instance harder to find.

## Generic instances

Besides Minecraft servers, MSTL can manage **any long-running program or command** as an instance: same start/stop, same live console, same log file, same ability to send it input, same optional tunnel. The only difference is that starting it does no Java resolution and no jar check. The terminal menu has the same entry point (`[3] New instance`).

An instance's `kind` is either `minecraft` (the default, the original Java + jar path) or `generic`. The `launch` block in `.server.json` describes how a generic instance starts:

| Launch mode | Behaviour |
|---|---|
| `exec` | Runs `program` (an executable) directly with the arguments from the `args` array, **without a shell** — so `&&` and pipes are *not* interpreted |
| `cmd` | `cmd.exe /c "<command>"` — the whole string goes to cmd, so pipes, redirection and `&&` work |
| `powershell` | `powershell -NoProfile -ExecutionPolicy Bypass -Command "<command>"` (`pwsh` elsewhere) |

`program` accepts both relative paths (resolved inside the instance directory) and absolute ones. `workdir` is the working directory, also relative to the instance directory when not absolute; leaving it empty means the instance directory itself.

Some real uses: dropping in your own Python or batch service script; running a custom command line such as `java -jar xxx.jar` through `cmd`; running a probe or scheduled task through `powershell`.

**Mind the trust boundary.** In `cmd` / `powershell` mode the configured string is handed to the shell verbatim — that is the feature, not an oversight. Its trust boundary is the same one as "the panel can send console commands" and "the panel can edit `.server.json`": anyone who can change this config could already run things on this machine.

There are two ways to change the launch spec: select the instance and press `⚙` in the pane header, or edit `.server.json` directly through the file manager (changes take effect on the next start). An instance's `kind` cannot be changed after creation — switching kinds would turn it into a different thing, and that should not happen silently inside "save settings".

## frp tunneling

The `frp` button in the top bar opens the tunnel panel, backed by `frp/frpc.toml`:

- **Status**: whether frpc is running and whether the binary has been downloaded
- **Connection settings**: `serverAddr` / `serverPort` / `auth.token`, written back to `frpc.toml` on save
- **Start / stop**: the same process the terminal menu drives
- **Download / update frpc**: fetch the binary from the mirror or GitHub Releases. **If the download will not go through you are not stuck**: the panel shows the GitHub releases page and an *Upload frpc* button — download the archive yourself (or the unpacked `frpc` / `frpc.exe`) and hand it over; archives are unpacked automatically
- **Tunnel list**: name, type, local address and port, remote port or domains, each removable
- **Add tunnel**: `tcp` / `udp` need a remote port, `http` / `https` need domains (comma separated)

The panel only touches those keys; everything else already in `frpc.toml` is read and written back untouched — including other arrays-of-tables such as `[[visitors]]`. It shares the same read/write implementation as the terminal menu. The one exception is **comments**: the minimal TOML reader/writer does not preserve them, so saving once drops them.

The upload fallback sniffs the **content**, not the filename (browsers rename things): a zip / tar.gz gets unpacked, a bare binary is taken as-is. It does check that what comes out **looks like an executable for this platform** — grabbing the wrong platform's archive (say a linux tarball on Windows) is refused on the spot and the half-written file is removed, rather than installing an `frpc.exe` that dies with a format error on first run.

> The panel manages **vanilla frp**. OpenFrp's cloud tunnels use a different API, and the panel only covers the small "bind it to an instance" step: fill in the user token and tunnel id under instance settings, and decide whether to raise the tunnel with the instance.

## Signing in

The first run generates a random token, writes it into `web.token` under `config.json`, and prints a URL carrying it:

```
Web panel started: http://127.0.0.1:8765/?token=xxxxxxxx
Access token: xxxxxxxxxxxxxxxx
```

Open that once and the token lands in a cookie; after that `http://127.0.0.1:8765/` is enough. Verification uses a constant-time comparison and only accepts the value of the `mstl_token` cookie itself.

The three ways of presenting a token (`X-MSTL-Token` header, `mstl_token` cookie, `?token=` query parameter) are **equally valid** — matching any one of them is enough. So when the browser still holds a cookie from an older token, the correct new token in the address bar still works, and it overwrites the stale cookie on the way in.

After "regenerate access token" in the menu, a **running panel accepts the new token immediately** — no restart needed; the menu prints the new URL for you.

### "Access token missing or invalid"

Check these in order:

1. **A stale cookie is in the way.** The browser may still hold a cookie from a previous token. Force-reload with `Ctrl+F5`, or clear this site's cookies, then open the full URL the menu printed.
2. **`127.0.0.1` vs `localhost`.** The browser treats them as different sites, so cookies are not shared. Keep using whichever one you started with.
3. **You just regenerated the token.** Use the newly printed URL; the token in the old one is dead.

The 401 page lists these same three hints.

## Security boundary

**The panel can send commands to a server console, rewrite files inside the instance directory, and configure an instance to execute arbitrary commands — so it hands both a command channel and the instance tree to a browser.** The daemon runs as your user, so one click can `stop` a server, change ops, edit `server.properties`, delete files, or point a generic instance at any command. Therefore:

- It listens on `127.0.0.1` only, unreachable from outside the machine
- Every `/api` route requires the token
- To reach it through frp, put a reverse proxy and HTTPS in front. Do not leave the panel bare, and think twice before changing `web.host`
- Holding the token means holding read/write access to the instance directory and this command channel; there is no switch that turns the file manager or generic instances off

## Configuration

| Field | Default | Description |
|---|---|---|
| `web.host` | `"127.0.0.1"` | Bind address; `0.0.0.0` means all interfaces |
| `web.port` | `8765` | Bind port; `0` means "pick a free one" |
| `web.token` | `""` | Access token; generated on first start when empty |
| `web.auto_start` | `false` | Start the panel with the app. Runs on a background thread and does not block the terminal menu; toggled under the panel submenu `[4]` |

!!! tip "About port 8765"
    8765 is also the default port of VS Code's Python debugger (debugpy). If you start the panel while debugging Python, the two compete for it; on Windows `SO_REUSEADDR` used to let the second bind "succeed" silently, which showed up as intermittent 401s or a panel that would not load. That option is now disabled on Windows, so the conflict is reported as a bind failure. Just pick another port.

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
| `POST` | `/api/file/extract` | `{"name": "...", "path": "...", "dest"?, "overwrite"?}` — unpack an archive in place |
| `POST` | `/api/file/upload` | `?name=&path=`; the request body *is* the file, read to exactly `Content-Length` |
| `GET` | `/api/cores` | Server core list; add `?type=<core>` for that core's versions |
| `POST` | `/api/instances` | `{"name", "kind"?, "mc_version", "min_mem", "max_mem", "core_type"?, "core_version"?, "jar_name"?, "launch"?}` |
| `POST` | `/api/instances/settings` | Update an instance: `{"name", "min_mem"?, "extra_jvm_args"?, "launch"?, "openfrp"?, "auto_start"?, ...}`; `kind` and `name` are not changeable |
| `POST` | `/api/instances/import/scan` | `{"zip_path": "..."}` — returns the candidate cores |
| `POST` | `/api/instances/import/commit` | `{"zip_path": "...", "jar": "...", "name": "..."}` |
| `POST` | `/api/instances/import/abort` | `{"zip_path": "..."}` — discards the extracted temp folder |
| `GET` | `/api/frp` | frpc status + connection settings + tunnel list |
| `POST` | `/api/frp/settings` | `{"serverAddr", "serverPort", "token"}` |
| `POST` | `/api/frp/proxy` | Add a tunnel: `{"name", "type", "localIP", "localPort", "remotePort"?\|"customDomains"?}` |
| `POST` | `/api/frp/proxy/delete` | `{"index": 0}` |
| `POST` | `/api/frp/start` | Start frpc |
| `POST` | `/api/frp/stop` | Stop frpc |
| `POST` | `/api/frp/download` | Download / update the frpc binary |

Conventions:

- A failure always returns `{"ok": false, "key": "<i18n key>", "message": "<explanation in the current language>"}` with HTTP 400; an unexpected exception returns 500. The front end simply shows `message`
- There are three event shapes: `{"type": "line", ...}` for a console line, `{"type": "state", ...}` for a state change, and `{"type": "job", ...}` for long-task progress (carrying `label` / `ratio` / `done`). `line` and `state` carry an `instance` field saying where they came from, plus a per-instance monotonic `seq` — on reconnect the server replays the whole history, and the front end uses `seq` to skip lines it has already drawn instead of duplicating them. `job` belongs to no instance, so it is never filtered by the per-instance subscription
- Lines with `kind: "note"` are produced by MSTL itself, already translated by the server before being pushed
- The download POSTs are synchronous and their progress rides the SSE stream above; uploads report progress straight from the browser (`XMLHttpRequest.upload.onprogress`), with the server uninvolved

## Known limits

This is the closed loop plus a few everyday panels, not a full operations panel. Not yet done: plugin and mod management, restoring "which servers were running" after a restart, and managing OpenFrp cloud tunnels from the panel (creating and deleting those still goes through the terminal menu).

The file manager keeps deliberate limits too: one file at a time (no multi-select or bulk delete), **empty folders cannot be uploaded** (the directory picker never reports them), extraction covers zip and tar only (7z / rar would need an external tool), there is no disk-usage column and no resumable transfers.

Each of those would plug into the same `core/` surface.
