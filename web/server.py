"""
MSTL 网页面板：最小闭环。

闭环只有四个动作：看实例 → 启动 → 实时看控制台 → 敲指令 / 停止。
HTTP 用标准库，事件推送用 SSE，前端是一张不依赖任何构建步骤的静态页。

安全边界（默认值故意保守）：

面板里能往服务端控制台发指令，等于一枚远程命令执行按钮。守护进程跑在
你的用户身份下，浏览器点一下就能 stop、改 op、动文件。所以

  - 默认只监听 127.0.0.1，要对外（比如经 frp 穿出去）得自己在配置里改 host
  - 所有 /api 都要求 token，token 首次启动随机生成并写进 config.json
  - 真要暴露到公网，请在前面加一层反向代理与 HTTPS，别让面板裸奔

这里只做传输：core 的事件流翻译成 SSE，HTTP 请求翻译成 core 调用。
"""

from __future__ import annotations

import atexit
import hmac
import json
import os
import queue
import secrets
import shutil
import threading
import traceback
import urllib.parse
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from config import load_config, save_config
from core import archive as archives
from core import files as fileops
from core import frp as frpops
from core import provision
from core.errors import CoreError
from core.runtime import RuntimeManager
from core.transfer import abort_import
from i18n import detect_os_language, load_language, t
from paths import bundled_dir
from server_manager import (
    KIND_GENERIC,
    KIND_MINECRAFT,
    LAUNCH_EXEC,
    ensure_servers_folder,
    list_servers,
)

COOKIE_NAME = "mstl_token"

# 通过面板上传单个文件的大小上限（服务端 jar 通常 40~80 MB）
MAX_UPLOAD_BYTES = 512 * 1024 * 1024

# 前端需要的那几句文案，由服务端统一下发，前端只负责摆放
WEB_STRINGS = (
    "web.panel.title",
    "web.panel.instances",
    "web.panel.no_instances",
    "web.panel.console",
    "web.panel.command",
    "web.panel.send",
    "web.panel.start",
    "web.panel.stop",
    "web.state.stopped",
    "web.state.running",
    "web.state.stopping",
    "web.auth.invalid",
    "web.auth.help",
    "web.panel.clear",
    "web.panel.follow",
    "web.panel.jump_bottom",
    "web.panel.quick",
    "web.detail.memory",
    "web.detail.pid",
    "web.detail.exit_code",
    "web.detail.command",
    "web.detail.workdir",
    "web.conn.online",
    "web.conn.connecting",
    "web.conn.offline",
    # 文件管理器
    "web.tab.console",
    "web.tab.files",
    "web.files.up",
    "web.files.root",
    "web.files.empty",
    "web.files.name",
    "web.files.size",
    "web.files.modified",
    "web.files.refresh",
    "web.files.new_file",
    "web.files.new_dir",
    "web.files.upload",
    "web.files.download",
    "web.files.rename",
    "web.files.delete",
    "web.files.edit",
    "web.files.save",
    "web.files.cancel",
    "web.files.close",
    "web.files.editing",
    "web.files.confirm_delete",
    "web.files.prompt_new_file",
    "web.files.prompt_new_dir",
    "web.files.prompt_rename",
    "web.files.saved",
    "web.files.uploading",
    "web.files.upload_done",
    "web.files.too_big_to_edit",
    "web.files.dir",
    "web.files.file",
    "web.files.extract",
    "web.files.extracting",
    "web.files.extract_done",
    "web.files.extract_confirm",
    "web.files.upload_folder",
    "web.files.uploading_folder",
    "web.files.folder_done",
    "web.files.folder_partial",
    # 新建实例
    "web.new.title",
    "web.new.name",
    "web.new.mc_version",
    "web.new.min_mem",
    "web.new.max_mem",
    "web.new.tab_core",
    "web.new.tab_jar",
    "web.new.tab_import",
    "web.new.core_type",
    "web.new.core_version",
    "web.new.loading",
    "web.new.create",
    "web.new.create_blank",
    "web.new.creating",
    "web.new.jar_hint",
    "web.new.jar_pick",
    "web.new.import_hint",
    "web.new.import_path",
    "web.new.import_scan",
    "web.new.import_jar",
    "web.new.import_commit",
    "web.new.done",
    "web.new.name_placeholder",
    "web.new.optional",
    # 通用实例
    "web.new.tab_generic",
    "web.new.launch_mode",
    "web.new.launch_exec",
    "web.new.launch_cmd",
    "web.new.launch_powershell",
    "web.new.program",
    "web.new.program_hint",
    "web.new.command",
    "web.new.command_hint",
    "web.new.args",
    "web.new.args_hint",
    "web.new.workdir",
    "web.new.workdir_hint",
    # 实例设置
    "web.settings.title",
    "web.settings.priority",
    "web.settings.mc",
    "web.settings.generic",
    "web.settings.openfrp",
    "web.settings.openfrp_hint",
    "web.settings.token",
    "web.settings.proxy_id",
    "web.settings.auto_start",
    "web.settings.save",
    "web.settings.saved",
    "web.settings.kind_locked",
    "web.settings.auto_start_instance",
    "web.settings.auto_start_instance_hint",
    # frp 面板
    "web.frp.title",
    "web.frp.status",
    "web.frp.running",
    "web.frp.stopped",
    "web.frp.binary_ok",
    "web.frp.binary_missing",
    "web.frp.server_addr",
    "web.frp.server_port",
    "web.frp.token",
    "web.frp.save",
    "web.frp.saved",
    "web.frp.start",
    "web.frp.stop",
    "web.frp.download",
    "web.frp.downloading",
    "web.frp.downloaded",
    "web.frp.proxies",
    "web.frp.no_proxies",
    "web.frp.proxy_name",
    "web.frp.proxy_type",
    "web.frp.local_ip",
    "web.frp.local_port",
    "web.frp.remote_port",
    "web.frp.domains",
    "web.frp.add_proxy",
    "web.frp.remove",
    "web.frp.confirm_remove",
    "web.frp.started",
    "web.frp.stopped_msg",
    "web.cli.missing_assets",
    "web.frp.manual",
    "web.frp.manual_hint",
    "web.frp.releases",
    "web.frp.upload",
    "web.frp.uploading",
    "web.frp.upload_done",
)


def web_settings(config: dict) -> tuple[str, int, str]:
    """把 config.json 里的 web 段收敛成 (host, port, token)。"""
    web = config.get("web")
    if not isinstance(web, dict):
        web = {}
    host = str(web.get("host") or "127.0.0.1")
    # 端口 0 是「随便挑一个空闲端口」，是合法配置；只在值缺失或非法时才回退，
    # 写成 `port or 8765` 会把 0 一起吞掉
    try:
        port = int(web.get("port"))
    except (TypeError, ValueError):
        port = 8765
    token = str(web.get("token") or "")
    return host, port, token


def web_auto_start(config: dict) -> bool:
    """config.json 里的 web.auto_start：面板是否随软件启动。"""
    web = config.get("web")
    if not isinstance(web, dict):
        return False
    return bool(web.get("auto_start"))


# ---------------------------------------------------------------------------
# 进程内共享的运行时管理器
# ---------------------------------------------------------------------------

_manager_lock = threading.Lock()
_shared_manager: Optional[RuntimeManager] = None


def shared_manager(project_dir: str) -> RuntimeManager:
    """进程内共用一个 RuntimeManager。

    软件启动时后台拉起的实例、和网页面板里看到的那批，必须是同一个管理器，
    否则面板会显示它们「未运行」，用户还能在面板里再启动一次——同一台服就
    被拉起两遍了。
    """
    global _shared_manager
    with _manager_lock:
        if _shared_manager is None:
            _shared_manager = RuntimeManager(project_dir)
        return _shared_manager


# ---------------------------------------------------------------------------
# 长任务进度（下载服务端核心、下载 frpc……）
# ---------------------------------------------------------------------------

class JobBus:
    """下载这类与实例无关的进度，走这条独立的事件流。

    塞进 RuntimeManager 的事件总线并不合适：那边的事件都带 instance，
    而「正在下载服务端核心」属于整个面板，不属于哪台实例。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list = []
        self._seq = 0

    def subscribe(self, callback) -> Any:
        with self._lock:
            self._subs.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subs:
                    self._subs.remove(callback)

        return unsubscribe

    def publish(self, payload: dict) -> None:
        with self._lock:
            self._seq += 1
            payload = dict(payload, seq=self._seq)
            subs = list(self._subs)
        for callback in subs:
            try:
                callback(payload)
            except Exception:  # noqa: BLE001
                pass


JOB_BUS = JobBus()


def progress_reporter(label: str):
    """产出一个给 core 下载函数用的进度回调 -> 面板进度条。

    回调里绝不能把异常抛回去：它是下载链路上的一环，进度画不出来不该让
    下载失败。
    """
    def report(ratio: float, downloaded: int = 0, total: int = 0) -> None:
        try:
            JOB_BUS.publish({
                "type": "job",
                "label": label,
                "ratio": max(0.0, min(1.0, float(ratio or 0.0))),
                "downloaded": int(downloaded or 0),
                "total": int(total or 0),
                "done": False,
            })
        except Exception:  # noqa: BLE001
            pass
    return report


def finish_job(label: str, message: str = "") -> None:
    JOB_BUS.publish({"type": "job", "label": label, "ratio": 1.0,
                     "done": True, "message": message})


def _render_event(event: dict) -> dict:
    """把带 i18n key 的事件翻译成人能看的文本。"""
    if event.get("type") == "line" and event.get("kind") == "note":
        out = dict(event)
        out["text"] = t(event.get("key", ""), **(event.get("kwargs") or {}))
        return out
    return event


def _index_html() -> str:
    path = os.path.join(bundled_dir(), "webui", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# 运行时要读的随包资源。打包脚本漏了 include-data-dir 时，界面上的表现是
# 「面板打不开」或「界面全是 i18n key」，很难联想到打包；启动时先点名说清楚。
_BUNDLED_ASSETS = (
    os.path.join("webui", "index.html"),
    os.path.join("locales", "zh.json"),
    os.path.join("locales", "en.json"),
)


def missing_assets() -> list[str]:
    """返回缺失的随包资源（相对路径）。源码模式下应当永远是空的。"""
    base = bundled_dir()
    return [rel for rel in _BUNDLED_ASSETS
            if not os.path.isfile(os.path.join(base, rel))]


def _warn_missing_assets() -> None:
    missing = missing_assets()
    if missing:
        print(t("web.cli.missing_assets", files=", ".join(missing)))


class _BodyStream:
    """包一层请求体，顺便记下到底读走了多少字节。

    上传是交给 core 去读的，读完多少处理器并不知道；而 _drain 只能补读
    「还没读过的」那部分——多读一次就会卡在下一个请求上（长连接不会关）。
    """

    def __init__(self, handler) -> None:
        self._handler = handler

    def read(self, size: int = -1) -> bytes:
        data = self._handler.rfile.read(size)
        self._handler._body_read += len(data)
        return data


# 导入分「解压出计划 → 选核心 → 落地」两步，中间要把解压结果留在磁盘上。
# plan_import 用的临时目录是固定的 .import_temp，所以同一时刻只有最新那一份
# 计划是有效的：新的一次解压会把上一份覆盖掉。这里也只保留最新的一份，并且
# 不对旧计划调 abort——那会连新解压出来的目录一起删掉。
_IMPORT_PLANS: dict[str, Any] = {}
_IMPORT_PLANS_LOCK = threading.Lock()


def _remember_plan(plan) -> None:
    with _IMPORT_PLANS_LOCK:
        _IMPORT_PLANS.clear()
        _IMPORT_PLANS[plan.zip_path] = plan


def _take_plan(zip_path: str):
    with _IMPORT_PLANS_LOCK:
        return _IMPORT_PLANS.pop(zip_path, None)


def _make_handler(manager: RuntimeManager, servers_dir: str, token: str,
                  project_dir: str):
    """把 manager、token 与目录关进闭包，产出一个请求处理器类。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "MSTL"
        protocol_version = "HTTP/1.1"
        _body_read = 0

        # -- 基础设施 ------------------------------------------------------

        def log_message(self, fmt: str, *args: Any) -> None:
            """默认会把每条请求打到 stderr，面板不需要这种噪音。"""

        def _query(self) -> dict[str, str]:
            parsed = urlparse(self.path)
            return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}

        def _presented_token(self, query: dict[str, str]) -> str:
            """按 请求头 → Cookie → query 的顺序取出客户端出示的 token。"""
            header = self.headers.get("X-MSTL-Token")
            if header:
                return header
            raw_cookie = self.headers.get("Cookie") or ""
            if raw_cookie:
                jar: SimpleCookie = SimpleCookie()
                try:
                    jar.load(raw_cookie)
                except CookieError:
                    jar = SimpleCookie()
                morsel = jar.get(COOKIE_NAME)
                if morsel is not None:
                    return morsel.value
            return query.get("token") or ""

        def _authed(self, query: dict[str, str]) -> bool:
            if not token:
                return True
            presented = self._presented_token(query)
            # 定长比较：用 == 逐字符短路会通过响应时间泄露 token 前缀。
            # 另外别再拿 "mstl_token=xxx" 去 in 整个 Cookie 头做子串匹配，
            # 别的 cookie 的值里带上这一串也会被当成通过。
            return bool(presented) and hmac.compare_digest(presented, token)

        def _send(self, body: bytes, status: int, content_type: str,
                  extra_headers: Optional[list[tuple[str, str]]] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            # 面板前后端都在本机迭代，别让浏览器拿旧页面
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or []):
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: dict, status: int = HTTPStatus.OK,
                  extra_headers: Optional[list[tuple[str, str]]] = None) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(body, status, "application/json; charset=utf-8", extra_headers)

        def _page(self, html: str, status: int = HTTPStatus.OK,
                  extra_headers: Optional[list[tuple[str, str]]] = None) -> None:
            self._send(html.encode("utf-8"), status,
                       "text/html; charset=utf-8", extra_headers)

        def _unauthorized(self) -> None:
            self._page(
                "<!doctype html><meta charset='utf-8'>"
                "<body style='font:14px system-ui;color:#1f2430;padding:24px;"
                "background-color:#ffffff;"
                "background-image:linear-gradient(180deg,#f7f8ff,#ffffff 260px);"
                "background-repeat:no-repeat'>"
                f"<p>{t('web.auth.invalid')}</p><p style='color:#6b7480'>{t('web.auth.help')}</p>",
                HTTPStatus.UNAUTHORIZED,
            )

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if length <= 0:
                return {}
            raw = _BodyStream(self).read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def _content_length(self) -> int:
            try:
                return max(0, int(self.headers.get("Content-Length") or 0))
            except ValueError:
                return 0

        def _drain(self, length: int) -> None:
            """把还没读的请求体读掉。

            HTTP/1.1 上是长连接：请求体没读完就回响应，下一个请求会从残留
            的字节开始解析，整条连接就错位了。只补读「还没读过的」那部分，
            否则会阻塞在下一个请求上——上传到一半失败时就是这样。
            """
            remaining = max(0, length - self._body_read)
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)

        def _core_error(self, err: CoreError) -> None:
            self._json({"ok": False, "key": err.key,
                        "message": t(err.key, **err.kwargs)},
                       HTTPStatus.BAD_REQUEST)

        def _server_error(self, err: Exception) -> None:
            """兜底：别让一个没预料到的异常变成空响应，前端会一直转圈。"""
            traceback.print_exc()
            self._json({"ok": False, "message": f"{type(err).__name__}: {err}"},
                       HTTPStatus.INTERNAL_SERVER_ERROR)

        def _instance_dir(self, name: str) -> str:
            """按实例名解析实例目录。只认 servers/ 下真实存在的实例。"""
            wanted = str(name or "")
            if not wanted:
                raise CoreError("web.error.unknown_instance", name=wanted)
            for entry in list_servers(servers_dir):
                entry_name = entry.get("name") or os.path.basename(
                    os.path.normpath(entry["_path"]))
                if entry_name == wanted:
                    return entry["_path"]
            raise CoreError("web.error.unknown_instance", name=wanted)

        # -- 路由 ----------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 的约定)
            path, query = self._query()
            if path in ("/", "/index.html"):
                if not self._authed(query):
                    return self._unauthorized()
                headers = []
                if query.get("token"):
                    headers.append(("Set-Cookie",
                                    f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax"))
                try:
                    return self._page(_index_html(), extra_headers=headers)
                except OSError:
                    return self._page("webui/index.html missing", HTTPStatus.INTERNAL_SERVER_ERROR)
            if not self._authed(query):
                return self._unauthorized()
            if path == "/api/state":
                return self._state()
            if path == "/api/stream":
                return self._stream(query)
            if path == "/api/files":
                return self._guard(lambda: self._files_list(query))
            if path == "/api/file":
                return self._guard(lambda: self._file_read(query))
            if path == "/api/file/raw":
                return self._guard(lambda: self._file_download(query))
            if path == "/api/cores":
                return self._guard(lambda: self._cores(query))
            if path == "/api/frp":
                return self._guard(self._frp_state)
            self._json({"ok": False, "message": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            path, query = self._query()
            if not self._authed(query):
                return self._unauthorized()
            # 上传走原始请求体，不能按 JSON 解析
            if path == "/api/file/upload":
                return self._guard(lambda: self._file_upload(query))
            if path == "/api/frp/upload":
                return self._guard(lambda: self._frp_upload(query))
            body = self._body()
            name = str(body.get("name") or "")
            try:
                if path == "/api/start":
                    runtime = manager.start_by_name(servers_dir, name)
                    return self._json({"ok": True, "instance": runtime.snapshot()})
                if path == "/api/stop":
                    manager.stop(name)
                    return self._json({"ok": True})
                if path == "/api/command":
                    manager.send(name, str(body.get("command") or ""))
                    return self._json({"ok": True})
            except CoreError as err:
                return self._core_error(err)
            return self._guard(lambda: self._post_rest(path, body))

        def _guard(self, fn) -> None:
            """统一接住业务异常与意外异常，保证一定回一个 JSON。"""
            try:
                fn()
            except CoreError as err:
                self._core_error(err)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as err:  # noqa: BLE001
                self._server_error(err)

        # -- 具体处理 ------------------------------------------------------

        def _state(self) -> None:
            instances = []
            for entry in list_servers(servers_dir):
                path = entry["_path"]
                name = entry.get("name") or os.path.basename(os.path.normpath(path))
                row = {
                    "name": name,
                    "kind": entry.get("kind") or "minecraft",
                    "mc_version": entry.get("mc_version", ""),
                    "jar": entry.get("jar", ""),
                    "min_mem": entry.get("min_mem", ""),
                    "max_mem": entry.get("max_mem", ""),
                    "java_path": entry.get("java_path") or "",
                    "launch": entry.get("launch") or {},
                    "process_priority": entry.get("process_priority", "normal"),
                    "extra_jvm_args": entry.get("extra_jvm_args") or [],
                    "extra_server_args": entry.get("extra_server_args") or [],
                    "openfrp": entry.get("openfrp") or {},
                    # 实例设置对话框靠它回显复选框；漏掉的话框永远是空的，
                    # 用户随手一保存就把 auto_start 悄悄关掉了
                    "auto_start": bool(entry.get("auto_start")),
                    "workdir": path,
                    "state": "stopped",
                    "exit_code": None,
                    "pid": None,
                    "started_at": None,
                    "java": "",
                    "program": "",
                    "command": [],
                }
                # 跑过的实例，运行时里存着更准的一份快照（真实 java、拼接后的命令、退出码）
                runtime = manager.state_of(name)
                if runtime:
                    row.update({key: runtime[key] for key in (
                        "state", "exit_code", "pid", "started_at", "java",
                        "program", "workdir", "min_mem", "max_mem", "command")})
                instances.append(row)
            self._json({
                "ok": True,
                "strings": {key: t(key) for key in WEB_STRINGS},
                "instances": instances,
            })

        # -- 文件管理器 ----------------------------------------------------

        def _files_list(self, query: dict[str, str]) -> None:
            root = self._instance_dir(query.get("name") or "")
            rel = query.get("path") or ""
            entries = [{
                "name": e.name,
                "path": e.path,
                "dir": e.is_dir,
                "size": e.size,
                "mtime": e.mtime,
                "editable": e.editable,
                # 界面按这个决定要不要显示「解压」按钮；真正解压时服务端
                # 仍按内容再判断一次
                "archive": (not e.is_dir) and archives.is_archive_name(e.name),
            } for e in fileops.list_dir(root, rel)]
            current = fileops.normalize_rel(rel)
            parent = ""
            if current:
                parent = current.rsplit("/", 1)[0] if "/" in current else ""
            self._json({
                "ok": True,
                "path": current,
                "parent": parent,
                "editable_max": fileops.MAX_EDIT_BYTES,
                "upload_max": MAX_UPLOAD_BYTES,
                "entries": entries,
            })

        def _file_read(self, query: dict[str, str]) -> None:
            root = self._instance_dir(query.get("name") or "")
            rel = query.get("path") or ""
            text = fileops.read_text(root, rel)
            self._json({
                "ok": True,
                "path": fileops.normalize_rel(rel),
                "text": text,
                "size": len(text.encode("utf-8")),
            })

        def _file_download(self, query: dict[str, str]) -> None:
            root = self._instance_dir(query.get("name") or "")
            rel = query.get("path") or ""
            target = fileops.resolve(root, rel)
            if not os.path.isfile(target):
                raise CoreError("web.error.no_such_file", path=rel or "/")

            size = os.path.getsize(target)
            base = os.path.basename(target)
            # 文件名可能带中文，两种形式都给：老浏览器认 filename，新的认 filename*
            ascii_name = base.encode("ascii", "replace").decode("ascii").replace('"', "_")
            disposition = (f'attachment; filename="{ascii_name}"; '
                           f"filename*=UTF-8''{urllib.parse.quote(base)}")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", disposition)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(target, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 64 * 1024)

        def _file_upload(self, query: dict[str, str]) -> None:
            root = self._instance_dir(query.get("name") or "")
            rel = query.get("path") or ""
            length = self._content_length()

            def fail(err: CoreError) -> None:
                # 请求体必须读掉，否则长连接上的下一个请求会错位
                self._drain(length)
                self._core_error(err)

            if length <= 0:
                raise CoreError("web.error.upload_empty")
            if length > MAX_UPLOAD_BYTES:
                fail(CoreError("web.error.upload_too_large",
                               max=round(MAX_UPLOAD_BYTES / 1024 / 1024)))
                return
            try:
                written = fileops.save_upload(
                    root, rel, _BodyStream(self), length, MAX_UPLOAD_BYTES,
                    # 上传文件夹时 world/level.dat 可能先于 world/ 到达
                    create_dirs=True)
            except CoreError as err:
                fail(err)
                return
            self._json({"ok": True, "size": written,
                        "path": fileops.normalize_rel(rel)})

        def _post_rest(self, path: str, body: dict) -> None:
            """do_POST 里除进程控制之外的部分。"""
            name = str(body.get("name") or "")

            if path == "/api/file/write":
                root = self._instance_dir(name)
                written = fileops.write_text(
                    root, str(body.get("path") or ""), str(body.get("content") or ""))
                return self._json({"ok": True, "size": written,
                                   "message": t("web.files.saved")})

            if path == "/api/file/mkdir":
                root = self._instance_dir(name)
                made = fileops.make_directory(root, str(body.get("path") or ""))
                return self._json({"ok": True, "path": made})

            if path == "/api/file/rename":
                root = self._instance_dir(name)
                new_rel = fileops.rename_entry(
                    root, str(body.get("path") or ""), str(body.get("new_name") or ""))
                return self._json({"ok": True, "path": new_rel})

            if path == "/api/file/delete":
                root = self._instance_dir(name)
                fileops.delete_entry(root, str(body.get("path") or ""))
                return self._json({"ok": True})

            if path == "/api/file/extract":
                root = self._instance_dir(name)
                src_rel = str(body.get("path") or "")
                dest_rel = body.get("dest")
                label = t("web.files.extracting", name=os.path.basename(src_rel))
                try:
                    result = archives.extract(
                        root, src_rel,
                        dest_rel if isinstance(dest_rel, str) else None,
                        overwrite=bool(body.get("overwrite")))
                finally:
                    finish_job(label)
                return self._json({
                    "ok": True,
                    "dest": result.dest,
                    "extracted": result.extracted,
                    "skipped": result.skipped,
                    "refused": result.refused,
                    "message": t("web.files.extract_done",
                                 count=result.extracted,
                                 dest=result.dest or "/",
                                 skipped=result.skipped),
                })

            if path == "/api/instances":
                return self._create_instance(body)

            if path == "/api/instances/settings":
                return self._update_instance(body)

            if path.startswith("/api/frp/"):
                return self._frp_action(path, body)

            if path == "/api/instances/import/scan":
                return self._import_scan(body)

            if path == "/api/instances/import/commit":
                return self._import_commit(body)

            if path == "/api/instances/import/abort":
                zip_path = os.path.abspath(
                    str(body.get("zip_path") or "").strip())
                plan = _take_plan(zip_path)
                if plan is not None:
                    abort_import(plan)
                return self._json({"ok": True})

            self._json({"ok": False, "message": "not found"}, HTTPStatus.NOT_FOUND)

        # -- 新建实例 ------------------------------------------------------

        def _cores(self, query: dict[str, str]) -> None:
            core_type = query.get("type") or ""
            if not core_type:
                return self._json({
                    "ok": True,
                    "cores": provision.list_core_types(project_dir),
                })
            versions, description = provision.list_core_versions(
                core_type, project_dir)
            self._json({
                "ok": True,
                "type": core_type,
                "description": description,
                "versions": versions[:provision.MAX_VERSION_CHOICES],
            })

        def _create_instance(self, body: dict) -> None:
            """新建实例：Minecraft 可以顺带下载核心，通用实例只写启动配置。"""
            name = str(body.get("name") or "")
            kind = str(body.get("kind") or KIND_MINECRAFT).strip().lower()
            core_type = str(body.get("core_type") or "")
            core_version = str(body.get("core_version") or "")
            mc_version = str(body.get("mc_version") or "")

            # core_version 就是核心支持的 MC 版本，用户没单独填就沿用它
            if not mc_version and core_type and core_version:
                mc_version = core_version

            server_dir = provision.create_instance(
                servers_dir, name,
                kind=kind,
                mc_version=mc_version,
                min_mem=str(body.get("min_mem") or "1G"),
                max_mem=str(body.get("max_mem") or "4G"),
                # 面板先建实例再把 jar 传上来，所以 jar 名要在这里就写进配置
                jar_name=str(body.get("jar_name") or "") or None,
                launch=body.get("launch"),
            )

            try:
                if kind == KIND_GENERIC:
                    self._require_generic_launch(body.get("launch"))
                elif core_type and core_version:
                    # 核心动辄几十 MB，进度推到面板上，别让用户干等
                    label = t("web.new.creating")
                    try:
                        jar_name = provision.install_core(
                            server_dir, core_type, core_version,
                            on_progress=progress_reporter(label))
                    finally:
                        finish_job(label)
                    provision.set_instance_core(
                        server_dir, jar_name, mc_version=mc_version or None)
            except CoreError:
                # 核心没装上、或启动配置根本没填，留下一个空壳实例只会让用户
                # 自己去删，不如回滚
                shutil.rmtree(server_dir, ignore_errors=True)
                raise

            self._json({"ok": True, "name": os.path.basename(server_dir),
                        "message": t("web.new.done",
                                     name=os.path.basename(server_dir))})

        @staticmethod
        def _require_generic_launch(launch) -> None:
            """通用实例必须有能跑的东西，否则建出来就是个哑巴实例。"""
            spec = provision.normalize_launch(launch)
            if spec["mode"] == LAUNCH_EXEC:
                if not spec["program"]:
                    raise CoreError("app.error_program_missing")
            elif not spec["command"]:
                raise CoreError("app.error_command_missing")

        def _update_instance(self, body: dict) -> None:
            """改实例设置：Minecraft 的内存/参数、通用实例的启动配置都走这里。"""
            server_dir = self._instance_dir(str(body.get("name") or ""))
            patch: dict[str, Any] = {}

            for key in ("mc_version", "jar", "min_mem", "max_mem",
                        "process_priority"):
                if key in body and body[key] is not None:
                    patch[key] = str(body[key])
            for key in ("extra_jvm_args", "extra_server_args"):
                value = body.get(key)
                if value is None:
                    continue
                patch[key] = value if isinstance(value, list) else str(value).split()
            if isinstance(body.get("launch"), dict):
                patch["launch"] = body["launch"]
            if isinstance(body.get("openfrp"), dict):
                patch["openfrp"] = body["openfrp"]
            if "auto_start" in body:
                patch["auto_start"] = bool(body["auto_start"])

            # 通用实例不能把启动配置清空，否则要等到下次启动才发现跑不起来
            entry = next((s for s in list_servers(servers_dir)
                          if s["_path"] == server_dir), None)
            if (entry is not None and "launch" in patch
                    and (entry.get("kind") or KIND_MINECRAFT) == KIND_GENERIC):
                self._require_generic_launch(patch["launch"])

            cfg = provision.update_instance(server_dir, patch)
            self._json({"ok": True, "instance": cfg,
                        "message": t("web.settings.saved")})

        # -- frp 隧道 ------------------------------------------------------

        def _frp_state(self) -> None:
            self._json({
                "ok": True,
                "status": frpops.status(project_dir),
                "settings": frpops.read_settings(project_dir),
                "proxies": frpops.list_proxies(project_dir),
                # 下载不通时的兜底：去哪下、下哪个文件。不联网，随时可给。
                "manual": frpops.manual_info(),
                "upload_max": frpops.MAX_FRPC_UPLOAD_BYTES,
            })

        def _frp_upload(self, query: dict[str, str]) -> None:
            """接收用户从 GitHub 手动下载的 frpc 包（或裸二进制）并装好。"""
            length = self._content_length()
            try:
                installed = frpops.install_from_stream(
                    project_dir, _BodyStream(self), length)
            except CoreError as err:
                self._drain(length)
                return self._core_error(err)
            self._json({"ok": True, "path": installed,
                        "status": frpops.status(project_dir),
                        "message": t("web.frp.upload_done")})

        def _frp_action(self, path: str, body: dict) -> None:
            if path == "/api/frp/settings":
                saved = frpops.write_settings(
                    project_dir,
                    server_addr=body.get("serverAddr"),
                    server_port=body.get("serverPort"),
                    token=body.get("token"),
                )
                return self._json({"ok": True, "settings": saved,
                                   "message": t("web.frp.saved")})

            if path == "/api/frp/proxy":
                frpops.add_proxy(
                    project_dir,
                    name=body.get("name"),
                    proxy_type=body.get("type"),
                    local_ip=body.get("localIP"),
                    local_port=body.get("localPort"),
                    remote_port=body.get("remotePort"),
                    custom_domains=body.get("customDomains"),
                )
                return self._json({"ok": True,
                                   "proxies": frpops.list_proxies(project_dir)})

            if path == "/api/frp/proxy/delete":
                frpops.remove_proxy(project_dir, body.get("index"))
                return self._json({"ok": True,
                                   "proxies": frpops.list_proxies(project_dir)})

            if path == "/api/frp/start":
                result = frpops.start(project_dir)
                return self._json({"ok": True, "status": frpops.status(project_dir),
                                   "already_running": result["already_running"],
                                   "message": t("web.frp.started")})

            if path == "/api/frp/stop":
                frpops.stop()
                return self._json({"ok": True, "status": frpops.status(project_dir),
                                   "message": t("web.frp.stopped_msg")})

            if path == "/api/frp/download":
                label = t("web.frp.downloading")
                try:
                    saved_path = frpops.download(
                        project_dir, on_progress=progress_reporter(label))
                finally:
                    finish_job(label)
                return self._json({"ok": True, "path": saved_path,
                                   "status": frpops.status(project_dir),
                                   "message": t("web.frp.downloaded")})

            self._json({"ok": False, "message": "not found"}, HTTPStatus.NOT_FOUND)

        def _import_scan(self, body: dict) -> None:
            zip_path = str(body.get("zip_path") or "").strip().strip('"')
            plan = provision.scan_archive(project_dir, zip_path)
            _remember_plan(plan)
            self._json({
                "ok": True,
                "zip_path": plan.zip_path,
                "candidates": list(plan.candidates),
                "auto_choice": plan.auto_choice or "",
                "suggested_name": plan.suggested_name,
            })

        def _import_commit(self, body: dict) -> None:
            zip_path = os.path.abspath(str(body.get("zip_path") or "").strip())
            plan = _take_plan(zip_path)
            if plan is None:
                # 面板重启过、或上一次解压结果已经被覆盖，就重新解压一次
                plan = provision.scan_archive(project_dir, zip_path)

            jar_rel = str(body.get("jar") or "") or (plan.auto_choice or "")
            if not jar_rel:
                abort_import(plan)
                raise CoreError("import.no_jar")

            name = str(body.get("name") or "").strip() or plan.suggested_name
            server_dir = provision.import_archive(
                plan, jar_rel, servers_dir, name=name)
            self._json({"ok": True, "name": os.path.basename(server_dir),
                        "message": t("web.new.done",
                                     name=os.path.basename(server_dir))})

        def _sse(self, event: dict) -> None:
            payload = json.dumps(_render_event(event), ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        def _stream(self, query: dict[str, str]) -> None:
            """name 为空表示订阅所有实例，前端默认就走这条：一次连接同时盯住
            全部实例的状态，也不必在切实例时重连。"""
            name = query.get("name") or ""
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            inbox: "queue.Queue[dict]" = queue.Queue()
            unsubscribe = manager.subscribe(inbox.put)
            # 下载进度这类与实例无关的事件也推给同一个客户端
            unsubscribe_jobs = JOB_BUS.subscribe(inbox.put)
            try:
                if name:
                    runtime = manager.get(name)
                    for event in (runtime.history() if runtime else []):
                        self._sse(event)
                else:
                    for events in manager.histories().values():
                        for event in events:
                            self._sse(event)
                while True:
                    try:
                        event = inbox.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    # job 事件不带 instance，别被按实例过滤掉
                    if (event.get("type") != "job" and name
                            and event.get("instance") != name):
                        continue
                    self._sse(event)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                unsubscribe()
                unsubscribe_jobs()

    return Handler


class _PanelServer(ThreadingHTTPServer):
    """面板的 HTTP 服务。

    Windows 上必须关掉 SO_REUSEADDR。它允许直接绑定到已被占用的端口，于是
    「端口被别的程序占着」不会报错，两个进程分着接同一个端口的请求——表现
    出来就是面板偶发 401 或干脆打不开，而日志里一片安静。Windows 本来就不
    依赖它绕过 TIME_WAIT，关掉之后冲突会变成一次明确的 bind 失败。
    """

    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def build_server(project_dir: str, *, host: str = "127.0.0.1", port: int = 8765,
                 token: str = "", manager: Optional[RuntimeManager] = None,
                 servers_dir: Optional[str] = None) -> ThreadingHTTPServer:
    """起一个 HTTP 服务（不进入事件循环），方便测试与嵌入。"""
    # 业务错误文案靠 locales 翻译，不加载语言的话 t() 只会把 i18n key 原样
    # 吐出来，调用方看到的就是 "web.error.xxx"。嵌入方（测试、别的入口）
    # 通常不会自己去 load_language，所以在这里补上。
    config = load_config(project_dir)
    load_language(config.get("language", "") or detect_os_language())

    servers_dir = servers_dir or ensure_servers_folder(project_dir)
    manager = manager or shared_manager(project_dir)
    handler = _make_handler(manager, servers_dir, token, project_dir)
    httpd = _PanelServer((host, port), handler)
    return httpd


# ---------------------------------------------------------------------------
# 随软件启动（后台常驻，不阻塞终端菜单）
# ---------------------------------------------------------------------------

_background_server: Optional[ThreadingHTTPServer] = None


def _stop_background() -> None:
    global _background_server
    httpd, _background_server = _background_server, None
    if httpd is not None:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:  # noqa: BLE001
            pass


def ensure_token(project_dir: str, config: dict) -> str:
    """没有 token 就生成一个并存下来，返回 token。"""
    _, _, token = web_settings(config)
    if token:
        return token
    token = secrets.token_urlsafe(24)
    web_cfg = dict(config.get("web") or {})
    web_cfg.update({"token": token})
    config["web"] = web_cfg
    save_config(config, project_dir)
    return token


def start_background(project_dir: str, host: Optional[str] = None,
                     port: Optional[int] = None) -> Optional[ThreadingHTTPServer]:
    """在后台线程里把面板拉起来，供「随软件启动」使用。

    与 run_web 的区别是不阻塞：面板跑在守护线程上，主线程该进菜单进菜单、
    该起服务器起服务器。失败返回 None（端口被占之类），调用方据此决定要
    不要提示。
    """
    global _background_server
    if _background_server is not None:
        return _background_server      # 已经在跑了，别起第二个

    config = load_config(project_dir)
    load_language(config.get("language", "") or detect_os_language())

    cfg_host, cfg_port, _ = web_settings(config)
    token = ensure_token(project_dir, config)
    bind_host = host or cfg_host
    bind_port = cfg_port if port is None else port

    try:
        httpd = build_server(project_dir, host=bind_host, port=bind_port, token=token)
    except OSError as e:
        print(t("web.cli.bind_failed", addr=f"{bind_host}:{bind_port}", msg=str(e)))
        return None

    threading.Thread(target=httpd.serve_forever, name="mstl-web", daemon=True).start()
    atexit.register(_stop_background)

    _warn_missing_assets()
    shown_host = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host
    print(t("web.cli.auto_launched",
            url=f"http://{shown_host}:{httpd.server_address[1]}/?token={token}"))
    if bind_host in ("0.0.0.0", "::"):
        print(t("web.cli.external_warning"))
    _background_server = httpd
    return httpd


def run_web(project_dir: str, host: Optional[str] = None,
            port: Optional[int] = None) -> bool:
    """进网页面板的事件循环：`python start.py web [--host H] [--port P]`。

    会一直阻塞到 Ctrl+C。返回 False 表示根本没起来（比如端口被占），
    调用方据此决定要不要报错——菜单里启动面板时要分得清这两种结果。
    """
    config = load_config(project_dir)
    load_language(config.get("language", "") or detect_os_language())

    cfg_host, cfg_port, _ = web_settings(config)
    token = ensure_token(project_dir, config)

    bind_host = host or cfg_host
    # 用 is not None 判断：--port 0 是「随便挑一个空闲端口」，是合法请求，
    # 写成 `port or cfg_port` 会把它当成没传
    bind_port = cfg_port if port is None else port

    try:
        httpd = build_server(project_dir, host=bind_host, port=bind_port, token=token)
    except OSError as e:
        # 端口被占是最常见的一种；原来会直接抛一个 traceback 出来，
        # 从菜单里启动面板时看起来像是程序崩了
        print(t("web.cli.bind_failed",
                addr=f"{bind_host}:{bind_port}", msg=str(e)))
        return False

    shown_host = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host
    _warn_missing_assets()
    print(t("web.cli.launched", url=f"http://{shown_host}:{httpd.server_address[1]}/?token={token}"))
    print(t("web.cli.token", token=token))
    print(t("web.cli.stop_hint"))
    if bind_host in ("0.0.0.0", "::"):
        print(t("web.cli.external_warning"))
    print()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
    return True


__all__ = [
    "build_server",
    "run_web",
    "start_background",
    "web_settings",
    "web_auto_start",
    "shared_manager",
    "JOB_BUS",
    "progress_reporter",
    "finish_job",
    "WEB_STRINGS",
    "COOKIE_NAME",
]
