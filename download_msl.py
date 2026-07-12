"""
MSL API V4 服务端下载模块

通过 MSL 镜像源下载 Minecraft 服务端核心。
API 端点：https://api.mslmc.cn/v4
"""

import os
import json
import shutil
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Optional

from server_manager import save_server_config

MSL_API_BASE = "https://api.mslmc.cn/v4"
MSL_USER_AGENT = "MSTL/1.0"

_CATEGORY_LABELS: dict[str, str] = {
    "pluginsCore": "插件端",
    "pluginsAndModsCore_Forge": "插件+模组端 (Forge)",
    "pluginsAndModsCore_Fabric": "插件+模组端 (Fabric)",
    "modsCore_Forge": "模组端 (Forge)",
    "modsCore_Fabric": "模组端 (Fabric)",
    "vanillaCore": "原版端",
    "bedrockCore": "基岩版",
    "proxyCore": "代理端",
}

_SERVER_DISPLAY_NAMES: dict[str, str] = {
    "paper": "Paper", "purpur": "Purpur", "leaf": "Leaf", "leaves": "Leaves",
    "spigot": "Spigot", "bukkit": "Bukkit", "folia": "Folia",
    "pufferfish": "Pufferfish", "pufferfish_purpur": "Pufferfish+Purpur",
    "spongevanilla": "SpongeVanilla",
    "arclight-forge": "Arclight (Forge)", "arclight-fabric": "Arclight (Fabric)",
    "arclight-neoforge": "Arclight (NeoForge)", "youer": "Youer", "mohist": "Mohist",
    "catserver": "CatServer", "banner": "Banner", "spongeforge": "SpongeForge",
    "neoforge": "NeoForge", "forge": "Forge", "fabric": "Fabric", "quilt": "Quilt",
    "vanilla": "Vanilla", "vanilla-snapshot": "Vanilla Snapshot",
    "bedrock-server": "Bedrock Server", "nukkitx": "NukkitX",
    "velocity": "Velocity", "bungeecord": "BungeeCord", "lightfall": "Lightfall",
    "travertine": "Travertine",
}


def _server_display_name(server_id: str) -> str:
    return _SERVER_DISPLAY_NAMES.get(server_id, server_id.capitalize())


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------

def _msl_cache_path(project_dir: str) -> str:
    return os.path.join(project_dir, "msl_cache.json")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _msl_load_cache(project_dir: str) -> dict:
    p = _msl_cache_path(project_dir)
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _msl_save_cache(cache: dict, project_dir: str) -> None:
    p = _msl_cache_path(project_dir)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _msl_cache_fetch(cache_key: str, path: str, project_dir: str) -> Any:
    cache = _msl_load_cache(project_dir)
    entry = cache.get(cache_key)
    today = _today_str()
    if entry and isinstance(entry, dict) and entry.get("cached_at") == today:
        print(f"  [缓存] 使用本地缓存 ({today})")
        return entry.get("data")

    url = f"{MSL_API_BASE}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  [错误] MSL API 返回 HTTP {e.code}: {e.reason}"); return None
    except urllib.error.URLError as e:
        print(f"  [错误] 网络请求失败: {e.reason}"); return None
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [错误] 响应解析失败: {e}"); return None

    if not isinstance(data, dict) or data.get("code") != 200:
        print(f"  [错误] MSL API: {data.get('message', '异常')}"); return None
    result = data.get("data")
    cache[cache_key] = {"cached_at": today, "data": result}
    _msl_save_cache(cache, project_dir)
    return result


def _msl_request(path: str) -> Any:
    """直接请求（无缓存），用于下载地址。"""
    url = f"{MSL_API_BASE}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  [错误] MSL API 返回 HTTP {e.code}: {e.reason}"); return None
    except urllib.error.URLError as e:
        print(f"  [错误] 网络请求失败: {e.reason}"); return None
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [错误] 响应解析失败: {e}"); return None
    if not isinstance(data, dict) or data.get("code") != 200:
        print(f"  [错误] MSL API: {data.get('message', '异常')}"); return None
    return data.get("data")


def msl_get_server_types(project_dir: str = "") -> Optional[dict]:
    return _msl_cache_fetch("server_types", "/mirrors", project_dir) if project_dir else _msl_request("/mirrors")


def msl_get_versions(server_type: str, project_dir: str = "") -> Optional[dict]:
    key = f"versions_{server_type}"
    return _msl_cache_fetch(key, f"/mirrors/{server_type}", project_dir) if project_dir else _msl_request(f"/mirrors/{server_type}")


def msl_get_download_url(server_type: str, version: str) -> Optional[dict]:
    return _msl_request(f"/download/server/{server_type}/{version}")


# ---------------------------------------------------------------------------
# 下载进度
# ---------------------------------------------------------------------------

def _download_with_progress(url: str, target_path: str, desc: str = "") -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = resp.length
            downloaded = 0
            bar_width = 30
            chunk_size = 8192
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total and total > 0:
                        pct = downloaded / total
                        filled = int(bar_width * pct)
                        bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                        print(f"    {desc} [{bar}] {pct * 100:5.1f}%", end="\r", flush=True)
                    else:
                        print(f"    {desc} 已下载 {downloaded/1024/1024:.1f} MB...", end="\r", flush=True)
        print()
        return True
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"\n  [错误] 下载失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 交互选择
# ---------------------------------------------------------------------------

def _interactive_pick_server_type(categorized: dict) -> Optional[str]:
    all_servers: list[tuple[str, str]] = []
    for cat, lst in categorized.items():
        label = _CATEGORY_LABELS.get(cat, cat)
        if not isinstance(lst, list):
            continue
        for sid in lst:
            if isinstance(sid, str):
                all_servers.append((sid, label))
    if not all_servers:
        print("  [错误] 服务端列表为空"); return None
    print()
    print("  请选择服务端类型：(MSL镜像源, https://www.mslmc.cn/)")
    print()
    current_cat = ""
    idx = 1
    index_map: list[tuple[int, str]] = []
    for sid, cat in all_servers:
        if cat != current_cat:
            print(f"  [{cat}]")
            current_cat = cat
        print(f"      [{idx}] {_server_display_name(sid)}")
        index_map.append((idx, sid))
        idx += 1
    print()
    while True:
        try:
            c = input(f"  请输入编号 (1-{len(index_map)}): ").strip()
            n = int(c)
            for i, sid in index_map:
                if i == n:
                    return sid
        except ValueError:
            pass
        print(f"  无效选择。")


def _interactive_pick_version(versions: list[str], description: str) -> Optional[str]:
    if not versions:
        print("  [错误] 没有可用版本"); return None
    print()
    if description:
        print(f"  {description}")
    print()
    print(f"  可用版本（共 {len(versions)} 个），默认下载最新版：")
    print()
    stable = [v for v in versions if not any(x in v.lower() for x in ("pre", "rc", "snapshot", "alpha", "beta"))]
    show = stable[:20] if stable else versions[:20]
    for i, ver in enumerate(show, 1):
        print(f"  [{i}] {ver}{' ← 最新' if i == 1 else ''}")
    print(f"  [L] 直接下载最新版 ({show[0]})")
    print()
    while True:
        c = input(f"  请输入编号 (1-{len(show)} 或 L): ").strip().lower()
        if c == "l":
            return show[0]
        try:
            idx = int(c) - 1
            if 0 <= idx < len(show):
                return show[idx]
        except ValueError:
            pass
        print(f"  无效选择。")


def _prompt_post_download_config(server_name: str) -> dict:
    print()
    print("  服务器下载完成，请配置运行时参数（直接回车使用默认值）：")
    print()
    ji = input("  Java 路径（回车自动检测）: ").strip()
    java_path = ji if ji else None
    while True:
        mi = input("  最小内存 [1G]: ").strip()
        if not mi:
            min_mem = "1G"; break
        if mi.upper().endswith(("G", "M")):
            min_mem = mi.upper(); break
        print("  格式错误，请输入如 1G、2048M")
    while True:
        ma = input("  最大内存 [4G]: ").strip()
        if not ma:
            max_mem = "4G"; break
        if ma.upper().endswith(("G", "M")):
            max_mem = ma.upper(); break
        print("  格式错误，请输入如 4G、4096M")
    return {"java_path": java_path, "min_mem": min_mem, "max_mem": max_mem}


def show_download_server_menu(servers_dir: str, project_dir: str) -> Optional[str]:
    """交互式下载服务器界面。返回服务器目录路径，失败返回 None。"""
    print(); print("  [下载] 正在获取可用服务端列表..."); print()
    categorized = msl_get_server_types(project_dir)
    if not categorized:
        print("  [错误] 无法获取服务端列表"); return None
    server_id = _interactive_pick_server_type(categorized)
    if server_id is None:
        return None

    print(f"  [下载] 正在获取 {_server_display_name(server_id)} 的版本列表...")
    version_data = msl_get_versions(server_id, project_dir)
    if not version_data:
        print(f"  [错误] 无法获取版本信息"); return None
    selected_version = _interactive_pick_version(version_data.get("versions", []), version_data.get("description", ""))
    if selected_version is None:
        return None

    print(f"  [下载] 已选择: {_server_display_name(server_id)} {selected_version}")
    print("  [下载] 正在获取下载地址...")
    download_info = msl_get_download_url(server_id, selected_version)
    if not download_info:
        print("  [错误] 无法获取下载地址（每小时限 30 次，每天 60 次）"); return None
    url = download_info.get("url", "")
    if not url:
        print("  [错误] 下载地址为空"); return None

    # 创建服务器目录
    server_name = f"{server_id}-{selected_version}"
    server_dir = os.path.join(servers_dir, server_name)
    counter = 1
    while os.path.exists(server_dir):
        server_dir = os.path.join(servers_dir, f"{server_name}_{counter}")
        counter += 1
    os.makedirs(server_dir)

    jar_filename = os.path.basename(url.split("?")[0])
    if not jar_filename.endswith(".jar"):
        jar_filename = f"{server_id}.jar"
    jar_path = os.path.join(server_dir, jar_filename)

    print(f"  [下载] 正在下载 {_server_display_name(server_id)} {selected_version}")
    print(f"         保存到: {jar_path}")
    if not _download_with_progress(url, jar_path, desc="下载中"):
        shutil.rmtree(server_dir, ignore_errors=True); return None
    if not os.path.isfile(jar_path) or os.path.getsize(jar_path) == 0:
        print("  [错误] 下载的文件无效")
        shutil.rmtree(server_dir, ignore_errors=True); return None

    post_cfg = _prompt_post_download_config(server_name)
    save_server_config({
        "name": server_name, "mc_version": selected_version, "jar": jar_filename,
        "java_path": post_cfg["java_path"], "min_mem": post_cfg["min_mem"],
        "max_mem": post_cfg["max_mem"], "extra_jvm_args": [], "extra_server_args": [],
    }, server_dir)

    size_mb = os.path.getsize(jar_path) / 1024 / 1024
    print(f"  [下载] 下载完成！({size_mb:.1f} MB)")
    if download_info.get("sha256"):
        print(f"          SHA256: {download_info['sha256']}")
    print(f"          服务器目录: {server_dir}")
    return server_dir
