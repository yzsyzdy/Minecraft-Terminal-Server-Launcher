"""
MSL API V4 server download module

Downloads Minecraft server jars via MSL mirror API.
API endpoint: https://api.mslmc.cn/v4
"""

import os
import json
import shutil
import time
import threading
import concurrent.futures
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Optional

from server_manager import save_server_config
from i18n import t
from constants import MSL_API_BASE, USER_AGENT

MSL_USER_AGENT = USER_AGENT

_CATEGORY_LABELS: dict[str, str] = {
    "pluginsCore": "download.category.pluginsCore",
    "pluginsAndModsCore_Forge": "download.category.pluginsAndModsCore_Forge",
    "pluginsAndModsCore_Fabric": "download.category.pluginsAndModsCore_Fabric",
    "modsCore_Forge": "download.category.modsCore_Forge",
    "modsCore_Fabric": "download.category.modsCore_Fabric",
    "vanillaCore": "download.category.vanillaCore",
    "bedrockCore": "download.category.bedrockCore",
    "proxyCore": "download.category.proxyCore",
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
# Cache
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
        print(t("cache.hit", date=today))
        return entry.get("data")

    url = f"{MSL_API_BASE}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(t("app.error", msg=f"MSL API HTTP {e.code}: {e.reason}")); return None
    except urllib.error.URLError as e:
        print(t("app.error", msg=f"Network error: {e.reason}")); return None
    except (json.JSONDecodeError, OSError) as e:
        print(t("app.error", msg=f"Response parse failed: {e}")); return None

    if not isinstance(data, dict) or data.get("code") != 200:
        print(t("app.error", msg=data.get("message", "Abnormal"))); return None
    result = data.get("data")
    cache[cache_key] = {"cached_at": today, "data": result}
    _msl_save_cache(cache, project_dir)
    return result


def _msl_request(path: str) -> Any:
    """Direct request (no cache), used for download URLs."""
    url = f"{MSL_API_BASE}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(t("app.error", msg=f"MSL API HTTP {e.code}: {e.reason}")); return None
    except urllib.error.URLError as e:
        print(t("app.error", msg=f"Network error: {e.reason}")); return None
    except (json.JSONDecodeError, OSError) as e:
        print(t("app.error", msg=f"Response parse failed: {e}")); return None
    if not isinstance(data, dict) or data.get("code") != 200:
        print(t("app.error", msg=data.get("message", "Abnormal"))); return None
    return data.get("data")


def msl_get_server_types(project_dir: str = "") -> Optional[dict]:
    return _msl_cache_fetch("server_types", "/mirrors", project_dir) if project_dir else _msl_request("/mirrors")


def msl_get_versions(server_type: str, project_dir: str = "") -> Optional[dict]:
    key = f"versions_{server_type}"
    return _msl_cache_fetch(key, f"/mirrors/{server_type}", project_dir) if project_dir else _msl_request(f"/mirrors/{server_type}")


def msl_get_download_url(server_type: str, version: str) -> Optional[dict]:
    return _msl_request(f"/download/server/{server_type}/{version}")


# ---------------------------------------------------------------------------
# Multi-threaded download
# ---------------------------------------------------------------------------

_DEFAULT_DOWNLOAD_THREADS = 16
_MIN_CHUNK_SIZE = 1 * 1024 * 1024


def _check_range_support(url: str) -> tuple[bool, int]:
    """Check if server supports Range requests. Returns (supported?, file_size)."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": MSL_USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as resp:
            accept_ranges = resp.headers.get("Accept-Ranges", "")
            content_length = resp.headers.get("Content-Length")
            size = int(content_length) if content_length else 0
            return "bytes" in accept_ranges, size
    except Exception:
        return False, 0


def _download_chunk(url: str, start: int, end: int, target_path: str,
                    shared: dict, lock: threading.Lock) -> bool:
    """Download a chunk and write to the target file at the correct offset."""
    range_header = f"bytes={start}-{end}"
    req = urllib.request.Request(url, headers={
        "User-Agent": MSL_USER_AGENT,
        "Range": range_header,
    })
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            with open(target_path, "rb+") as f:
                f.seek(start)
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    with lock:
                        shared["downloaded"] += len(chunk)
        return True
    except Exception:
        return False


def _download_with_progress(url: str, target_path: str, desc: str = "") -> bool:
    """Single-thread download (fallback when multi-thread is not viable)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with urllib.request.urlopen(req, timeout=300) as resp:
            total = resp.length
            downloaded = 0
            bar_width = 30
            chunk_size = 8192
            os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
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
                        print(f"    {desc} {downloaded/1024/1024:.1f} MB...", end="\r", flush=True)
        print()
        return True
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"\n  {t('app.error', msg=f'Download failed: {e}')}")
        return False


def multithreaded_download(url: str, target_path: str, desc: str = "",
                           num_threads: int = _DEFAULT_DOWNLOAD_THREADS) -> bool:
    """
    Multi-threaded file download.

    Automatically detects if the server supports Range requests.
    Falls back to single-thread if not supported or file is too small.
    On chunk failure, retries with single-thread download.
    """
    # 先获取文件大小用于分块
    _, file_size = _check_range_support(url)

    if file_size < _MIN_CHUNK_SIZE or file_size <= 0:
        return _download_with_progress(url, target_path, desc)

    # 即使 HEAD 未报告 Range 支持，仍尝试多线程
    #（部分 CDN 对 HEAD/GET 响应不一致，实际 GET 支持 Range）
    actual_threads = min(num_threads, max(1, file_size // _MIN_CHUNK_SIZE))
    if actual_threads <= 1:
        return _download_with_progress(url, target_path, desc)

    chunk_size = file_size // actual_threads

    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    with open(target_path, "wb") as f:
        f.truncate(file_size)

    chunks: list[tuple[int, int]] = []
    for i in range(actual_threads):
        start = i * chunk_size
        end = start + chunk_size - 1 if i < actual_threads - 1 else file_size - 1
        chunks.append((start, end))

    shared: dict[str, Any] = {"downloaded": 0}
    lock = threading.Lock()
    bar_width = 30

    print(f"    {desc} ({actual_threads} threads)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=actual_threads) as executor:
        futures = [
            executor.submit(_download_chunk, url, start, end, target_path,
                            shared, lock)
            for start, end in chunks
        ]

        while not all(f.done() for f in futures):
            with lock:
                downloaded = shared["downloaded"]
            if file_size > 0:
                pct = downloaded / file_size
                filled = int(bar_width * pct)
                bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                print(f"    {desc} [{bar}] {pct * 100:5.1f}%  "
                      f"({downloaded/1024/1024:.1f}/{file_size/1024/1024:.1f} MB)",
                      end="\r", flush=True)
            else:
                print(f"    {desc} {downloaded/1024/1024:.1f} MB...",
                      end="\r", flush=True)
            time.sleep(0.15)

        failed_indices = [
            i for i, f in enumerate(futures)
            if f.exception() or f.result() is False
        ]
        if failed_indices:
            try:
                os.remove(target_path)
            except OSError:
                pass
            print(f"\n  {t('download.retry_fallback')}")
            return _download_with_progress(url, target_path, desc)

    final_bar = "\u2588" * bar_width
    print(f"    {desc} [{final_bar}] 100.0%  "
          f"({file_size/1024/1024:.1f}/{file_size/1024/1024:.1f} MB)")
    return True


def set_download_threads(count: int) -> None:
    """Set the global default download thread count."""
    global _DEFAULT_DOWNLOAD_THREADS
    if count < 1:
        count = 1
    _DEFAULT_DOWNLOAD_THREADS = count


# ---------------------------------------------------------------------------
# Interactive selection
# ---------------------------------------------------------------------------

def _interactive_pick_server_type(categorized: dict) -> Optional[str]:
    all_servers: list[tuple[str, str]] = []
    for cat, lst in categorized.items():
        label_key = _CATEGORY_LABELS.get(cat, cat)
        if not isinstance(lst, list):
            continue
        for sid in lst:
            if isinstance(sid, str):
                all_servers.append((sid, label_key))
    if not all_servers:
        print(t("app.error", msg="Server list is empty")); return None
    print()
    print(t("download.select_type"))
    print()
    current_cat = ""
    idx = 1
    index_map: list[tuple[int, str]] = []
    for sid, cat_key in all_servers:
        label = t(cat_key)
        if label != current_cat:
            print(f"  [{label}]")
            current_cat = label
        print(f"      [{idx}] {_server_display_name(sid)}")
        index_map.append((idx, sid))
        idx += 1
    print()
    while True:
        try:
            c = input(t("download.select_version_prompt", max=len(index_map))).strip()
            n = int(c)
            for i, sid in index_map:
                if i == n:
                    return sid
        except ValueError:
            pass
        print(t("app.invalid_choice_short"))


def _interactive_pick_version(versions: list[str], description: str) -> Optional[str]:
    if not versions:
        print(t("app.error", msg="No available versions")); return None
    print()
    if description:
        print(f"  {description}")
    print()
    print(f"  {len(versions)} versions available, default is latest:")
    print()
    stable = [v for v in versions if not any(x in v.lower() for x in ("pre", "rc", "snapshot", "alpha", "beta"))]
    show = stable[:20] if stable else versions[:20]
    for i, ver in enumerate(show, 1):
        print(f"  [{i}] {ver}{'  <- latest' if i == 1 else ''}")
    print(f"  [L] Download latest ({show[0]})")
    print()
    while True:
        c = input("  Enter number (1-{0} or L): ".format(len(show))).strip().lower()
        if c == "l":
            return show[0]
        try:
            idx = int(c) - 1
            if 0 <= idx < len(show):
                return show[idx]
        except ValueError:
            pass
        print(t("app.invalid_choice_short"))


def _prompt_post_download_config(server_name: str) -> dict:
    print()
    print(t("download.config_prompt"))
    print()
    ji = input(t("download.config_java")).strip()
    java_path = ji if ji else None
    while True:
        mi = input(t("download.config_min_mem", default="1G")).strip()
        if not mi:
            min_mem = "1G"; break
        if mi.upper().endswith(("G", "M")):
            min_mem = mi.upper(); break
        print(t("download.config_mem_invalid"))
    while True:
        ma = input(t("download.config_max_mem", default="4G")).strip()
        if not ma:
            max_mem = "4G"; break
        if ma.upper().endswith(("G", "M")):
            max_mem = ma.upper(); break
        print(t("download.config_mem_invalid"))
    return {"java_path": java_path, "min_mem": min_mem, "max_mem": max_mem}


def show_download_server_menu(servers_dir: str, project_dir: str) -> Optional[str]:
    """Interactive server download UI. Returns server directory or None."""
    print(); print(t("download.fetching_types")); print()
    categorized = msl_get_server_types(project_dir)
    if not categorized:
        print(t("app.error", msg="Failed to get server type list")); return None
    server_id = _interactive_pick_server_type(categorized)
    if server_id is None:
        return None

    print(t("download.fetching_versions", name=_server_display_name(server_id)))
    version_data = msl_get_versions(server_id, project_dir)
    if not version_data:
        print(t("app.error", msg="Failed to get version info")); return None
    selected_version = _interactive_pick_version(version_data.get("versions", []), version_data.get("description", ""))
    if selected_version is None:
        return None

    print(t("download.selected", name=_server_display_name(server_id), ver=selected_version))
    print(t("download.fetching_url"))
    download_info = msl_get_download_url(server_id, selected_version)
    if not download_info:
        print(t("download.url_failed")); return None
    url = download_info.get("url", "")
    if not url:
        print(t("download.url_empty")); return None

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

    print(t("download.downloading", name=_server_display_name(server_id), ver=selected_version))
    print(t("download.saving_to", path=jar_path))
    if not multithreaded_download(url, jar_path, desc=_server_display_name(server_id)):
        shutil.rmtree(server_dir, ignore_errors=True); return None
    if not os.path.isfile(jar_path) or os.path.getsize(jar_path) == 0:
        print(t("download.invalid_file"))
        shutil.rmtree(server_dir, ignore_errors=True); return None

    post_cfg = _prompt_post_download_config(server_name)
    save_server_config({
        "name": server_name, "mc_version": selected_version, "jar": jar_filename,
        "java_path": post_cfg["java_path"], "min_mem": post_cfg["min_mem"],
        "max_mem": post_cfg["max_mem"], "extra_jvm_args": [], "extra_server_args": [],
    }, server_dir)

    size_mb = os.path.getsize(jar_path) / 1024 / 1024
    print(t("download.complete", size=size_mb))
    if download_info.get("sha256"):
        print(t("download.sha256", hash=download_info["sha256"]))
    print(t("download.server_dir", dir=server_dir))
    return server_dir
