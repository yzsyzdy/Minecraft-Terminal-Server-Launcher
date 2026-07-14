"""
模组搜索与下载模块

集成 Modrinth、CurseForge 两个模组源。
搜索时自动按 MC 版本和模组加载器过滤。
"""

import json
import os
import urllib.request
import urllib.parse
import urllib.error
import urllib.error
from typing import Any, Optional

from constants import USER_AGENT, MODRINTH_BASE, CURSEFORGE_OFFICIAL, CURSEFORGE_PROXY

API_TIMEOUT = 15

# 模组加载器名称 <-> CurseForge 枚举值
_CURSE_LOADER_MAP: dict[str, int] = {
    "forge": 1,
    "cauldron": 2,
    "liteloader": 3,
    "fabric": 4,
    "quilt": 5,
    "neoforge": 6,
}

_MOD_LOADER_NAMES: set[str] = {"fabric", "forge", "neoforge", "quilt"}

# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

_curseforge_api_key: str = ""


def set_curseforge_api_key(key: str) -> None:
    """设置 CurseForge API key。有 key 时自动切换官方 API，无 key 时使用 curse.tools 代理。"""
    global _curseforge_api_key, CURSEFORGE_BASE
    _curseforge_api_key = key
    CURSEFORGE_BASE = "https://api.curseforge.com/v1" if key else "https://api.curse.tools/v1/cf"


def extract_mod_loader(name: str) -> str:
    """从服务器名称中提取模组加载器。"""
    parts = name.lower().replace("_", "-").split("-")
    for part in parts:
        if part in _MOD_LOADER_NAMES:
            return part
    return "fabric"  # 默认


def _fetch_json(url: str, headers: Optional[dict] = None, timeout: int = API_TIMEOUT) -> Optional[Any]:
    """GET 请求返回 JSON。"""
    req_hdrs = {"User-Agent": USER_AGENT}
    if headers:
        req_hdrs.update(headers)
    try:
        req = urllib.request.Request(url, headers=req_hdrs)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Modrinth
# ---------------------------------------------------------------------------


def modrinth_search(query: str, mc_version: str = "", loader: str = "",
                    limit: int = 20) -> list[dict]:
    """
    搜索 Modrinth 模组。

    支持按 MC 版本和模组加载器过滤。
    返回列表，每个元素为标准化结果 dict。
    """
    # 构建 facets
    facets_list = [["project_type:mod"]]
    if mc_version:
        facets_list.append([f"versions:{mc_version}"])
    if loader:
        facets_list.append([f"categories:{loader}"])

    facets_json = json.dumps(facets_list, separators=(",", ":"))
    encoded_query = urllib.parse.quote(query, safe="")
    encoded_facets = urllib.parse.quote(facets_json, safe="")
    url = (f"{MODRINTH_BASE}/search?query={encoded_query}&limit={limit}"
           f"&facets={encoded_facets}")

    data = _fetch_json(url)
    if not data or not isinstance(data, dict):
        return []
    hits = data.get("hits") or []
    results: list[dict] = []
    for item in hits:
        pid = item.get("project_id")
        if not pid:
            continue
        results.append({
            "source": "Modrinth",
            "id": pid,
            "name": item.get("title", "?"),
            "author": item.get("author", "?"),
            "description": (item.get("description") or "")[:120],
            "downloads": item.get("downloads", 0),
            "mc_versions": item.get("versions") or [],
            "categories": item.get("categories") or [],
            "icon_url": item.get("icon_url", ""),
        })
    return results


def modrinth_get_suitable_version(slug: str, mc_version: str,
                                  loader: str) -> Optional[tuple[str, str, str]]:
    """
    获取模组匹配指定 MC 版本+加载器的最新版本。
    返回 (version_id, download_url, version_name) 或 None。
    使用 /v2/project/{slug}/version 接口（同时支持 slug 和 UUID）。
    """
    url = f"{MODRINTH_BASE}/project/{urllib.parse.quote(slug, safe='')}/version"
    data = _fetch_json(url)
    if not data or not isinstance(data, list):
        return None

    # 按 date_published 降序
    data.sort(key=lambda v: v.get("date_published", ""), reverse=True)

    for version in data:
        # 检查版本兼容性
        game_versions = version.get("game_versions") or []
        loaders = set(version.get("loaders") or [])

        if mc_version and mc_version not in game_versions:
            continue
        if loader and loader not in loaders:
            continue

        files = version.get("files") or []
        for f in files:
            dl_url = f.get("url")
            if dl_url:
                vname = version.get("name") or version.get("version_number", "?")
                return version.get("id"), dl_url, vname

    return None


def modrinth_download(project_id: str, target_dir: str, name: str,
                      mc_version: str, loader: str) -> Optional[str]:
    """
    从 Modrinth 下载模组。
    返回 jar 文件名，失败返回 None。
    """
    from download_msl import multithreaded_download

    from i18n import t

    info = modrinth_get_suitable_version(project_id, mc_version, loader)
    if not info:
        print(t("mod.no_version_match"))
        return None
    _, dl_url, ver_name = info

    filename = f"{name}-{ver_name}.jar"
    # 清理文件名中不可用于路径的字符
    filename = "".join(c if c.isalnum() or c in "._-+" else "_" for c in filename)
    target_path = os.path.join(target_dir, filename)

    if multithreaded_download(dl_url, target_path, desc=f"Mod: {name}"):
        return filename
    return None


# ---------------------------------------------------------------------------
# CurseForge
# ---------------------------------------------------------------------------

CURSEFORGE_BASE = "https://api.curse.tools/v1/cf"  # 默认使用免费代理，有 API key 时自动切换

# Minecraft 模组的 classId
CURSE_CLASS_MOD = 6


def _curse_headers() -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if _curseforge_api_key:
        headers["x-api-key"] = _curseforge_api_key
    return headers


def curseforge_search(query: str, mc_version: str = "", loader: str = "",
                      limit: int = 20) -> list[dict]:
    """
    搜索 CurseForge 模组。通过 curse.tools 代理（无需 API key）或官方 API（需配置 key）。
    支持按 MC 版本和模组加载器过滤。
    返回列表，每个元素为标准化结果 dict。
    """

    params = [
        f"gameId=432",
        f"classId={CURSE_CLASS_MOD}",
        f"searchFilter={query}",
        f"pageSize={min(limit, 50)}",
        f"sortField=2",  # 下载量排序
        f"sortOrder=desc",
    ]
    if mc_version:
        params.append(f"gameVersion={mc_version}")
    if loader:
        curse_loader = _CURSE_LOADER_MAP.get(loader, 0)
        if curse_loader:
            params.append(f"modLoaderType={curse_loader}")

    url = f"{CURSEFORGE_BASE}/mods/search?{'&'.join(params)}"
    data = _fetch_json(url, headers=_curse_headers(), timeout=API_TIMEOUT)
    if not data or not isinstance(data, dict):
        return []
    items = data.get("data") or []
    results: list[dict] = []
    for item in items:
        mod_id = item.get("id")
        if not mod_id:
            continue
        # 取最新文件的信息
        latest_files = item.get("latestFiles") or []
        download_count = None
        for f in latest_files:
            if f.get("isServerPack"):
                download_count = f.get("downloadCount")
                break
        results.append({
            "source": "CurseForge",
            "id": str(mod_id),
            "name": item.get("name", "?"),
            "author": (item.get("authors") or [{}])[0].get("name", "?") if item.get("authors") else "?",
            "description": (item.get("summary") or "")[:120],
            "downloads": download_count or item.get("downloadCount", 0),
            "slug": item.get("slug", ""),
        })
    return results


def curseforge_get_download_url(mod_id: str, mc_version: str,
                                loader: str) -> Optional[tuple[str, str, int]]:
    """
    获取 CurseForge 模组匹配指定版本的下载信息。
    通过 curse.tools 代理或官方 API 获取。
    返回 (filename, download_url, file_size) 或 None。
    """
    # 获取模组文件列表
    url = f"{CURSEFORGE_BASE}/mods/{mod_id}/files?pageSize=50&sortOrder=desc"
    if mc_version:
        url += f"&gameVersion={mc_version}"
    if loader:
        curse_loader = _CURSE_LOADER_MAP.get(loader, 0)
        if curse_loader:
            url += f"&modLoaderType={curse_loader}"

    data = _fetch_json(url, headers=_curse_headers(), timeout=API_TIMEOUT)
    if not data or not isinstance(data, dict):
        return None
    files = data.get("data") or []
    if not files:
        return None

    # 取最新的文件
    latest = files[0]
    filename = latest.get("fileName", "mod.jar")
    file_size = latest.get("fileLength", 0)

    # 构建 CDN 下载 URL
    file_id = latest.get("id", 0)
    # 从 file ID 拆出第一段作为 CDN 子路径的一部分
    file_id_str = str(file_id)
    sub_path = file_id_str[:4] if len(file_id_str) >= 4 else file_id_str
    cdn_url = f"https://edge.forgecdn.net/files/{sub_path}/{file_id}/{filename}"

    return filename, cdn_url, file_size


def curseforge_download(mod_id: str, target_dir: str, name: str,
                        mc_version: str, loader: str) -> Optional[str]:
    """
    从 CurseForge 下载模组。
    返回 jar 文件名，失败返回 None。
    """
    from download_msl import multithreaded_download

    from i18n import t
    info = curseforge_get_download_url(mod_id, mc_version, loader)
    if not info:
        print(t("mod.no_version_match"))
        return None
    filename, dl_url, _ = info

    target_path = os.path.join(target_dir, filename)

    # CDN 下载无需 API key
    if multithreaded_download(dl_url, target_path, desc=f"Mod: {name}"):
        return filename
    return None


# ---------------------------------------------------------------------------
# 统一搜索
# ---------------------------------------------------------------------------


def search_all_mods(query: str, mc_version: str = "", loader: str = "",
                    limit: int = 10) -> dict[str, list[dict]]:
    """
    同时在 Modrinth 和 CurseForge 搜索模组。

    支持中文关键词（Modrinth 原生支持 Unicode 查询）。
    搜索结果少于 3 条时，自动回退到不限制版本/加载器的搜索。
    返回 {source: [results]}，并在结果 dict 中注入 "_fallback" 键标记回退。
    """
    results: dict[str, Any] = {}

    def _search_into(target: dict, src_name: str, search_fn, *args):
        try:
            items = search_fn(*args)
            if items:
                target[src_name] = items
        except Exception:
            pass

    # 1) 带过滤的精准搜索
    _search_into(results, "Modrinth", modrinth_search, query, mc_version, loader, limit)
    _search_into(results, "CurseForge", curseforge_search, query, mc_version, loader, limit)

    total_filtered = sum(len(v) for v in results.values())

    # 2) 如果结果太少（< 3），自动放宽搜索范围
    if total_filtered < 3:
        fallback: dict[str, list[dict]] = {}
        _search_into(fallback, "Modrinth", modrinth_search, query, "", "", limit)
        _search_into(fallback, "CurseForge", curseforge_search, query, "", "", limit)
        total_fallback = sum(len(v) for v in fallback.values())

        if total_fallback > total_filtered:
            seen_ids = set()
            for src, items in results.items():
                for item in items:
                    item_id = item.get("id", "") + "|" + src
                    seen_ids.add(item_id)

            for src, items in fallback.items():
                new_items = [
                    item for item in items
                    if item.get("id", "") + "|" + src not in seen_ids
                ]
                if new_items:
                    if src in results:
                        results[src].extend(new_items)
                    else:
                        results[src] = new_items

            results["_fallback"] = True

    return results


def download_mod(result: dict, target_dir: str,
                 mc_version: str, loader: str) -> Optional[str]:
    """
    根据搜索结果下载模组到目标目录。
    返回 jar 文件名，失败返回 None。
    """
    source = result["source"]
    pid = result["id"]
    name = result.get("name", "mod")

    if source == "Modrinth":
        return modrinth_download(pid, target_dir, name, mc_version, loader)
    elif source == "CurseForge":
        return curseforge_download(pid, target_dir, name, mc_version, loader)

    return None
