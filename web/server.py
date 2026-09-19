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

import json
import os
import queue
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from config import load_config, save_config
from core.errors import CoreError
from core.runtime import RuntimeManager
from i18n import detect_os_language, load_language, t
from paths import bundled_dir
from server_manager import ensure_servers_folder, list_servers

COOKIE_NAME = "mstl_token"

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
)


def web_settings(config: dict) -> tuple[str, int, str]:
    """把 config.json 里的 web 段收敛成 (host, port, token)。"""
    web = config.get("web")
    if not isinstance(web, dict):
        web = {}
    host = str(web.get("host") or "127.0.0.1")
    try:
        port = int(web.get("port") or 8765)
    except (TypeError, ValueError):
        port = 8765
    token = str(web.get("token") or "")
    return host, port, token


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


def _make_handler(manager: RuntimeManager, servers_dir: str, token: str):
    """把 manager 与 token 关进闭包，产出一个请求处理器类。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "MSTL"
        protocol_version = "HTTP/1.1"

        # -- 基础设施 ------------------------------------------------------

        def log_message(self, fmt: str, *args: Any) -> None:
            """默认会把每条请求打到 stderr，面板不需要这种噪音。"""

        def _query(self) -> dict[str, str]:
            parsed = urlparse(self.path)
            return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}

        def _authed(self, query: dict[str, str]) -> bool:
            if not token:
                return True
            if self.headers.get("X-MSTL-Token") == token:
                return True
            cookie = self.headers.get("Cookie") or ""
            if f"{COOKIE_NAME}={token}" in cookie:
                return True
            return query.get("token") == token

        def _send(self, body: bytes, status: int, content_type: str,
                  extra_headers: Optional[list[tuple[str, str]]] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
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
                "<body style='font:14px system-ui;background:#11151c;color:#e6ebf2;padding:24px'>"
                f"<p>{t('web.auth.invalid')}</p><p style='color:#8b98a9'>{t('web.auth.help')}</p>",
                HTTPStatus.UNAUTHORIZED,
            )

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def _core_error(self, err: CoreError) -> None:
            self._json({"ok": False, "key": err.key,
                        "message": t(err.key, **err.kwargs)},
                       HTTPStatus.BAD_REQUEST)

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
            if path == "/api/state":
                if not self._authed(query):
                    return self._unauthorized()
                return self._state()
            if path == "/api/stream":
                if not self._authed(query):
                    return self._unauthorized()
                return self._stream(query)
            self._json({"ok": False, "message": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            path, query = self._query()
            if not self._authed(query):
                return self._unauthorized()
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
            self._json({"ok": False, "message": "not found"}, HTTPStatus.NOT_FOUND)

        # -- 具体处理 ------------------------------------------------------

        def _state(self) -> None:
            instances = []
            for entry in list_servers(servers_dir):
                path = entry["_path"]
                name = entry.get("name") or os.path.basename(os.path.normpath(path))
                row = {
                    "name": name,
                    "mc_version": entry.get("mc_version", ""),
                    "jar": entry.get("jar", ""),
                    "state": "stopped",
                    "exit_code": None,
                    "pid": None,
                }
                runtime = manager.state_of(name)
                if runtime:
                    row.update({k: runtime[k] for k in ("state", "exit_code", "pid")})
                instances.append(row)
            self._json({
                "ok": True,
                "strings": {key: t(key) for key in WEB_STRINGS},
                "instances": instances,
            })

        def _sse(self, event: dict) -> None:
            payload = json.dumps(_render_event(event), ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        def _stream(self, query: dict[str, str]) -> None:
            name = query.get("name") or ""
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            inbox: "queue.Queue[dict]" = queue.Queue()
            unsubscribe = manager.subscribe(inbox.put)
            try:
                runtime = manager.get(name)
                for event in (runtime.history() if runtime else []):
                    self._sse(event)
                while True:
                    try:
                        event = inbox.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if event.get("instance") != name:
                        continue
                    self._sse(event)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                unsubscribe()

    return Handler


def build_server(project_dir: str, *, host: str = "127.0.0.1", port: int = 8765,
                 token: str = "", manager: Optional[RuntimeManager] = None,
                 servers_dir: Optional[str] = None) -> ThreadingHTTPServer:
    """起一个 HTTP 服务（不进入事件循环），方便测试与嵌入。"""
    servers_dir = servers_dir or ensure_servers_folder(project_dir)
    manager = manager or RuntimeManager(project_dir)
    handler = _make_handler(manager, servers_dir, token)
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    return httpd


def run_web(project_dir: str, host: Optional[str] = None,
            port: Optional[int] = None) -> None:
    """命令行入口：`python start.py web [--host H] [--port P]`。"""
    config = load_config(project_dir)
    load_language(config.get("language", "") or detect_os_language())

    cfg_host, cfg_port, token = web_settings(config)
    if not token:
        token = secrets.token_urlsafe(24)
        config["web"] = {"host": cfg_host, "port": cfg_port, "token": token}
        save_config(config, project_dir)

    bind_host = host or cfg_host
    bind_port = port or cfg_port
    httpd = build_server(project_dir, host=bind_host, port=bind_port, token=token)
    shown_host = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host
    print(t("web.cli.launched", url=f"http://{shown_host}:{httpd.server_address[1]}/?token={token}"))
    print(t("web.cli.token", token=token))
    if bind_host in ("0.0.0.0", "::"):
        print("  [警告] 面板正在监听所有网卡，请确认前面有反代与 HTTPS。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()


__all__ = ["build_server", "run_web", "web_settings", "WEB_STRINGS", "COOKIE_NAME"]
