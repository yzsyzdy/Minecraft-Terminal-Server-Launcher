"""
MSL API V4 server download module

Downloads Minecraft server jars via MSL mirror API.
API endpoint: https://api.mslmc.cn/v4
"""

import os
import json
import re
import shutil
import hashlib
import threading
import concurrent.futures
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Any, Optional

from server_manager import save_server_config
from http_utils import open_url
from i18n import t
from constants import MSL_API_BASE, USER_AGENT

MSL_USER_AGENT = USER_AGENT

# Windows 文件名里不允许出现的字符，外加控制字符
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 保留设备名，撞上了就写不出文件
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_filename(name: str, fallback: str = "download", max_length: int = 120) -> str:
    """把远端提供的名字收敛成一个安全的文件名。

    Modrinth / CurseForge / SpigotMC 的项目标题是远端数据，可能带 / 或 \\
    甚至 ..，直接拼进 os.path.join 会把文件写到目标目录外面；Windows 上还有
    一批字符根本不能出现在文件名里。
    """
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", str(name or ""))
    cleaned = os.path.basename(cleaned.replace("\\", "/")).strip(" .")
    if not cleaned or cleaned in (".", ".."):
        return fallback
    if cleaned.split(".")[0].lower() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    if len(cleaned) > max_length:
        stem, ext = os.path.splitext(cleaned)
        cleaned = stem[: max(1, max_length - len(ext))] + ext
    return cleaned or fallback

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
    # 先写临时文件再 os.replace：open(p, "w") 是就地截断，写到一半崩掉会留下
    # 半截 JSON，下次读缓存时 json.JSONDecodeError 直接让当天所有接口回退成
    # 「没有数据」。
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _msl_cache_prune(cache: dict) -> dict:
    """清理超过 7 天的缓存条目。"""
    today = _today_str()
    pruned = {}
    for key, entry in cache.items():
        if isinstance(entry, dict) and entry.get("cached_at") == today:
            pruned[key] = entry
        elif isinstance(entry, dict) and entry.get("cached_at", "").startswith("20"):
            # 保留当天和最近 7 天的缓存，超出则丢弃
            try:
                from datetime import datetime, timedelta
                cache_date = datetime.strptime(entry["cached_at"], "%Y-%m-%d")
                cached_today = datetime.strptime(today, "%Y-%m-%d")
                if (cached_today - cache_date).days <= 7:
                    pruned[key] = entry
            except (ValueError, KeyError):
                pass
    return pruned


def _msl_cache_fetch(cache_key: str, path: str, project_dir: str) -> Any:
    cache = _msl_load_cache(project_dir)
    entry = cache.get(cache_key)
    today = _today_str()
    if entry and isinstance(entry, dict) and entry.get("cached_at") == today:
        cached = entry.get("data")
        # 只有真正拿到数据才认命中。MSL 偶尔会回 200 + {"code":200,"data":null}，
        # 旧版本会把这个 null 连当天日期一起写进缓存，于是接下来一整天每次调用
        # 都命中这条空缓存、连网络请求都不发，服务端类型/版本列表全空——而且
        # 落盘了，重启进程也救不回来，只能等 UTC 午夜。空条目当未命中重取。
        if cached is not None:
            print(t("cache.hit", date=today))
            return cached

    url = f"{MSL_API_BASE}{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with open_url(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(t("app.error", msg=f"MSL API HTTP {e.code}: {e.reason}")); return None
    except urllib.error.URLError as e:
        print(t("app.error", msg=f"Network error: {e.reason}")); return None
    except (json.JSONDecodeError, OSError) as e:
        print(t("app.error", msg=f"Response parse failed: {e}")); return None

    if not isinstance(data, dict) or data.get("code") != 200:
        # data 不是 dict 时不能走 data.get：API 一旦返回 JSON 数组/字符串
        # （网关错误页也能是合法 JSON），这里会抛 AttributeError 而不是给出提示
        message = data.get("message", "Abnormal") if isinstance(data, dict) else "Unexpected API response"
        print(t("app.error", msg=message)); return None
    result = data.get("data")
    if result is not None:
        cache[cache_key] = {"cached_at": today, "data": result}
        cache = _msl_cache_prune(cache)
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
        message = data.get("message", "Abnormal") if isinstance(data, dict) else "Unexpected API response"
        print(t("app.error", msg=message)); return None
    return data.get("data")


def msl_get_server_types(project_dir: str = "") -> Optional[dict]:
    """获取 MSL 镜像支持的所有服务端类型列表。

    按类型分类: pluginsCore / modsCore / vanillaCore / proxyCore / bedrockCore。
    有 project_dir 时会走每日缓存，避免频繁请求（MSL API 有限速:
    30次/小时, 60次/天）。
    """
    return _msl_cache_fetch("server_types", "/mirrors", project_dir) if project_dir else _msl_request("/mirrors")


def msl_get_versions(server_type: str, project_dir: str = "") -> Optional[dict]:
    """获取指定服务端类型的可用版本列表。

    版本列表会过滤掉 pre/rc/snapshot 等测试版，只显示稳定版。
    """
    key = f"versions_{server_type}"
    if project_dir:
        data = _msl_cache_fetch(key, f"/mirrors/{server_type}",
                                project_dir)
    else:
        data = _msl_request(f"/mirrors/{server_type}")
    return data


def msl_get_download_url(server_type: str, version: str) -> Optional[dict]:
    """获取指定服务端和版本的下载地址。

    注意: 下载 URL 不缓存，因为 MSL API 的限速按请求计（非按响应缓存）。
    返回的 dict 里目前稳定提供 url 与 sha256；size 字段在实测响应中并不存在
    （v4 /download/server/paper/1.21.8 只回了这两个键），所以不要依赖它，
    需要文件大小时用 HEAD 探测。
    """
    return _msl_request(f"/download/server/{server_type}/{version}")


# ---------------------------------------------------------------------------
# Multi-threaded download
# ---------------------------------------------------------------------------

_DEFAULT_DOWNLOAD_THREADS = 16
_MIN_CHUNK_SIZE = 1 * 1024 * 1024
# 线程数上限。分片大小固定 1MiB，4GiB 的文件能算出 4096 个分片，若照单全收
# 就是 4096 个线程 —— ThreadPoolExecutor 会直接 "can't start new thread"。
# 64 对家用带宽已经远超收益，再往上只是让服务器和本机都更难受。
MAX_DOWNLOAD_THREADS = 64
# 单个分片失败后单独重试的次数，用完才退回单线程整文件重下
_CHUNK_RETRIES = 3


def file_sha256(path: str) -> str:
    """流式算文件的 sha256，不把整个文件读进内存。"""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_sha256(path: str, expected: str, quiet: bool = False) -> bool:
    """校验下载结果的 sha256，不匹配就删掉文件并返回 False。

    MSL 的 /download/server/{type}/{version} 响应里带着 sha256，而这是它唯一
    提供的完整性信息（没有 size 字段）。以前这个值只被打印出来给用户看，从不
    参与判断，于是截断、CDN 损坏、在途篡改都要等服务器启动失败才暴露。
    """
    expected = str(expected or "").strip().lower()
    if not expected:
        return True
    try:
        actual = file_sha256(path)
    except OSError as e:
        if not quiet:
            print(f"\n  {t('app.error', msg=f'Cannot hash downloaded file: {e}')}")
        return False
    if actual == expected:
        return True
    try:
        os.remove(path)
    except OSError:
        pass
    if not quiet:
        print(f"\n  {t('download.sha256_mismatch', expected=expected, actual=actual)}")
    return False


def _check_range_support(url: str, headers: Optional[dict] = None) -> tuple[bool, int]:
    """Check if server supports Range requests. Returns (supported?, file_size)."""
    req_headers = {"User-Agent": MSL_USER_AGENT}
    if headers:
        req_headers.update(headers)
    try:
        req = urllib.request.Request(url, method="HEAD", headers=req_headers)
        with open_url(req, timeout=30) as resp:
            accept_ranges = resp.headers.get("Accept-Ranges", "")
            content_length = resp.headers.get("Content-Length")
            size = int(content_length) if content_length else 0
            return "bytes" in accept_ranges, size
    except Exception:
        return False, 0


def _download_chunk(url: str, start: int, end: int, target_path: str,
                    shared: dict, lock: threading.Lock,
                    headers: Optional[dict] = None) -> bool:
    """Download a chunk and write to the target file at the correct offset."""
    range_header = f"bytes={start}-{end}"
    expected = end - start + 1
    req_headers = {"User-Agent": MSL_USER_AGENT, "Range": range_header}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with open_url(req, timeout=300) as resp:
            # 必须确认服务器真的按 Range 回了 206。某些 CDN 会照样回 200 +
            # 整个文件，那时每个线程都会把自己的 offset 当成文件开头，最终
            # 拼出一个长度对得上、内容全错的文件，而且不会报任何错。
            if getattr(resp, "status", None) != 206:
                return False
            written = 0
            with open(target_path, "rb+") as f:
                f.seek(start)
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    written += len(chunk)
                    # 按分片分别记账：重试某个分片时要能把它上一次已经写进去
                    # 的字节数扣掉，否则进度会重复累加、冲到 100% 以上。
                    with lock:
                        shared["downloaded"] += len(chunk)
                        shared["chunks"][start] = \
                            shared["chunks"].get(start, 0) + len(chunk)
        return written == expected
    except Exception:
        return False


def _forget_chunk_progress(shared: dict, lock: threading.Lock, start: int) -> None:
    """把某个分片之前记进进度里的字节数扣掉（重试前调用）。"""
    with lock:
        previous = shared["chunks"].pop(start, 0)
        if previous:
            shared["downloaded"] = max(0, shared["downloaded"] - previous)


def download_file_with_progress(url: str, target_path: str, desc: str = "",
                                headers: Optional[dict] = None,
                                quiet: bool = False,
                                on_progress=None,
                                expected_sha256: Optional[str] = None) -> bool:
    """单线程文件下载（带进度条）。

    Args:
        url: 下载 URL
        target_path: 本地保存路径
        desc: 进度条描述文字
        headers: 自定义请求头（可选）
        quiet: 不往 stdout 画进度，供不需要终端的调用方使用
        on_progress: 进度回调 on_progress(ratio, downloaded, total)，
                     供网页面板把进度推给浏览器
        expected_sha256: 期望的 sha256，给了就校验，不匹配则删除文件并失败

    Returns:
        成功返回 True，失败返回 False
    """
    req_headers = {"User-Agent": MSL_USER_AGENT}
    if headers:
        req_headers.update(headers)
    try:
        req = urllib.request.Request(url, headers=req_headers)
        with open_url(req, timeout=300) as resp:
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
                    if on_progress is not None and total:
                        try:
                            on_progress(min(1.0, downloaded / total),
                                        downloaded, total)
                        except Exception:  # noqa: BLE001
                            pass
                    if quiet:
                        continue
                    if total and total > 0:
                        pct = min(1.0, downloaded / total)
                        filled = int(bar_width * pct)
                        bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                        print(f"    {desc} [{bar}] {pct * 100:5.1f}%", end="\r", flush=True)
                    else:
                        print(f"    {desc} {downloaded/1024/1024:.1f} MB...", end="\r", flush=True)
        # 服务器声明了 Content-Length 却提前收尾时，http.client 把短 body 当成
        # 正常 EOF，不抛任何异常——于是这里会安静地留下一个截断的文件，上层看到
        # True 就当安装成功。必须自己比对字节数。
        if total is not None and downloaded != total:
            try:
                os.remove(target_path)
            except OSError:
                pass
            if not quiet:
                print(f"\n  {t('download.truncated', got=downloaded, want=total)}")
            return False
        if expected_sha256 and not _verify_sha256(target_path, expected_sha256, quiet):
            return False
        if not quiet:
            print()
        return True
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        if not quiet:
            print(f"\n  {t('app.error', msg=f'Download failed: {e}')}")
        return False


# 向后兼容别名
_download_with_progress = download_file_with_progress


def multithreaded_download(url: str, target_path: str, desc: str = "",
                           num_threads: Optional[int] = None,
                           headers: Optional[dict] = None,
                           quiet: bool = False,
                           on_progress=None,
                           expected_sha256: Optional[str] = None) -> bool:
    """
    Multi-threaded file download.

    Automatically detects if the server supports Range requests.
    Falls back to single-thread if not supported or file is too small.
    On chunk failure, retries the failed chunks alone before falling back.

    headers 会带给探测、分片和单线程回退三条路径。CurseForge 从 2026-07-16
    起要求下载也带 x-api-key，缺了它整个下载路径都只会拿到 401。
    quiet 供网页面板这类没有终端的调用方使用；on_progress 同理，用来把
    进度推给浏览器。
    expected_sha256 是完整性校验值，会一路传到单线程回退路径，保证任何一条
    分支下载完都会验一次。

    num_threads 默认 None = 「用当前设置」。以前这里写的是
    `num_threads: int = _DEFAULT_DOWNLOAD_THREADS`，默认值在函数定义时就被
    固定成 16 了，之后再调 set_download_threads 也改不动它——菜单里设置
    下载线程数会提示「已保存」，实际永远是 16。
    """
    if num_threads is None:
        num_threads = _DEFAULT_DOWNLOAD_THREADS

    def say(message: str, end: str = "\n") -> None:
        if not quiet:
            print(message, end=end, flush=True)

    def report(ratio: float, downloaded: int, total: int) -> None:
        if on_progress is None:
            return
        try:
            on_progress(ratio, downloaded, total)
        except Exception:  # noqa: BLE001
            pass

    # 检查服务器是否支持 Range 请求，同时获取文件大小
    range_supported, file_size = _check_range_support(url, headers)

    if file_size < _MIN_CHUNK_SIZE or file_size <= 0:
        return download_file_with_progress(url, target_path, desc, headers,
                                           quiet, on_progress, expected_sha256)

    # 如果服务器明确不支持 Range 请求，直接使用单线程下载
    if not range_supported:
        return download_file_with_progress(url, target_path, desc, headers,
                                           quiet, on_progress, expected_sha256)

    # 这里再夹一次上限：set_download_threads 会挡，但 num_threads 是显式传进来的
    # 参数，调用方可以绕过设置直接给个大数。
    actual_threads = min(num_threads, MAX_DOWNLOAD_THREADS,
                         max(1, file_size // _MIN_CHUNK_SIZE))
    if actual_threads <= 1:
        return download_file_with_progress(url, target_path, desc, headers,
                                           quiet, on_progress, expected_sha256)

    chunk_size = file_size // actual_threads

    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    with open(target_path, "wb") as f:
        f.truncate(file_size)

    chunks: list[tuple[int, int]] = []
    for i in range(actual_threads):
        start = i * chunk_size
        end = start + chunk_size - 1 if i < actual_threads - 1 else file_size - 1
        chunks.append((start, end))

    # chunks 记录每个分片已写入的字节数，重试某个分片时才能把它上一次的
    # 残留计入量扣掉（分片是先写文件、再校验长度，失败的写入也占了计数）。
    shared: dict[str, Any] = {"downloaded": 0, "chunks": {}}
    lock = threading.Lock()
    bar_width = 30

    say(f"    {desc} ({actual_threads} threads)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=actual_threads) as executor:
        futures = [
            executor.submit(_download_chunk, url, start, end, target_path,
                            shared, lock, headers)
            for start, end in chunks
        ]

        # 用 wait(timeout=...) 等「至少有一个完成」，不要 while + sleep 空转
        pending = set(futures)
        while pending:
            _done, pending = concurrent.futures.wait(pending, timeout=0.15)
            with lock:
                downloaded = shared["downloaded"]
            if file_size > 0:
                report(min(1.0, downloaded / file_size), downloaded, file_size)
            if quiet:
                continue
            if file_size > 0:
                pct = min(1.0, downloaded / file_size)
                filled = int(bar_width * pct)
                bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                print(f"    {desc} [{bar}] {pct * 100:5.1f}%  "
                      f"({downloaded/1024/1024:.1f}/{file_size/1024/1024:.1f} MB)",
                      end="\r", flush=True)
            else:
                print(f"    {desc} {downloaded/1024/1024:.1f} MB...",
                      end="\r", flush=True)

        failed_indices = [
            i for i, f in enumerate(futures)
            if f.exception() or f.result() is False
        ]

        # 只重试失败的那几个分片。以前一有分片失败就删掉整个文件、串行重下，
        # 一个分片抖动就扔掉已经下好的几十上百 MB，用户只看到一句
        # 「回退到单线程下载」。
        for i in list(failed_indices):
            start, end = chunks[i]
            for attempt in range(1, _CHUNK_RETRIES + 1):
                _forget_chunk_progress(shared, lock, start)
                # 别把这条 t(...) 直接塞进 f-string 的花括号里跨行写：3.12 之前
                # 的 f-string 是单个字符串 token，花括号里换行就是语法错误
                # （3.12 的 PEP 701 才允许）。CI 的打包解释器正是 3.11。
                message = t('download.chunk_retry', index=i + 1,
                            attempt=attempt, total=_CHUNK_RETRIES)
                say(f"  {message}")
                if _download_chunk(url, start, end, target_path,
                                   shared, lock, headers):
                    failed_indices.remove(i)
                    with lock:
                        downloaded = shared["downloaded"]
                    report(min(1.0, downloaded / file_size), downloaded, file_size)
                    break

        if failed_indices:
            try:
                os.remove(target_path)
            except OSError:
                pass
            # 整文件重下，进度必须从 0 重新算；否则回调会把上一次的字节数
            # 继续加上去，进度条直接冲到 100% 以上再跳回 0。
            with lock:
                shared["downloaded"] = 0
            report(0.0, 0, file_size)
            say(f"\n  {t('download.retry_fallback')}")
            return download_file_with_progress(url, target_path, desc, headers,
                                               quiet, on_progress,
                                               expected_sha256)

    if expected_sha256 and not _verify_sha256(target_path, expected_sha256, quiet):
        return False
    final_bar = "\u2588" * bar_width
    say(f"    {desc} [{final_bar}] 100.0%  "
        f"({file_size/1024/1024:.1f}/{file_size/1024/1024:.1f} MB)")
    return True


def set_download_threads(count: int) -> None:
    """Set the global default download thread count."""
    global _DEFAULT_DOWNLOAD_THREADS
    if count < 1:
        count = 1
    elif count > MAX_DOWNLOAD_THREADS:
        count = MAX_DOWNLOAD_THREADS
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
    if not multithreaded_download(url, jar_path, desc=_server_display_name(server_id),
                                  expected_sha256=download_info.get("sha256")):
        shutil.rmtree(server_dir, ignore_errors=True); return None
    if not os.path.isfile(jar_path) or os.path.getsize(jar_path) == 0:
        print(t("download.invalid_file"))
        shutil.rmtree(server_dir, ignore_errors=True); return None

    post_cfg = _prompt_post_download_config(server_name)
    save_server_config({
        "name": server_name, "mc_version": selected_version, "jar": jar_filename,
        "java_path": post_cfg["java_path"], "min_mem": post_cfg["min_mem"],
        "max_mem": post_cfg["max_mem"], "extra_jvm_args": [], "extra_server_args": [],
        "process_priority": "normal",
    }, server_dir)

    size_mb = os.path.getsize(jar_path) / 1024 / 1024
    print(t("download.complete", size=size_mb))
    if download_info.get("sha256"):
        print(t("download.sha256", hash=download_info["sha256"]))
    print(t("download.server_dir", dir=server_dir))
    return server_dir
