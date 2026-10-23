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
import ipaddress
import json
import os
import queue
import secrets
import shutil
import tempfile
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
from core import instances as instlib
from core import provision
from core.errors import CoreError
from core.runtime import RuntimeManager, accept_eula, eula_accepted
from core.transfer import abort_import
from core import transfer as transferops
from i18n import available_languages, detect_os_language, load_language, t
from paths import bundled_dir
from web.ai import AiAssistant
from server_manager import (
    EXPORT_CATEGORIES,
    KIND_GENERIC,
    KIND_MINECRAFT,
    LAUNCH_EXEC,
    classify_server_type,
    ensure_servers_folder,
    list_servers,
    load_server_config,
)

COOKIE_NAME = "mstl_token"

# 通过面板上传单个文件的大小上限（服务端 jar 通常 40~80 MB）
MAX_UPLOAD_BYTES = 512 * 1024 * 1024

# JSON 接口请求体上限。
#
# _body() 以前直接按 Content-Length 读进内存，没有上限：一个 Content-Length:
# 9999999999 的请求就足以让面板吃掉几个 G 内存（而且长度是客户端说了算的，
# 读不到那么多字节还会一直阻塞在那条连接上）。JSON 接口里最大的一份是
# /api/file/write 的正文，受 fileops.MAX_EDIT_BYTES（2 MiB）约束，再加上
# 转义与其余字段，8 MiB 已经很宽裕。
MAX_JSON_BODY_BYTES = 8 * 1024 * 1024

# 单个连接的空闲/单次收发超时（秒）。没有它的时候，一个连上来就不说话的
# socket 能让处理线程永远停在 readline 上：daemon_threads 只是让进程能退，
# 活着的线程和它的 socket 一样占着资源。SSE 那条流每 15 秒发一次心跳，
# 所以这里只要大于 15 就不会误杀正常的长连接。
SOCKET_TIMEOUT = 30

# 回 401 / 报错前愿意替客户端读掉的请求体上限。排空本来是为了保住长连接，
# 但声明多大的 Content-Length 完全由客户端说了算：一个匿名请求写着
# Content-Length: 1000000000000 就能让我们一直堵在读上面。超过这个数就直接
# 关连接——关掉比错位安全，而且这么大的请求体本来也不该被当成下一个请求。
MAX_DRAIN_BYTES = 1024 * 1024

# 下载响应头里的文件名要塞进 Content-Disposition，而文件名是用户可控的：
# Linux 上 \r 和 \n 都是合法文件名字符，直接拼进响应头就是一次头注入。
# 顺带把引号和反斜杠也换掉，免得把 filename="..." 提前闭合。
_UNSAFE_HEADER_CHARS = {code: "_" for code in range(0x20)}
_UNSAFE_HEADER_CHARS[0x7F] = "_"
_UNSAFE_HEADER_CHARS[ord('"')] = "_"
_UNSAFE_HEADER_CHARS[ord("\\")] = "_"

# 实例能装什么，看的是服务端类型：插件的只有 plugins/，模组的只有 mods/，
# 混合端（比如 Arclight）两边都有。其余的端两个目录都不该出现。
def _jar_kind_of(server_cfg: dict, server_dir: str) -> str:
    stype = classify_server_type(server_cfg, server_dir)
    if stype == "plugin":
        return "plugins"
    if stype == "mod":
        return "mods"
    if stype == "hybrid":
        return "both"
    return ""


def _jar_dir(server_dir: str, kind: str) -> str:
    return (instlib.mods_dir(server_dir) if kind == "mods"
            else instlib.plugins_dir(server_dir))


def _safe_jar_name(filename: str) -> str:
    """只接受纯文件名。这个值会拼进目录路径，带分隔符就是一次目录穿越。"""
    name = str(filename or "")
    if not name or os.path.basename(name) != name or name in (".", ".."):
        raise CoreError("web.error.no_such_file", path=name)
    if "\\" in name or "/" in name or ":" in name:
        raise CoreError("web.error.no_such_file", path=name)
    return name


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
    # 401 时要给用户排查方向，前端那份也一并下发（见 index.html 里的 401 分支）
    "web.auth.hint_cookie",
    "web.auth.hint_host",
    "web.auth.hint_regenerate",
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
    # 内容（插件 / 模组）
    "web.jars.tab",
    "web.jars.plugins",
    "web.jars.mods",
    "web.jars.empty",
    "web.jars.search",
    "web.jars.search_hint",
    "web.jars.searching",
    "web.jars.no_results",
    "web.jars.download",
    "web.jars.upload",
    "web.jars.enable",
    "web.jars.disable",
    "web.jars.confirm_delete",
    "web.jars.uploaded",
    # 导出
    "web.export.title",
    "web.export.hint",
    "web.export.start",
    "web.export.none",
    # EULA
    "web.eula.title",
    "web.eula.body",
    "web.eula.agree",
    "web.eula.accepted",
    # 全局设置
    "web.panel.settings",
    "web.settings.language",
    "web.settings.language_auto",
    "web.settings.threads",
    "web.settings.threads_hint",
    "web.settings.auto_start_server",
    "web.settings.auto_start_none",
    "web.settings.startup",
    "web.settings.startup_hint",
    "web.settings.update_check",
    "web.settings.update_running",
    "web.settings.update_new",
    "web.settings.update_latest",
    "web.settings.update_unknown",
    "web.settings.saved",
    # 新建实例
    "web.new.title",
    # 新建实例时的前端校验文案：后端本来就有这两句翻译，但没下发到前端，
    # 于是中文用户看到的是英文兜底 "name required" 和一句空提示
    "web.error.name_required",
    "web.error.core_selection_required",
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
    "web.settings.jar",
    "web.settings.jvm_args",
    "web.settings.server_args",
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
    "web.settings.auto_restart",
    "web.settings.auto_restart_hint",
    "web.settings.restart_max",
    "web.settings.restart_max_hint",
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
    # AI 助手（前端标签页 + 设置面板里的 AI 区块）
    "web.settings.ai",
    "web.settings.ai_enabled",
    "web.settings.ai_base_url",
    "web.settings.ai_base_url_hint",
    "web.settings.ai_model",
    "web.settings.ai_model_hint",
    "web.settings.ai_key",
    "web.settings.ai_key_hint",
    "web.settings.ai_key_set",
    "web.settings.ai_key_env",
    "web.settings.ai_saved",
    "web.ai.tab",
    "web.ai.placeholder",
    "web.ai.send",
    "web.ai.thinking",
    "web.ai.empty",
    "web.ai.need_config",
    "web.ai.confirm",
    "web.ai.cancel",
    "web.ai.pending_title",
    "web.ai.reset",
    "web.ai.you",
    "web.ai.assistant",
    "web.ai.hint_disabled",
    "web.ai.tool_read",
    "web.ai.expires",
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


def start_job(label: str) -> None:
    """起一个不知道总量的长任务：进度条显示成来回扫的那条，而不是卡在 0%。"""
    JOB_BUS.publish({"type": "job", "label": label, "ratio": 0.0,
                     "indeterminate": True, "done": False})


class _JobReporter:
    """把 core 的 Reporter 协议接到面板的进度条上。

    core 的 export_instance / 下载函数只认「有 log/progress/warn 三个方法的
    对象」，而 JOB_BUS 这边是一条事件流。这层就是两边之间的适配器。
    """

    def __init__(self, label: str) -> None:
        self.label = label

    def log(self, key: str, **kwargs) -> None:
        try:
            JOB_BUS.publish({"type": "job", "label": self.label, "ratio": 0.0,
                             "indeterminate": True, "done": False,
                             "message": t(key, **kwargs)})
        except Exception:  # noqa: BLE001
            pass

    def progress(self, *, label: str = "", current=None, total=None,
                 ratio=None, done: bool = False) -> None:
        if ratio is None:
            ratio = (float(current) / float(total)
                     if current is not None and total else 0.0)
        try:
            JOB_BUS.publish({"type": "job", "label": label or self.label,
                             "ratio": max(0.0, min(1.0, float(ratio))),
                             "done": bool(done)})
        except Exception:  # noqa: BLE001
            pass

    def warn(self, key: str, **kwargs) -> None:
        return None


def _job_reporter(label: str) -> _JobReporter:
    return _JobReporter(label)


def _render_event(event: dict) -> dict:
    """把带 i18n key 的事件翻译成人能看的文本。"""
    if event.get("type") == "line" and event.get("kind") == "note":
        out = dict(event)
        out["text"] = t(event.get("key", ""), **(event.get("kwargs") or {}))
        return out
    return event


_INDEX_CACHE: dict[str, Any] = {}
_INDEX_CACHE_LOCK = threading.Lock()


def _index_html() -> str:
    """读 webui/index.html。

    每次请求都重读一遍 78 KB 是白费力气（面板刷得很勤），但也不能一直缓存
    不放——开发时改了页面、刷新一下就该看到新内容。所以按 (mtime, size)
    记住上一次的结果：文件没动就直接复用，动了就重读。
    """
    path = os.path.join(bundled_dir(), "webui", "index.html")
    stat = os.stat(path)
    key = (path, stat.st_mtime_ns, stat.st_size)
    with _INDEX_CACHE_LOCK:
        if _INDEX_CACHE.get("key") == key:
            return _INDEX_CACHE["html"]
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    with _INDEX_CACHE_LOCK:
        _INDEX_CACHE["key"] = key
        _INDEX_CACHE["html"] = html
    return html


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
# 每份计划都有自己的临时目录（plan_import 用 mkdtemp），所以旧计划不会挡住
# 新计划；但旧计划的目录得有人来清，否则连点几次「扫描」就在项目根下堆一堆
# .import_temp-*。这里只保留最新的一份，换掉时顺手把上一份 abort 掉。
_IMPORT_PLANS: dict[str, Any] = {}
_IMPORT_PLANS_LOCK = threading.Lock()


def _remember_plan(plan) -> None:
    with _IMPORT_PLANS_LOCK:
        stale = list(_IMPORT_PLANS.values())
        _IMPORT_PLANS.clear()
        _IMPORT_PLANS[plan.zip_path] = plan
    for old in stale:
        abort_import(old)


def _take_plan(zip_path: str):
    with _IMPORT_PLANS_LOCK:
        return _IMPORT_PLANS.pop(zip_path, None)


def _is_loopback_address(address: str) -> bool:
    """来源地址是不是本机回环（127.0.0.0/8 或 ::1）。

    只在「没配 token」时用得上：那时只有本机才配享受免鉴权。解析不出来的
    地址一律当外部来源处理——宁可多要求一次 token。
    """
    text = str(address or "").strip().strip("[]")
    if text.startswith("::ffff:"):        # 双栈监听下 IPv4 会带这个前缀
        text = text[len("::ffff:"):]
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _make_handler(manager: RuntimeManager, servers_dir: str, token: str,
                  project_dir: str, token_provider: Any = None):
    """把 manager、token 与目录关进闭包，产出一个请求处理器类。

    token 是启动时那份（兜底用）；token_provider 每次请求现读配置，
    让「重新生成 token」不必重启面板就能生效。
    """

    # 一个面板进程一个助手：会话、待确认请求都挂在它身上。放进闭包而不是
    # 模块级全局，测试里每个 build_server 才能拿到互不干扰的一份。
    assistant = AiAssistant(project_dir, servers_dir, manager)

    class Handler(BaseHTTPRequestHandler):
        server_version = "MSTL"
        protocol_version = "HTTP/1.1"
        # socketserver 会在 setup() 里拿这个值给连接 setblocking/settimeout，
        # 所以它同时管住「连上来不发请求」和「发到一半不动了」两种情况。
        timeout = SOCKET_TIMEOUT
        _body_read = 0

        def handle_one_request(self) -> None:
            """一个 TCP 连接上会连着处理多个请求，_body_read 必须按请求归零。

            它记的是「这个请求的请求体已经读走了多少」，_drain() 拿它算还剩
            多少没读。以前只在第一个请求里是 0：第二个请求时它还是上一个请求
            的累计值，于是 _drain 要么少读（残留字节把下一个请求顶歪，整条
            连接错位），要么多读（阻塞在下一个请求上，长连接直接卡死）。
            """
            self._body_read = 0
            super().handle_one_request()

        # -- 基础设施 ------------------------------------------------------

        def log_message(self, fmt: str, *args: Any) -> None:
            """默认会把每条请求打到 stderr，面板不需要这种噪音。"""

        def _query(self) -> dict[str, str]:
            parsed = urlparse(self.path)
            return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}

        def _presented_tokens(self, query: dict[str, str]) -> list[str]:
            """客户端出示的**全部** token：请求头、Cookie、query 三条都收。

            以前是「取第一条找到的就返回」，于是浏览器里留着一个旧 token 的
            cookie 时，地址栏里那个正确的新 token 会被直接无视掉——用户拿着
            对 token 也永远进不来，只能自己去清 cookie。三条并列，任何一条
            对就该放行。
            """
            values: list[str] = []
            header = self.headers.get("X-MSTL-Token")
            if header:
                values.append(header)
            raw_cookie = self.headers.get("Cookie") or ""
            if raw_cookie:
                jar: SimpleCookie = SimpleCookie()
                try:
                    jar.load(raw_cookie)
                except CookieError:
                    jar = SimpleCookie()
                morsel = jar.get(COOKIE_NAME)
                if morsel is not None and morsel.value:
                    values.append(morsel.value)
            query_token = query.get("token")
            if query_token:
                values.append(query_token)
            return values

        def _expected_token(self) -> str:
            """此刻该认的 token。

            现从配置里读，而不是认处理器创建时闭包进来的那个：菜单里「重新
            生成 token」之后，正在跑的面板应该立刻认新 token，不用重启。
            读不到（config.json 被删、写坏、web 段没了）就退回启动时那一个——
            宁可继续要求旧 token，也不能因为读不到配置就把鉴权整个关掉。
            """
            if token_provider is not None:
                try:
                    current = str(token_provider() or "")
                except Exception:  # noqa: BLE001
                    current = ""
                if current:
                    return current
            return token

        def _authed(self, query: dict[str, str]) -> bool:
            expected = self._expected_token()
            if not expected:
                # 「没配 token 就不校验」是既定行为：面板默认只监听本机，
                # start_background / run_web 也会先补一个 token 再启动。
                # 但它不能顺带变成「谁连上都算本机」——一旦 --host 0.0.0.0
                # 暴露出去而 token 又是空的，整块面板就是裸的。这种情况下
                # 只认回环来源。
                return _is_loopback_address(self.client_address[0])
            # 定长比较：用 == 逐字符短路会通过响应时间泄露 token 前缀。
            # 另外别再拿 "mstl_token=xxx" 去 in 整个 Cookie 头做子串匹配，
            # 别的 cookie 的值里带上这一串也会被当成通过。
            return any(hmac.compare_digest(value, expected)
                       for value in self._presented_tokens(query) if value)

        def _send(self, body: bytes, status: int, content_type: str,
                  extra_headers: Optional[list[tuple[str, str]]] = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            # 面板前后端都在本机迭代，别让浏览器拿旧页面
            self.send_header("Cache-Control", "no-store")
            # 首次进面板时 token 在地址栏里（?token=...），带外链的页面上
            # 一旦发生跳转就会顺着 Referer 把它送出去。这里直接禁掉。
            self.send_header("Referrer-Policy", "no-referrer")
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
            # 最常见的两种情况其实是「浏览器里还留着旧 token 的 cookie」和
            # 「用 localhost 开的、cookie 却存在 127.0.0.1 名下」，光说一句
            # 「token 无效」用户只能干瞪眼，把排查方向一并写出来。
            hints = "".join(
                f"<li>{t(key)}</li>"
                for key in ("web.auth.hint_cookie", "web.auth.hint_host",
                            "web.auth.hint_regenerate")
            )
            self._page(
                "<!doctype html><meta charset='utf-8'>"
                "<body style='font:14px system-ui;color:#1f2430;padding:24px;"
                "background-color:#ffffff;"
                "background-image:linear-gradient(180deg,#f7f8ff,#ffffff 260px);"
                "background-repeat:no-repeat'>"
                f"<p>{t('web.auth.invalid')}</p><p style='color:#6b7480'>{t('web.auth.help')}</p>"
                f"<ul style='color:#6b7480;line-height:1.9'>{hints}</ul>",
                HTTPStatus.UNAUTHORIZED,
            )

        def _reject_unauthorized(self) -> None:
            """回 401 之前先把请求体读掉。

            HTTP/1.1 上是长连接：请求体没读完就回响应，下一个请求会从残留
            的字节开始解析。而 401 恰恰是最容易被反复触发的一条路径（换了
            token、cookie 还是旧的、用 localhost 开的而 cookie 存在 127.0.0.1
            名下），不排空的话每被拒一次那条连接就错位一次。
            """
            self._drain(self._content_length())
            self._unauthorized()

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return {}
            if length <= 0:
                return {}
            if length > MAX_JSON_BODY_BYTES:
                # 同样要先把请求体读掉再报错，否则长连接上的下一个请求会
                # 从这一大坨请求体中间开始解析。
                self._drain(length)
                raise CoreError("web.error.body_too_large",
                                max=round(MAX_JSON_BODY_BYTES / 1024 / 1024))
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
            if remaining > MAX_DRAIN_BYTES:
                # 声明了一个自己也不打算发的巨大请求体，是这类排空逻辑最容易被
                # 反用的一点。不读、直接关连接：已经读走的字节不会被错当成
                # 下一个请求，而这条连接上也不会再有「下一个请求」了。
                self.close_connection = True
                return
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
            """按实例名解析实例目录。只认 servers/ 下真实存在的实例。

            实例名通常就等于目录名（create_instance 就是拿实例名当目录名，
            而且 name 不在 _PATCH_FIELDS 里，建好之后改不了）。所以先按目录
            名直接试一次，命中就不必再扫整个 servers/ ——文件管理器的每个
            操作都要走这里，实例一多，那次全量扫描就是白花的钱。目录名和
            配置里的 name 对不上（用户手工改过目录或 .server.json）时会走
            下面的全量查找，结果一样。
            """
            wanted = str(name or "")
            if not wanted:
                raise CoreError("web.error.unknown_instance", name=wanted)
            if wanted not in (".", "..") and not any(
                    sep in wanted for sep in ("/", "\\")):
                direct = os.path.join(servers_dir, wanted)
                if os.path.isfile(os.path.join(direct, ".server.json")):
                    try:
                        cfg = load_server_config(direct)
                    except OSError:
                        cfg = {}
                    if str(cfg.get("name") or wanted) == wanted:
                        return direct
            for entry in list_servers(servers_dir):
                entry_name = entry.get("name") or os.path.basename(
                    os.path.normpath(entry["_path"]))
                if entry_name == wanted:
                    return entry["_path"]
            raise CoreError("web.error.unknown_instance", name=wanted)

        def _maybe_start_tunnel(self, servers_dir: str, name: str) -> None:
            """实例配了「随实例自动开启隧道」就在后台把 frpc 拉起来。

            面板设置里那个勾（文案是「启动实例时自动开启隧道」）以前只有
            终端前台启动会认，从面板点「启动」不开隧道，页面上也不会说
            任何话——服务器起来了，外面却连不进来。

            放后台线程是因为 launch() 里要等 2 秒确认进程没立刻退出，
            必要时还得去下载 frpc；这些都不该堵在这个 HTTP 请求里。
            """
            def run() -> None:
                try:
                    entry = next(
                        (s for s in list_servers(servers_dir)
                         if (s.get("name") or os.path.basename(
                             os.path.normpath(s["_path"]))) == name), None)
                    if entry is None:
                        return
                    from openfrp.tunnel_manager import maybe_auto_start_for_instance
                    maybe_auto_start_for_instance(project_dir, entry)
                except Exception as e:  # noqa: BLE001
                    # 隧道起不来不该影响实例本身，但也不能一声不吭
                    print(f"  [警告] 自动开启隧道失败: {e}")

            threading.Thread(target=run, name=f"mstl-tunnel-{name}",
                             daemon=True).start()

        # -- 路由 ----------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 的约定)
            path, query = self._query()
            if path in ("/", "/index.html"):
                if not self._authed(query):
                    return self._reject_unauthorized()
                headers = []
                expected = self._expected_token()
                if query.get("token") and expected:
                    # 用「此刻生效的」token 回写 cookie：地址栏带对了 token 时
                    # 顺手把浏览器的旧 cookie 覆盖掉，下次不带 token 也能进。
                    headers.append(("Set-Cookie",
                                    f"{COOKIE_NAME}={expected}; Path=/; HttpOnly; SameSite=Lax"))
                try:
                    return self._page(_index_html(), extra_headers=headers)
                except OSError:
                    return self._page("webui/index.html missing", HTTPStatus.INTERNAL_SERVER_ERROR)
            if not self._authed(query):
                return self._reject_unauthorized()
            if path == "/api/state":
                return self._state()
            if path == "/api/stream":
                return self._guard(lambda: self._stream(query))
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
            if path == "/api/ai":
                return self._guard(lambda: self._json(assistant.state()))
            if path == "/api/jars":
                return self._guard(lambda: self._jar_list(query))
            if path == "/api/jars/search":
                return self._guard(lambda: self._jar_search(query))
            if path == "/api/export":
                # 带 name 就是「打包给我」，不带就是「先告诉我有哪些分类」
                if query.get("name"):
                    return self._guard(lambda: self._export_download(query))
                return self._guard(self._export_categories)
            if path == "/api/settings":
                return self._guard(lambda: self._json(self._settings_view()))
            if path == "/api/update":
                return self._guard(self._update_check)
            self._json({"ok": False, "message": "not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            path, query = self._query()
            if not self._authed(query):
                return self._reject_unauthorized()
            # 上传走原始请求体，不能按 JSON 解析
            if path == "/api/file/upload":
                return self._guard(lambda: self._file_upload(query))
            if path == "/api/frp/upload":
                return self._guard(lambda: self._frp_upload(query))
            if path == "/api/jars/upload":
                return self._guard(lambda: self._jar_upload(query))
            try:
                body = self._body()
            except CoreError as err:
                return self._core_error(err)
            name = str(body.get("name") or "")
            try:
                if path == "/api/start":
                    runtime = manager.start_by_name(servers_dir, name)
                    self._maybe_start_tunnel(servers_dir, name)
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
                    # 面板靠它决定要不要显示「内容」标签页，以及往哪个目录放 jar。
                    # "plugins" / "mods" / "both" / ""（这个端没有内容目录）
                    "jar_kind": _jar_kind_of(entry, path),
                    # 通用实例没有 EULA 这回事，别让它弹那条框
                    "eula": (True if (entry.get("kind") or KIND_MINECRAFT)
                             == KIND_GENERIC else eula_accepted(path)),
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
                    # min_mem / max_mem 故意不在这里覆盖：它们是用户在设置里
                    # 填的值，而运行时那份是**启动那一刻**从启动计划里抄下来
                    # 的。一旦实例被启动过一次，快照就会一直压着磁盘上的新值
                    # （通用实例的计划里干脆是 1G/4G 默认值），于是设置对话框
                    # 回显旧内存、用户随手一保存就把自己刚改的数值改了回去。
                    row.update({key: runtime[key] for key in (
                        "state", "exit_code", "pid", "started_at", "java",
                        "program", "workdir", "command")})
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
            #
            # 但 filename="..." 这一份是原样拼进响应头的，而文件名在 Linux 上
            # 可以含 \r 和 \n —— 直接拼就是一次响应头注入。先把控制字符（以及
            # 会提前闭合引号的那两个）换成下划线。filename* 那份走
            # urllib.parse.quote，控制字符本来就会变成 %0D/%0A，不必额外处理。
            ascii_name = base.encode("ascii", "replace").decode("ascii")
            ascii_name = ascii_name.translate(_UNSAFE_HEADER_CHARS)
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

        # -- EULA ----------------------------------------------------------

        def _accept_eula(self, body: dict) -> None:
            """用户在前端点了「同意」。写盘之后再启动一次，由前端发起。"""
            root = self._instance_dir(str(body.get("name") or ""))
            accept_eula(root)
            self._json({"ok": True, "message": t("web.eula.accepted")})

        # -- 导出 ----------------------------------------------------------

        def _export_categories(self) -> None:
            self._json({
                "ok": True,
                "categories": [
                    {"key": key, "label": t(label), "default": bool(default)}
                    for key, label, _patterns, default in EXPORT_CATEGORIES
                ],
            })

        def _export_download(self, query: dict) -> None:
            """在临时目录打包，然后把字节流给浏览器。

            不在实例目录里落地：导出的 zip 动辄上 GB，而实例目录本身可能就
            在系统盘上；临时文件用完即删，浏览器那边就是一次普通下载。
            """
            name = str(query.get("name") or "")
            root = self._instance_dir(name)
            chosen = {c.strip() for c in str(query.get("cats") or "").split(",")
                      if c.strip()}
            chosen &= {key for key, _l, _p, _d in EXPORT_CATEGORIES}
            if not chosen:
                raise CoreError("web.export.none")

            label = t("web.export.packing", name=name)
            fd, tmp = tempfile.mkstemp(prefix="mstl-export-", suffix=".zip")
            os.close(fd)
            try:
                try:
                    transferops.export_instance(
                        root, tmp, chosen,
                        reporter=_job_reporter(label))
                finally:
                    finish_job(label)
                self._send_file(tmp, f"{name or 'server'}-export.zip")
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

        # -- 插件 / 模组 ---------------------------------------------------

        def _jar_target(self, source: dict) -> tuple[str, str]:
            """解析出实例目录与目录种类（plugins / mods）。"""
            kind = str(source.get("kind") or "plugins")
            if kind not in ("plugins", "mods"):
                raise CoreError("web.error.bad_kind")
            return self._instance_dir(str(source.get("name") or "")), kind

        def _jar_list(self, query: dict) -> None:
            root, kind = self._jar_target(query)
            directory = _jar_dir(root, kind)
            jar_set = instlib.list_jars(directory)
            rows = []
            for filename, enabled in ([(f, True) for f in jar_set.enabled]
                                      + [(f, False) for f in jar_set.disabled]):
                try:
                    stat = os.stat(os.path.join(directory, filename))
                except OSError:
                    continue
                rows.append({
                    "file": filename,
                    "enabled": enabled,
                    "size": stat.st_size,
                    "mtime": stat.st_mtime,
                })
            rows.sort(key=lambda r: (-r["mtime"], r["file"].lower()))
            self._json({"ok": True, "kind": kind, "entries": rows})

        def _jar_toggle(self, body: dict) -> None:
            root, kind = self._jar_target(body)
            filename = _safe_jar_name(body.get("file"))
            directory = _jar_dir(root, kind)
            # 当前是 .jar.disabled 就说明它现在是禁用的，操作是把启用回来
            enable = filename.lower().endswith(".jar.disabled")
            new_name = instlib.set_jar_enabled(directory, filename, enable)
            self._json({"ok": True, "file": new_name,
                        "message": t("web.jars.toggled", name=new_name)})

        def _jar_delete(self, body: dict) -> None:
            root, kind = self._jar_target(body)
            filename = _safe_jar_name(body.get("file"))
            instlib.delete_jar(_jar_dir(root, kind), filename)
            self._json({"ok": True,
                        "message": t("web.jars.deleted", name=filename)})

        def _jar_upload(self, query: dict) -> None:
            root, kind = self._jar_target(query)
            filename = _safe_jar_name(query.get("file"))
            if not filename.lower().endswith(".jar"):
                raise CoreError("web.jars.not_jar")
            length = self._content_length()
            if length <= 0:
                raise CoreError("web.error.upload_empty")
            if length > MAX_UPLOAD_BYTES:
                self._drain(length)
                raise CoreError("web.error.upload_too_large",
                                max=round(MAX_UPLOAD_BYTES / 1024 / 1024))
            written = fileops.save_upload(
                root, f"{kind}/{filename}", _BodyStream(self), length,
                MAX_UPLOAD_BYTES, create_dirs=True)
            self._json({"ok": True, "file": filename, "size": written,
                        "message": t("web.jars.uploaded", name=filename)})

        def _jar_search(self, query: dict) -> None:
            root, kind = self._jar_target(query)
            text = str(query.get("q") or "").strip()
            if not text:
                raise CoreError("web.error.empty_query")
            results = []
            if kind == "plugins":
                from plugin_sources import search_all as search_plugins
                found = search_plugins(text, limit=6)
            else:
                from mod_sources import extract_mod_loader, search_all_mods
                cfg = load_server_config(root)
                found = search_all_mods(
                    text,
                    str(cfg.get("mc_version") or ""),
                    extract_mod_loader(str(cfg.get("jar") or "")),
                    limit=8)
            for source, items in found.items():
                for item in items:
                    row = dict(item)
                    row["source"] = source
                    results.append(row)
            self._json({"ok": True, "kind": kind, "results": results[:40]})

        def _jar_download(self, body: dict) -> None:
            root, kind = self._jar_target(body)
            result = body.get("result")
            if not isinstance(result, dict):
                raise CoreError("web.jars.bad_result")
            source = str(result.get("source") or "")
            allowed = (("SpigotMC", "Modrinth", "Hangar") if kind == "plugins"
                       else ("Modrinth", "CurseForge"))
            if source not in allowed:
                raise CoreError("web.jars.bad_result")
            directory = _jar_dir(root, kind)
            os.makedirs(directory, exist_ok=True)
            label = t("web.jars.downloading", name=str(result.get("name") or ""))
            start_job(label)
            try:
                if kind == "plugins":
                    from plugin_sources import download_plugin
                    filename = download_plugin(result, directory)
                else:
                    from mod_sources import download_mod, extract_mod_loader
                    cfg = load_server_config(root)
                    filename = download_mod(
                        result, directory,
                        str(cfg.get("mc_version") or ""),
                        extract_mod_loader(str(cfg.get("jar") or "")))
            finally:
                finish_job(label)
            if not filename:
                raise CoreError("web.jars.download_failed",
                                name=str(result.get("name") or ""))
            self._json({"ok": True, "file": filename,
                        "message": t("web.jars.installed", name=filename)})

        # -- 全局设置 ---------------------------------------------------

        def _settings_view(self) -> dict:
            config = load_config(project_dir)
            from startup import is_startup_enabled, is_supported
            startup_state = False
            try:
                startup_state = is_startup_enabled()
            except Exception:  # noqa: BLE001 注册表读不出来不该让设置页打不开
                startup_state = False
            return {
                "ok": True,
                "language": str(config.get("language") or "auto") or "auto",
                "download_threads": int(config.get("download_threads") or 16),
                "auto_start_server": str(config.get("auto_start_server") or ""),
                "languages": [{"code": code, "name": name}
                              for code, name in available_languages()],
                "startup": {"enabled": startup_state,
                            "supported": is_supported()},
            }

        def _settings_save(self, body: dict) -> None:
            config = load_config(project_dir)
            changed_language = False

            if "language" in body:
                lang = str(body.get("language") or "").strip()
                if lang in ("", "auto"):
                    config["language"] = ""
                else:
                    codes = {code for code, _n in available_languages()}
                    if lang not in codes:
                        raise CoreError("web.settings.bad_language")
                    config["language"] = lang
                changed_language = True

            if "download_threads" in body:
                try:
                    threads = int(body.get("download_threads"))
                except (TypeError, ValueError):
                    raise CoreError("web.settings.bad_threads") from None
                from download_msl import MAX_DOWNLOAD_THREADS, set_download_threads
                threads = max(1, min(threads, MAX_DOWNLOAD_THREADS))
                config["download_threads"] = threads
                set_download_threads(threads)

            if "auto_start_server" in body:
                target = str(body.get("auto_start_server") or "")
                if target:
                    self._instance_dir(target)   # 不存在的实例名直接报错
                config["auto_start_server"] = target

            save_config(config, project_dir)

            if "startup" in body:
                from startup import is_supported, set_startup_enabled
                if not is_supported():
                    raise CoreError("web.settings.startup_unsupported")
                if not set_startup_enabled(bool(body.get("startup"))):
                    raise CoreError("web.settings.startup_failed")

            # 语言当场生效：菜单和面板读的是同一份 config，晚一步就会出
            # 现「设置里写着 English、界面还是中文」
            if changed_language:
                load_language(config.get("language") or detect_os_language())

            self._json(dict(self._settings_view(),
                            message=t("web.settings.saved")))

        def _update_check(self) -> None:
            """检查更新。网络不通时如实说不知道，不要冒充「已是最新」。"""
            from check_update import check_for_update
            has_new, current, latest = check_for_update()
            if has_new:
                status = "new"
            elif current and not latest:
                status = "latest"
            else:
                status = "unknown"
            self._json({"ok": True, "status": status,
                        "current": current, "latest": latest})

        def _send_file(self, path: str, download_name: str) -> None:
            """把一个本地文件当成下载响应发出去。"""
            size = os.path.getsize(path)
            ascii_name = download_name.encode("ascii", "replace").decode("ascii")
            ascii_name = ascii_name.translate(_UNSAFE_HEADER_CHARS)
            disposition = (f'attachment; filename="{ascii_name}"; '
                           f"filename*=UTF-8''{urllib.parse.quote(download_name)}")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", disposition)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 64 * 1024)

        def _save_ai_config(self, body: dict) -> None:
            """保存 AI 设置。密钥只进不出：查询接口只回 key_set 布尔值。"""
            config = load_config(project_dir)
            current = config.get("ai")
            section = dict(current) if isinstance(current, dict) else {}
            if "enabled" in body:
                section["enabled"] = bool(body["enabled"])
            if "base_url" in body:
                section["base_url"] = str(body.get("base_url") or "").strip()
            if "model" in body:
                section["model"] = str(body.get("model") or "").strip()
            # 空字符串一律当成「不改」：前端每次保存都会带着这三个字段，而它
            # 拿不到已保存的密钥（接口只回 key_set）。真要清空请显式传 clear_key。
            api_key = body.get("api_key")
            if isinstance(api_key, str) and api_key.strip():
                section["api_key"] = api_key.strip()
            if body.get("clear_key"):
                section["api_key"] = ""
            config["ai"] = section
            save_config(config, project_dir)
            return self._json({"ok": True, "message": t("web.settings.ai_saved"),
                               "ai": assistant.state()})

        def _post_rest(self, path: str, body: dict) -> None:
            """do_POST 里除进程控制之外的部分。"""
            name = str(body.get("name") or "")

            # -- AI 助手 ---------------------------------------------------
            if path == "/api/ai/chat":
                return self._json(assistant.chat(
                    str(body.get("text") or ""),
                    str(body.get("instance") or "")))

            if path == "/api/ai/confirm":
                return self._json(assistant.confirm(
                    str(body.get("id") or ""), bool(body.get("approve"))))

            if path == "/api/ai/reset":
                return self._json(assistant.reset())

            if path == "/api/ai/config":
                return self._save_ai_config(body)

            if path == "/api/eula":
                return self._accept_eula(body)

            if path == "/api/settings":
                return self._settings_save(body)

            if path == "/api/jars/toggle":
                return self._jar_toggle(body)

            if path == "/api/jars/delete":
                return self._jar_delete(body)

            if path == "/api/jars/download":
                return self._jar_download(body)

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

            # java_path 也在 _PATCH_FIELDS 白名单里、也由 /api/state 下发，
            # 这里漏掉它的话，接口收下了却永远写不进 .server.json
            for key in ("mc_version", "jar", "min_mem", "max_mem",
                        "process_priority", "java_path"):
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
            全部实例的状态，也不必在切实例时重连。

            订阅放在发响应头**之前**：这一步失败时还一个字节都没写过，
            _guard 的兜底 JSON 能正常送出去。反过来先发头再订阅，一旦订阅
            抛异常就只剩往 SSE 流里塞状态行这一条路，前端拿到的是一条永远
            连不上的流。发头之后的所有异常都自己吞掉——连接断了就安静收摊。
            """
            name = query.get("name") or ""
            inbox: "queue.Queue[dict]" = queue.Queue()
            unsubscribe = manager.subscribe(inbox.put)
            try:
                unsubscribe_jobs = JOB_BUS.subscribe(inbox.put)
            except Exception:  # noqa: BLE001
                unsubscribe()
                raise
            try:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True

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


def _config_token_provider(project_dir: str):
    """返回一个「现读配置里的 token」的函数，供运行中的面板用。

    单独抽出来是为了让处理器不必自己碰配置读取，也方便测试直接塞一个假的。

    刻意不做缓存：面板是本机单用户的，`load_config` 一次约 0.2ms，相对于
    这些接口本身做的事可以忽略；而缓存鉴权状态正是「token 换了却不生效」
    这类 bug 的来源。要改成缓存的话，务必按 `config.json` 的 mtime + 大小
    失效，别只缓存时间。
    """

    def provider() -> str:
        return web_settings(load_config(project_dir))[2]

    return provider


def build_server(project_dir: str, *, host: str = "127.0.0.1", port: int = 8765,
                 token: str = "", manager: Optional[RuntimeManager] = None,
                 servers_dir: Optional[str] = None,
                 token_provider: Any = None) -> ThreadingHTTPServer:
    """起一个 HTTP 服务（不进入事件循环），方便测试与嵌入。

    token_provider 传了的话，每个请求都会现读一次 token（重新生成 token
    后无需重启面板）；不传就只认 token 参数——测试与嵌入方能完全掌控鉴权。
    """
    # 业务错误文案靠 locales 翻译，不加载语言的话 t() 只会把 i18n key 原样
    # 吐出来，调用方看到的就是 "web.error.xxx"。嵌入方（测试、别的入口）
    # 通常不会自己去 load_language，所以在这里补上。
    config = load_config(project_dir)
    load_language(config.get("language", "") or detect_os_language())

    servers_dir = servers_dir or ensure_servers_folder(project_dir)
    manager = manager or shared_manager(project_dir)
    handler = _make_handler(manager, servers_dir, token, project_dir, token_provider)
    httpd = _PanelServer((host, port), handler)
    return httpd


# ---------------------------------------------------------------------------
# 随软件启动（后台常驻，不阻塞终端菜单）
# ---------------------------------------------------------------------------

_background_server: Optional[ThreadingHTTPServer] = None
# start_background 与 _stop_background（atexit）可能来自不同线程：前者在
# 菜单线程，后者在解释器退出时。没有这把锁，「检查有没有起过」和「记下来」
# 之间就有一段空窗。
_background_lock = threading.Lock()


def _stop_background() -> None:
    global _background_server
    with _background_lock:
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


def _prepare_data_dir(project_dir: str, config: dict) -> Optional[str]:
    """准备数据目录并取到 token。失败返回 None，原因已经打印出来。

    这两件事以前混在「绑定端口」的 try 里，而它们抛的同样是 OSError：数据
    目录不可写时（例如把 exe 解压到了 Program Files），用户看到的是
    「无法监听 xxx，端口可能已被占用，去菜单里换一个」——照着换端口永远
    也好不了。分开之后，那条消息才真的只表示端口问题。
    """
    try:
        ensure_servers_folder(project_dir)
        return ensure_token(project_dir, config)
    except OSError as e:
        print(t("web.cli.data_dir_failed", path=project_dir, msg=str(e)))
        return None


def start_background(project_dir: str, host: Optional[str] = None,
                     port: Optional[int] = None) -> Optional[ThreadingHTTPServer]:
    """在后台线程里把面板拉起来，供「随软件启动」使用。

    与 run_web 的区别是不阻塞：面板跑在守护线程上，主线程该进菜单进菜单、
    该起服务器起服务器。失败返回 None（端口被占之类），调用方据此决定要
    不要提示。
    """
    global _background_server

    config = load_config(project_dir)
    load_language(config.get("language", "") or detect_os_language())

    cfg_host, cfg_port, _ = web_settings(config)
    token = _prepare_data_dir(project_dir, config)
    if token is None:
        return None
    bind_host = host or cfg_host
    bind_port = cfg_port if port is None else port

    with _background_lock:
        if _background_server is not None:
            return _background_server      # 已经在跑了，别起第二个

        try:
            httpd = build_server(project_dir, host=bind_host, port=bind_port, token=token,
                                 token_provider=_config_token_provider(project_dir))
        except OSError as e:
            print(t("web.cli.bind_failed", addr=f"{bind_host}:{bind_port}", msg=str(e)))
            return None

        # 先把句柄记下来再起线程：反过来的话，线程已经在跑而
        # _background_server 还是 None，这中间进来的 _stop_background()
        # （atexit）会看不到它，面板永远不会被关掉；并发的第二次
        # start_background 也会以为没人起过，再往同一个端口绑一次。
        _background_server = httpd

    threading.Thread(target=httpd.serve_forever, name="mstl-web", daemon=True).start()
    atexit.register(_stop_background)

    _warn_missing_assets()
    shown_host = "127.0.0.1" if bind_host in ("0.0.0.0", "::") else bind_host
    print(t("web.cli.auto_launched",
            url=f"http://{shown_host}:{httpd.server_address[1]}/?token={token}"))
    if bind_host in ("0.0.0.0", "::"):
        print(t("web.cli.external_warning"))
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
    token = _prepare_data_dir(project_dir, config)
    if token is None:
        return False

    bind_host = host or cfg_host
    # 用 is not None 判断：--port 0 是「随便挑一个空闲端口」，是合法请求，
    # 写成 `port or cfg_port` 会把它当成没传
    bind_port = cfg_port if port is None else port

    try:
        httpd = build_server(project_dir, host=bind_host, port=bind_port, token=token,
                             token_provider=_config_token_provider(project_dir))
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
