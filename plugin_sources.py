"""
插件搜索与下载模块

集成 Spiget (SpigotMC)、Modrinth、Hangar 三个插件源。
"""

import json
import os
import re
import shutil
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Optional

from i18n import t
from constants import SPIGET_BASE, USER_AGENT
from http_utils import fetch_json
from download_msl import safe_filename

# ---------------------------------------------------------------------------
# 通用结构
# ---------------------------------------------------------------------------

PluginResult = dict[str, Any]
"""标准化搜索结果：{source, id, name, author, description, downloads, tested_versions}"""

VersionResult = dict[str, Any]
"""标准化版本信息：{version, download_url, game_versions, loaders}"""


def _download_jar(url: str, target_path: str) -> bool:
    """下载 jar 文件到目标路径（使用多线程下载）。"""
    from download_msl import multithreaded_download
    return multithreaded_download(url, target_path, desc="Download")


# ---------------------------------------------------------------------------
# Spiget
# ---------------------------------------------------------------------------


def _spiget_author_label(author: Any) -> str:
    """Spiget 搜索结果里的 author 对象只有 id、没有名字（名字要另开
    /authors/{id} 查）。至少把 id 显示出来，比永远显示 "?" 有信息量。"""
    if isinstance(author, dict):
        name = author.get("name")
        if name:
            return str(name)
        if author.get("id"):
            return f"#{author['id']}"
    return "?"


def spiget_search(query: str, limit: int = 10) -> list[PluginResult]:
    """搜索 SpigotMC 插件。"""
    # 关键词必须转义后再拼进 URL。查询里只要有空格，http.client 就会抛
    # InvalidURL；有中文则抛 UnicodeEncodeError——多词搜索和中文搜索会直接
    # 崩在 urllib 里，被 search_all 的 except 吞掉，表现为「一个结果都没有」。
    safe_query = urllib.parse.quote(query, safe="")
    url = (f"{SPIGET_BASE}/search/resources/{safe_query}"
           f"?size={limit}&fields=id,name,tag,testedVersions,downloads,author")
    data = fetch_json(url)
    if not data or not isinstance(data, list):
        return []
    results: list[PluginResult] = []
    for item in data:
        tid = item.get("id")
        if not tid:
            continue
        tested = item.get("testedVersions") or []
        results.append({
            "source": "SpigotMC",
            "id": str(tid),
            "name": item.get("name", "?"),
            "author": _spiget_author_label(item.get("author")),
            "description": item.get("tag", ""),
            "downloads": item.get("downloads", 0),
            "tested_versions": [v for v in tested if isinstance(v, str)],
        })
    return results


def spiget_get_resource_details(resource_id: str) -> Optional[dict]:
    """获取 SpigotMC 资源详情，包含 external、file.externalUrl 等字段。"""
    url = f"{SPIGET_BASE}/resources/{resource_id}"
    data = fetch_json(url)
    if not data or not isinstance(data, dict):
        return None
    return data


def spiget_resolve_download_url(resource_id: str) -> Optional[str]:
    """跟随 Spiget 下载重定向拿到真实下载 URL。"""
    url = f"{SPIGET_BASE}/resources/{resource_id}/download"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://www.spigotmc.org/",
        })
        # 用 with 关掉响应：这里只取重定向后的地址，不读正文，
        # 不复用连接池的话每次搜索都会漏一个 socket
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.url  # 重定向后的真实地址
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        return None


def _spiget_download_external_github(external_url: str, target_dir: str,
                                     plugin_name: str) -> Optional[str]:
    """
    尝试从 GitHub releases 页面下载 jar 文件。
    支持两种 URL 格式：
      - https://github.com/{owner}/{repo}/releases/tag/{tag}
      - https://github.com/{owner}/{repo}/releases/download/{tag}/{file}
    返回 jar 文件名，失败返回 None。
    """
    from download_msl import multithreaded_download

    # 形如 .../releases/download/{tag}/{file} 就是 jar 直链，直接下即可。
    # 原实现只认 releases/tag/，把这种直链也当成「解析不了」推给用户手动下载。
    m = re.match(
        r"https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/(.+)",
        external_url)
    if m:
        raw_name = urllib.parse.unquote(m.group(4).split("?")[0])
        filename = safe_filename(raw_name, fallback="plugin.jar")
        target_path = os.path.join(target_dir, filename)
        print(t("github.downloading", name=filename))
        if multithreaded_download(external_url, target_path, desc="Download"):
            return filename
        return None

    # 尝试匹配 releases/tag/ 格式
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/releases/tag/(.+)", external_url)
    if m:
        owner, repo, tag = m.group(1), m.group(2), m.group(3)
        # 用 GitHub API 获取 release 信息
        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
        try:
            req = urllib.request.Request(
                api_url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        assets = data.get("assets") or []
        # 过滤 jar 文件，按大小排序（通常主插件最大）
        jars = [a for a in assets if a.get("name", "").endswith(".jar")]
        if not jars:
            return None
        jars.sort(key=lambda a: -(a.get("size", 0) or 0))

        # 有多个 jar 时让用户选择
        selected = jars[0]
        if len(jars) > 1:
            print()
            print(t("github.found_jars", count=len(jars)))
            print()
            for i, a in enumerate(jars, 1):
                size_mb = (a.get("size", 0) or 0) / 1024 / 1024
                print(f"  [{i}] {a['name']}  ({size_mb:.1f} MB)")
            print()
            while True:
                try:
                    c = input(t("github.select_jar", max=len(jars))).strip()
                    idx = int(c) - 1
                    if 0 <= idx < len(jars):
                        selected = jars[idx]
                        break
                except ValueError:
                    pass
                print(t("app.invalid_choice_short"))

        dl_url = selected.get("browser_download_url", "")
        if not dl_url:
            return None
        # release 资源名同样来自远端，落盘前先收敛
        filename = safe_filename(selected["name"], fallback="plugin.jar")
        target_path = os.path.join(target_dir, filename)

        print(t("github.downloading", name=filename))
        if multithreaded_download(dl_url, target_path, desc="Download"):
            return filename
        return None

    return None


def _spiget_download_external(target_dir: str, result: PluginResult) -> Optional[str]:
    """处理外部托管的 SpigotMC 插件下载。"""
    pid = result["id"]
    details = spiget_get_resource_details(pid)
    if not details:
        print(t("github.failed_metadata"))
        return None

    external_url = (details.get("file") or {}).get("externalUrl", "")
    if not external_url:
        print(t("github.empty_url"))
        return None

    # 尝试 GitHub 下载
    if external_url.startswith("https://github.com/"):
        filename = _spiget_download_external_github(external_url, target_dir,
                                                     result.get("name", "plugin"))
        if filename:
            return filename
        print(t("github.hint_manual"))
        print(f"         {external_url}")
        return None

    # 未知外部源，告诉用户手动下载
    print(t("github.hint_unknown"))
    print(f"         {external_url}")
    return None


# ---------------------------------------------------------------------------
# Modrinth
# ---------------------------------------------------------------------------

MODRINTH_BASE = "https://api.modrinth.com/v2"


def modrinth_search(query: str, limit: int = 10) -> list[PluginResult]:
    """搜索 Modrinth 插件。使用 facets 过滤 project_type:plugin"""
    # facets 是一段 JSON，而且必须是「数组的数组」：[["project_type:plugin"]]。
    # 原来拼的是 {"project_type:plugin"}，那连合法 JSON 都不是，Modrinth 会
    # 直接报错，于是插件搜索里的 Modrinth 这一路永远是空的。
    facets = urllib.parse.quote(json.dumps([["project_type:plugin"]]), safe="")
    safe_query = urllib.parse.quote(query, safe="")
    url = (f"{MODRINTH_BASE}/search?query={safe_query}&limit={limit}"
           f"&facets={facets}")
    data = fetch_json(url)
    if not data or not isinstance(data, dict):
        return []
    hits = data.get("hits") or []
    results: list[PluginResult] = []
    for item in hits:
        pid = item.get("project_id")
        if not pid:
            continue
        results.append({
            "source": "Modrinth",
            "id": pid,
            "name": item.get("title", "?"),
            "author": item.get("author", "?"),
            "description": item.get("description", ""),
            "downloads": item.get("downloads", 0),
            "tested_versions": item.get("versions") or [],
            "categories": item.get("categories") or [],
        })
    return results


def modrinth_get_versions(project_id: str) -> list[dict]:
    """获取 Modrinth 项目的所有版本。"""
    url = f"{MODRINTH_BASE}/project/{project_id}/version"
    data = fetch_json(url)
    if not data or not isinstance(data, list):
        return []
    return data


def modrinth_get_latest_download(project_id: str) -> Optional[tuple[str, str, str]]:
    """
    获取 Modrinth 项目最新版本的下载信息。
    返回 (version_name, download_url, game_version_summary) 或 None。
    """
    versions = modrinth_get_versions(project_id)
    if not versions:
        return None
    # 取第一个（最新的）
    v = versions[0]
    version_name = v.get("name") or v.get("version_number", "?")
    game_versions = v.get("game_versions") or []
    files = v.get("files") or []
    # 优先取 primary 文件，否则可能下到 sources/javadoc
    ordered = [f for f in files if f.get("primary")] or files
    for f in ordered:
        url = f.get("url")
        if url:
            summary = ", ".join(game_versions[:3])
            if len(game_versions) > 3:
                summary += f" ... (+{len(game_versions) - 3})"
            return version_name, url, summary
    return None


# ---------------------------------------------------------------------------
# Hangar
# ---------------------------------------------------------------------------

HANGAR_BASE = "https://hangar.papermc.io/api/v1"


def hangar_search(query: str, limit: int = 10) -> list[PluginResult]:
    """搜索 Hangar 插件。"""
    safe_query = urllib.parse.quote(query, safe="")
    url = f"{HANGAR_BASE}/projects?q={safe_query}&limit={limit}&sort=-stars"
    data = fetch_json(url)
    if not data or not isinstance(data, dict):
        return []
    result_list = data.get("result") or []
    results: list[PluginResult] = []
    for item in result_list:
        namespace = item.get("namespace") or {}
        owner = namespace.get("owner", "?")
        slug = namespace.get("slug", "")
        results.append({
            "source": "Hangar",
            "id": f"{owner}/{slug}",
            "name": item.get("name", "?"),
            "author": owner,
            "description": item.get("description", ""),
            "downloads": item.get("stats", {}).get("downloads", 0),
            "tested_versions": [],
            "slug": slug,
            "owner": owner,
        })
    return results


def hangar_get_download(author: str, slug: str) -> Optional[tuple[str, str]]:
    """获取 Hangar 项目最新版本下载 URL。
    返回 (version_name, download_url) 或 None。
    """
    url = f"{HANGAR_BASE}/projects/{author}/{slug}/versions?limit=1&offset=0"
    data = fetch_json(url)
    if not data or not isinstance(data, dict):
        return None
    versions = data.get("result") or []
    if not versions:
        return None
    v = versions[0]
    version_name = v.get("name", "?")
    downloads = v.get("downloads") or {}
    # Hangar 按平台分（PAPER, WATERFALL, VELOCITY）
    for platform in ("PAPER", "VELOCITY", "WATERFALL"):
        info = downloads.get(platform) if isinstance(downloads, dict) else None
        if isinstance(info, dict):
            dl_url = info.get("downloadUrl") or info.get("externalUrl")
            if dl_url:
                return version_name, dl_url
    return None


# ---------------------------------------------------------------------------
# 统一搜索
# ---------------------------------------------------------------------------

SEARCH_SOURCES = {
    "1": ("SpigotMC", spiget_search, spiget_resolve_download_url),
    "2": ("Modrinth", modrinth_search, None),
    "3": ("Hangar", hangar_search, hangar_get_download),
}


def search_all(query: str, limit: int = 5) -> dict[str, list[PluginResult]]:
    """同时在三个源搜索，返回 {source_name: [results]}。"""
    results = {}
    for src_name, search_fn, _ in [SEARCH_SOURCES[k] for k in ("1", "2", "3")]:
        try:
            items = search_fn(query, limit)
            if items:
                results[src_name] = items
        except Exception as e:  # noqa: BLE001
            # 见 mod_sources 里的同一处：不吭声的话，坏掉的源与「没搜到」
            # 无法区分，真实 bug 会被当成「这个网站没有这个插件」
            print(t("search.source_failed", source=src_name,
                    msg=f"{type(e).__name__}: {e}"))
    return results


def download_plugin(result: PluginResult, target_dir: str) -> Optional[str]:
    """
    根据搜索结果下载插件到目标目录。
    返回 jar 文件名，失败返回 None。
    """
    source = result["source"]
    pid = result["id"]
    target_path = ""

    try:
        if source == "SpigotMC":
            # 先检查资源是否为外部托管
            details = spiget_get_resource_details(pid)
            is_external = details and details.get("external") is True
            if is_external:
                return _spiget_download_external(target_dir, result)

            # 注意函数名是 spiget_ 不是 spigot_：写错的话 NameError 会被下面
            # 的 except Exception 吞掉，表现成「所有 SpigotMC 插件都下载失败」
            real_url = spiget_resolve_download_url(pid)
            if not real_url:
                return None
            filename = safe_filename(
                os.path.basename(real_url.split("?")[0]), fallback="")
            if not filename.endswith(".jar"):
                filename = safe_filename(f"{result['name']}.jar", fallback="plugin.jar")
            target_path = os.path.join(target_dir, filename)
            if _download_jar(real_url, target_path):
                return filename

        elif source == "Modrinth":
            info = modrinth_get_latest_download(pid)
            if not info:
                return None
            ver_name, dl_url, _ = info
            filename = safe_filename(f"{result['name']}-{ver_name}.jar",
                                     fallback="plugin.jar")
            target_path = os.path.join(target_dir, filename)
            if _download_jar(dl_url, target_path):
                return filename

        elif source == "Hangar":
            author = result.get("owner", "")
            slug = result.get("slug", "")
            info = hangar_get_download(author, slug)
            if not info:
                return None
            ver_name, dl_url = info
            filename = safe_filename(f"{result['name']}-{ver_name}.jar",
                                     fallback="plugin.jar")
            target_path = os.path.join(target_dir, filename)
            if _download_jar(dl_url, target_path):
                return filename

    except Exception as e:  # noqa: BLE001
        # 这里以前是 `pass`。调用方只看到「下载失败」，而真正的原因
        # （比如调了个不存在的函数导致 NameError）被吞得一干二净——
        # SpigotMC 的插件下载就是这么一直失败、又一直查不出所以然。
        print(t("download.source_failed", source=source,
                msg=f"{type(e).__name__}: {e}"))

    return None