"""BMCLAPI 镜像（bmclapi2.bangbang93.com）。

国内社区维护的 Minecraft 资源镜像，最初就是为了绕开 Mojang 那套 Amazon S3
在国内的速度问题。这个项目只下服务端，所以用得上的是两块：

    /mc/game/version_manifest_v2.json   原版版本清单（Mojang 那份的镜像）
    /version/<版本>/json                该版本的元数据，含 downloads.server 的
                                        url 与 sha1
    /version/<版本>/server              原版服务端 jar 本体
    /fabric-meta/v2/...                 Fabric 的元数据（meta.fabricmc.net 的镜像）

**它不代理的东西也要说清楚**：Fabric 的 server jar 是 meta.fabricmc.net 现拼
出来的，BMCL 上没有（实测 404）；Paper / Purpur 这类也不镜像。所以这里只负责
「原版核心」和「Fabric 的元数据」，其余核心照旧走 MSL 或各自的官方源。

契约和 download_msl 里那批 _official_* 函数一致：失败返回 None，不打印、
不抛异常，由调用方决定退到哪里。所有请求都走 http_utils.fetch_json，那边已经
把超时和异常兜住了。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from constants import BMCL_API_BASE
from http_utils import fetch_json

# 版本清单和 Fabric 的元数据都是小 JSON，但面板每 15 秒刷一次状态、下载菜单
# 也会反复打开。缓存 10 分钟，够用又不至于让「刚出的新版本」看不到。
_CACHE_TTL = 600

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()


def _cached(key: str, loader) -> Optional[Any]:
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    value = loader()
    if value is not None:
        with _lock:
            _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _json(path: str, timeout: int = 15) -> Optional[Any]:
    """取一个 JSON。失败重试一次。

    这个镜像在并发稍微多一点的时候会直接重置连接（实测连续几个请求里就会
    掉一个），而它掉一下，用户看到的就是「下载地址获取失败」。多问一次比
    让调用方去猜运气便宜。
    """
    url = f"{BMCL_API_BASE}{path}"
    data = fetch_json(url, timeout=timeout)
    if data is None:
        time.sleep(0.4)
        data = fetch_json(url, timeout=timeout)
    return data


# ---------------------------------------------------------------------------
# 原版
# ---------------------------------------------------------------------------

def version_manifest() -> Optional[dict]:
    return _cached("manifest", lambda: _json("/mc/game/version_manifest_v2.json"))


def vanilla_versions() -> Optional[dict]:
    """原版版本列表，形状和 MSL 一致：{"versions": [...], "description": ""}。

    只收 release：快照装不了服，列出来只会被误选。顺序是新的在前，
    调用方（_interactive_pick_version）默认把第一个当最新版。
    """
    data = version_manifest()
    if not isinstance(data, dict):
        return None
    versions = [str(v.get("id")) for v in data.get("versions") or []
                if isinstance(v, dict) and v.get("type") == "release"]
    if not versions:
        return None
    return {"versions": versions, "description": ""}


def vanilla_server(version: str) -> Optional[dict]:
    """原版服务端 jar 的地址与 sha1。

    元数据走 /version/<v>/json，而不是自己拼 piston-data 的地址：对象路径里
    带哈希，拼不出来。这个 json 同时也给了 sha1，比原来「从 Mojang 拿地址、
    不给任何校验」强一截。
    """
    data = _json(f"/version/{version}/json", timeout=20)
    if not isinstance(data, dict):
        return None
    server = ((data.get("downloads") or {}).get("server") or {})
    if not server.get("url"):
        return None
    return {
        "url": f"{BMCL_API_BASE}/version/{version}/server",
        # BMCL 的 version json 里 url 指向 Mojang 的 object；这里换成镜像地址，
        # 但校验值仍然用 Mojang 给的那份，两者是同一个文件
        "sha1": str(server.get("sha1") or ""),
        "sha256": "",
    }


# ---------------------------------------------------------------------------
# Fabric
# ---------------------------------------------------------------------------

def fabric_versions() -> Optional[dict]:
    data = _cached("fabric_game", lambda: _json("/fabric-meta/v2/versions/game"))
    if not isinstance(data, list):
        return None
    versions = [str(v.get("version")) for v in data
                if isinstance(v, dict) and v.get("version")]
    if not versions:
        return None
    return {"versions": versions, "description": ""}


def fabric_loader(version: str) -> Optional[str]:
    """该 MC 版本最新的稳定 loader 版本号。"""
    data = _json(f"/fabric-meta/v2/versions/loader/{version}", timeout=20)
    if not isinstance(data, list):
        return None
    for entry in data:
        loader = (entry or {}).get("loader") or {}
        if loader.get("stable") and loader.get("version"):
            return str(loader["version"])
    return None


def fabric_server_url(version: str, loader: str) -> str:
    """Fabric 的 server jar 地址。

    这条 **不在** BMCL 上（实测 404），只能回 meta.fabricmc.net。放在这里是为了
    让「元数据走镜像、jar 走官方」这一半一半的事实留在一处，而不是散在调用方。
    """
    return (f"https://meta.fabricmc.net/v2/versions/loader/{version}"
            f"/{loader}/server/jar")


__all__ = [
    "version_manifest",
    "vanilla_versions",
    "vanilla_server",
    "fabric_versions",
    "fabric_loader",
    "fabric_server_url",
    "clear_cache",
]
