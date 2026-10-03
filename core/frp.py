"""
原版 frp（frpc）配置的读写与进程控制。

CLI 的隧道菜单直接在 menu.py 里 print/input，网页面板不能那么干，所以把
「配置长什么样、怎么改、怎么起停」抽到这里：这一层只做类型收敛与参数校验，
不打印也不提问。协议与进程管理仍然是 frp/tunnel_manager 那套极简 TOML
读写 + 单例进程句柄，两边共用同一个 frpc 进程。

面板能改的只有 frpc.toml 里本项目用到的那几个键：serverAddr / serverPort /
auth.token 与 [[proxies]]。其余内容读进来会原样保留，写回去不会丢——包括
[[visitors]] 这类别的「表的数组」，不能把它们写成字符串。

唯一的例外是注释：那个极简 TOML 读写不保留注释，保存一次就会被去掉。
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Optional

from .errors import CoreError
from frp.tunnel_manager import (
    download_frpc,
    expected_asset_name,
    get_frpc_bin_path,
    get_frpc_toml_path,
    get_latest_frp_version,
    install_frpc_from_file,
    is_frpc_running,
    launch_frpc,
    load_frp_config,
    releases_url,
    save_frp_config,
    stop_frpc,
)

DEFAULT_SERVER_PORT = 7000
MAX_PORT = 65535

# 手动上传 frpc 时的大小上限。frp 完整包约 15~20 MB，这里给足余量，
# 同时挡住「传了个几百 MB 的东西过来」。
MAX_FRPC_UPLOAD_BYTES = 200 * 1024 * 1024

# 面板上能选的隧道类型。tcp / udp 要 remotePort，http / https 要域名。
PROXY_TYPES = ("tcp", "udp", "http", "https")

# 一次最多列这么多条隧道，避免配置文件被写坏之后界面直接卡住
MAX_PROXIES = 200


def _as_port(value: Any, fallback: int = 0) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    if port < 0 or port > MAX_PORT:
        return fallback
    return port


def _auth_section(cfg: dict) -> dict:
    """取出（必要时建立）auth 表。auth 被手改成非 dict 时重建一个。"""
    auth = cfg.get("auth")
    if not isinstance(auth, dict):
        auth = {}
        cfg["auth"] = auth
    return auth


def read_settings(project_dir: str) -> dict[str, Any]:
    """读 frpc 的连接设置。文件不存在就返回空壳，不报错。"""
    cfg = load_frp_config(project_dir)
    auth = cfg.get("auth") if isinstance(cfg.get("auth"), dict) else {}
    return {
        "serverAddr": str(cfg.get("serverAddr") or ""),
        "serverPort": _as_port(cfg.get("serverPort"), DEFAULT_SERVER_PORT),
        "token": str(auth.get("token") or ""),
    }


def write_settings(project_dir: str, *, server_addr: Any = None,
                   server_port: Any = None, token: Any = None) -> dict[str, Any]:
    """更新 frpc 的连接设置。只覆盖显式传进来的字段。

    token 传空字符串表示清空；传 None 表示不动。
    """
    cfg = load_frp_config(project_dir)

    if server_addr is not None:
        cfg["serverAddr"] = str(server_addr).strip()
    if server_port is not None:
        port = _as_port(server_port, 0)
        if port <= 0:
            raise CoreError("web.error.frp_bad_port", port=server_port)
        cfg["serverPort"] = port
    if token is not None:
        token_text = str(token).strip()
        if token_text:
            _auth_section(cfg)["token"] = token_text
        else:
            _auth_section(cfg).pop("token", None)

    if not save_frp_config(project_dir, cfg):
        raise CoreError("web.error.frp_save_failed")
    return read_settings(project_dir)


def list_proxies(project_dir: str) -> list[dict[str, Any]]:
    """列出所有隧道，附带在列表里的下标（删改时按它定位）。"""
    cfg = load_frp_config(project_dir)
    raw = cfg.get("proxies")
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for index, item in enumerate(raw[:MAX_PROXIES]):
        if not isinstance(item, dict):
            continue
        domains = item.get("customDomains")
        if isinstance(domains, str):
            domains = [domains]
        elif not isinstance(domains, list):
            domains = []
        out.append({
            "index": index,
            "name": str(item.get("name") or f"proxy-{index + 1}"),
            "type": str(item.get("type") or "tcp"),
            "localIP": str(item.get("localIP") or "127.0.0.1"),
            "localPort": _as_port(item.get("localPort"), 0),
            "remotePort": _as_port(item.get("remotePort"), 0),
            "customDomains": [str(d) for d in domains],
        })
    return out


def add_proxy(project_dir: str, *, name: Any, proxy_type: Any,
              local_ip: Any = "127.0.0.1", local_port: Any,
              remote_port: Any = None, custom_domains: Any = None) -> dict[str, Any]:
    """追加一条隧道。参数不合法就抛 CoreError，不会写出半条配置。"""
    name_text = str(name or "").strip()
    if not name_text:
        raise CoreError("web.error.frp_proxy_name_required")

    ptype = str(proxy_type or "tcp").strip().lower()
    if ptype not in PROXY_TYPES:
        raise CoreError("web.error.frp_bad_proxy_type", type=proxy_type)

    port = _as_port(local_port, 0)
    if port <= 0:
        raise CoreError("web.error.frp_bad_port", port=local_port)

    cfg = load_frp_config(project_dir)
    proxies = cfg.get("proxies")
    if not isinstance(proxies, list):
        proxies = []
    if any(isinstance(p, dict) and str(p.get("name")) == name_text for p in proxies):
        raise CoreError("web.error.frp_proxy_exists", name=name_text)

    proxy: dict[str, Any] = {
        "name": name_text,
        "type": ptype,
        "localIP": str(local_ip or "127.0.0.1").strip() or "127.0.0.1",
        "localPort": port,
    }
    if ptype in ("tcp", "udp"):
        remote = _as_port(remote_port, 0)
        if remote <= 0:
            raise CoreError("web.error.frp_remote_port_required")
        proxy["remotePort"] = remote
    else:
        domains = custom_domains
        if isinstance(domains, str):
            domains = [d.strip() for d in domains.replace(",", " ").split() if d.strip()]
        elif isinstance(domains, list):
            domains = [str(d).strip() for d in domains if str(d).strip()]
        else:
            domains = []
        if not domains:
            raise CoreError("web.error.frp_domain_required")
        proxy["customDomains"] = domains

    proxies.append(proxy)
    cfg["proxies"] = proxies
    if not save_frp_config(project_dir, cfg):
        raise CoreError("web.error.frp_save_failed")
    return list_proxies(project_dir)[-1]


def remove_proxy(project_dir: str, index: Any) -> None:
    """按下标删掉一条隧道。"""
    try:
        idx = int(index)
    except (TypeError, ValueError):
        raise CoreError("web.error.frp_bad_proxy_index") from None

    cfg = load_frp_config(project_dir)
    proxies = cfg.get("proxies")
    if not isinstance(proxies, list) or not (0 <= idx < len(proxies)):
        raise CoreError("web.error.frp_bad_proxy_index")
    proxies.pop(idx)
    cfg["proxies"] = proxies
    if not save_frp_config(project_dir, cfg):
        raise CoreError("web.error.frp_save_failed")


def status(project_dir: str) -> dict[str, Any]:
    """frpc 的当前状态。不联网，所以不会因为网络慢把面板拖住。"""
    bin_path = get_frpc_bin_path(project_dir)
    toml_path = get_frpc_toml_path(project_dir)
    return {
        "running": is_frpc_running(),
        "binary": os.path.isfile(bin_path),
        "binaryPath": bin_path,
        "config": os.path.isfile(toml_path),
        "configPath": toml_path,
    }


def latest_version() -> str:
    """查 frpc 最新版本号。会联网，只在用户点「下载/更新」时调用。"""
    return get_latest_frp_version() or ""


def manual_info() -> dict[str, str]:
    """下载不通时给用户的兜底信息：去哪下、下哪个文件、装出来叫什么。

    故意不联网——需要它的场合往往正是网络有问题的时候。
    """
    return {
        "url": releases_url(),
        "asset": expected_asset_name(),
        "binName": "frpc.exe" if os.name == "nt" else "frpc",
    }


def download(project_dir: str, on_progress=None) -> str:
    """下载（或重新下载）frpc 二进制，返回路径。

    失败时把 GitHub 发布页地址一并带进错误里：面板会把 message 弹给用户，
    里面直接有可以点的地址，不用再让人自己去搜。on_progress 透传给下载
    引擎，供界面层画进度。
    """
    path = download_frpc(project_dir, quiet=True, on_progress=on_progress)
    if not path or not os.path.isfile(path):
        raise CoreError("web.error.frpc_download_failed",
                        url=releases_url(), asset=expected_asset_name())
    return path


def install_from_stream(project_dir: str, stream, content_length: int,
                        max_bytes: int = MAX_FRPC_UPLOAD_BYTES) -> str:
    """把用户手动下载的 frpc 包/二进制装好，返回可执行文件路径。

    压缩包与裸二进制都认（按内容判断，不看文件名）。上传流按
    Content-Length 精确读取：这是 HTTP 长连接上的请求体，读到 EOF 会一直
    阻塞在下一个字节上。先落到临时文件，装完就删。
    """
    if content_length <= 0:
        raise CoreError("web.error.upload_empty")
    if content_length > max_bytes:
        raise CoreError("web.error.upload_too_large",
                        max=round(max_bytes / 1024 / 1024))

    fd, tmp_path = tempfile.mkstemp(prefix="mstl-frpc-")
    os.close(fd)
    try:
        remaining = content_length
        with open(tmp_path, "wb") as f:
            while remaining > 0:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                f.write(chunk)
        if remaining != 0:
            raise CoreError("web.error.upload_incomplete")
        installed = install_frpc_from_file(tmp_path, project_dir)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not installed:
        raise CoreError("web.error.frpc_install_failed",
                        asset=expected_asset_name(),
                        name=os.path.basename(get_frpc_bin_path(project_dir)))
    return installed


def start(project_dir: str) -> dict[str, Any]:
    """启动 frpc。已经在跑就原样返回，不当成失败。"""
    already = is_frpc_running()
    proc = launch_frpc(project_dir, quiet=True)
    if proc is None:
        raise CoreError("web.error.frp_start_failed")
    return {
        "running": True,
        "already_running": already,
        "pid": proc.pid,
    }


def stop() -> None:
    """停止 frpc。没在跑也算成功。"""
    stop_frpc(quiet=True)


__all__ = [
    "DEFAULT_SERVER_PORT",
    "MAX_FRPC_UPLOAD_BYTES",
    "PROXY_TYPES",
    "read_settings",
    "write_settings",
    "list_proxies",
    "add_proxy",
    "remove_proxy",
    "status",
    "latest_version",
    "manual_info",
    "download",
    "install_from_stream",
    "start",
    "stop",
]
