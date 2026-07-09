"""
Minecraft 服务器启动模块

提供启动 Minecraft 服务器的核心函数，基于 Leaves/Fabric 服务器的 JVM 参数设计。
"""

import subprocess
import sys
import os
import signal
import json
import glob
import zipfile
import shutil
import urllib.request
import urllib.error
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, Any


# =============================================================================
# 配置管理（config.json）
# =============================================================================

DEFAULT_CONFIG: dict[str, Any] = {
    # 全局 Java 路径（每个服务器可单独覆盖）
    "java_path": None,
    # 是否启用控制台交互
    "interactive": True,
}


def _config_path(storage_dir: str) -> str:
    return os.path.join(storage_dir, "config.json")


def load_config(storage_dir: str) -> dict[str, Any]:
    """
    加载 config.json。如果文件不存在则自动创建默认配置并返回。

    返回的字典保证包含 DEFAULT_CONFIG 中的所有键。
    """
    path = _config_path(storage_dir)
    config = dict(DEFAULT_CONFIG)

    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for k in DEFAULT_CONFIG:
                    if k in loaded:
                        config[k] = loaded[k]
        except (json.JSONDecodeError, OSError):
            pass

    return config


def save_config(config: dict[str, Any], storage_dir: str) -> None:
    """保存配置到 config.json。缺失的键用默认值补齐。"""
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    path = _config_path(storage_dir)
    os.makedirs(storage_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


# =============================================================================
# 服务器管理（版本隔离）
# =============================================================================

DEFAULT_SERVER_CONFIG: dict[str, Any] = {
    "name": "",
    "mc_version": "",
    "jar": "server.jar",
    "java_path": None,
    "min_mem": "1G",
    "max_mem": "4G",
    "extra_jvm_args": [],
    "extra_server_args": [],
}


def _servers_dir(storage_dir: str) -> str:
    return os.path.join(storage_dir, "servers")


def ensure_servers_folder(storage_dir: str) -> str:
    """确保 servers/ 文件夹存在，返回其绝对路径。"""
    path = _servers_dir(storage_dir)
    os.makedirs(path, exist_ok=True)
    return path


def _server_config_path(server_dir: str) -> str:
    return os.path.join(server_dir, ".server.json")


def list_servers(servers_dir: str) -> list[dict[str, Any]]:
    """
    扫描 servers/ 目录，返回所有有效的服务器配置列表。
    每个服务器子目录下必须有 .server.json 才被认为是有效服务器。
    """
    servers: list[dict[str, Any]] = []
    if not os.path.isdir(servers_dir):
        return servers

    for entry in sorted(os.listdir(servers_dir)):
        server_path = os.path.join(servers_dir, entry)
        if not os.path.isdir(server_path):
            continue
        config_path = _server_config_path(server_path)
        if not os.path.isfile(config_path):
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                continue
            # 补全默认值
            merged = dict(DEFAULT_SERVER_CONFIG)
            merged.update(cfg)
            merged["_path"] = server_path   # 内部字段，不写出到文件
            merged["_config_path"] = config_path
            servers.append(merged)
        except (json.JSONDecodeError, OSError):
            continue

    return servers


def load_server_config(server_dir: str) -> dict[str, Any]:
    """加载指定服务器目录的 .server.json。"""
    config_path = _server_config_path(server_dir)
    cfg = dict(DEFAULT_SERVER_CONFIG)
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_server_config(config: dict[str, Any], server_dir: str) -> None:
    """保存服务器配置到 .server.json。"""
    merged = dict(DEFAULT_SERVER_CONFIG)
    merged.update(config)
    # 去掉内部字段
    for key in ("_path", "_config_path"):
        merged.pop(key, None)
    config_path = _server_config_path(server_dir)
    os.makedirs(server_dir, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


def _prompt_zip_path() -> Optional[str]:
    """交互式询问压缩包路径，返回绝对路径，用户取消返回 None。"""
    print()
    print("  请输入压缩包路径（支持拖拽文件到窗口）：")
    raw = input("  > ").strip()

    if not raw:
        return None

    # 去掉拖拽时 Windows 自动加的双引号
    raw = raw.strip('"')
    path = os.path.abspath(raw)

    if not os.path.isfile(path):
        print(f"  [错误] 文件不存在: {path}")
        return None

    ext = os.path.splitext(path)[1].lower()
    if ext not in (".zip", ".jar"):
        print(f"  [错误] 不支持的文件格式: {ext}，仅支持 .zip")
        return None

    return path


def _find_first_level_jars(directory: str) -> list[str]:
    """
    在目录的第一层查找 .jar 文件。
    如果第一层没有 jar 但恰好只有一个子目录，则检查该子目录的第一层。
    返回路径相对于 directory。
    """
    jars: list[str] = []
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return jars

    # 第一层扫描
    for entry in entries:
        entry_path = os.path.join(directory, entry)
        if os.path.isfile(entry_path) and entry.lower().endswith(".jar"):
            jars.append(entry)

    if jars:
        return jars

    # 没有 jar，看是不是套了一层文件夹
    subdirs = [
        e for e in entries
        if os.path.isdir(os.path.join(directory, e))
    ]
    if len(subdirs) == 1:
        subdir = subdirs[0]
        sub_path = os.path.join(directory, subdir)
        try:
            for entry in sorted(os.listdir(sub_path)):
                entry_path = os.path.join(sub_path, entry)
                if os.path.isfile(entry_path) and entry.lower().endswith(".jar"):
                    jars.append(os.path.join(subdir, entry))
        except OSError:
            pass

    return jars


def _pick_jar_interactive(jars: list[str]) -> str:
    """多个 jar 时让用户选择，返回 jar 的相对路径。"""
    print()
    print(f"  发现 {len(jars)} 个 .jar 文件，请选择服务端核心：")
    print()
    for i, j in enumerate(jars, 1):
        print(f"  [{i}] {j}")
    print()
    while True:
        try:
            choice = input(f"  请选择 (1-{len(jars)}): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(jars):
                return jars[idx]
        except ValueError:
            pass
        print(f"  无效选择。")


def _extract_with_progress(zip_path: str, target_dir: str) -> None:
    """解压 zip 并显示 ASCII 进度条。"""
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        # 排除目录，只算实际文件的大小
        total = sum(m.file_size for m in members if not m.is_dir())
        extracted = 0
        bar_width = 30

        print()
        for member in members:
            zf.extract(member, target_dir)
            if not member.is_dir():
                extracted += member.file_size

            if total > 0:
                pct = extracted / total
                filled = int(bar_width * pct)
                bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                print(
                    f"    \u89e3\u538b [{bar}] {pct * 100:5.1f}%",
                    end="\r",
                    flush=True,
                )
        print()


def import_server_from_zip(zip_path: str, servers_dir: str, project_dir: str) -> Optional[str]:
    """
    从压缩包导入一个 Minecraft 服务器。

    参数
    ------
    zip_path : str
        压缩包文件路径。
    servers_dir : str
        servers/ 目录的绝对路径（服务器将被创建在这里）。
    project_dir : str
        项目根目录（用于放置临时文件夹）。

    返回
    ------
    str or None
        成功时返回服务器目录的绝对路径，失败返回 None。

    流程
    -----
    1. 在项目目录创建临时文件夹 .import_temp
    2. 解压压缩包到临时文件夹
    3. 在第一层（含单层子目录兜底）查找 .jar 文件
    4. 零个 → 报错；一个 → 自动使用；多个 → 用户选择
    5. 以压缩包文件名（去扩展名）为服务器名，在 servers/ 下创建目录
    6. 将选中 jar 所在目录的所有内容移动到服务器目录
    7. 生成 .server.json
    8. 清理临时文件夹
    """
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        print(f"  [错误] 文件不存在: {zip_path}")
        return None

    temp_dir = os.path.join(project_dir, ".import_temp")

    try:
        # ---- 清理旧 temp 并解压 ----
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)

        zip_name = os.path.basename(zip_path)
        print(f"  [导入] 正在解压 {zip_name}")
        try:
            _extract_with_progress(zip_path, temp_dir)
        except zipfile.BadZipFile:
            print(f"  [错误] 文件不是有效的压缩包: {zip_name}")
            return None

        # ---- 查找 jar ----
        jars = _find_first_level_jars(temp_dir)
        if not jars:
            print("  [错误] 压缩包第一层未找到任何 .jar 文件。")
            print("         请确认压缩包内包含服务端核心 jar。")
            return None

        selected = jars[0] if len(jars) == 1 else _pick_jar_interactive(jars)
        jar_name = os.path.basename(selected)

        if len(jars) == 1:
            print(f"  [导入] 自动识别核心: {selected}")
        else:
            print(f"  [导入] 已选择核心: {selected}")

        # ---- 创建服务器目录 ----
        base_name = os.path.splitext(os.path.basename(zip_path))[0]
        server_dir = os.path.join(servers_dir, base_name)
        counter = 1
        while os.path.exists(server_dir):
            server_dir = os.path.join(servers_dir, f"{base_name}_{counter}")
            counter += 1
        os.makedirs(server_dir)

        # ---- 移动文件 ----
        jar_rel_dir = os.path.dirname(selected)  # 空字符串或子目录名
        if jar_rel_dir:
            source = os.path.join(temp_dir, jar_rel_dir)
            for item in os.listdir(source):
                shutil.move(os.path.join(source, item), server_dir)
        else:
            for item in os.listdir(temp_dir):
                shutil.move(os.path.join(temp_dir, item), server_dir)

        # ---- 生成 .server.json ----
        server_config = {
            "name": base_name,
            "mc_version": "",
            "jar": jar_name,
            "java_path": None,
            "min_mem": "1G",
            "max_mem": "4G",
            "extra_jvm_args": [],
            "extra_server_args": [],
        }
        save_server_config(server_config, server_dir)

        print(f"  [导入] 服务器 \"{base_name}\" 导入成功！")
        print(f"         目录: {server_dir}")
        return server_dir

    finally:
        # 无论如何都清理临时文件夹
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def show_no_server_menu(servers_dir: str, project_dir: str) -> None:
    """当 servers/ 下没有服务器时，显示导入/下载选项。"""
    while True:
        print()
        print("  servers/ 文件夹中没有找到任何服务器。")
        print()
        print("  [1] 导入服务器压缩包")
        print("  [2] 下载服务器")
        print("  [0] 退出")
        print()
        choice = input("  请选择 (0-2): ").strip()

        if choice == "0":
            print("[退出] 用户选择退出")
            sys.exit(0)

        elif choice == "1":
            zip_path = _prompt_zip_path()
            if zip_path is None:
                input("  按 Enter 返回菜单...")
                continue

            result = import_server_from_zip(zip_path, servers_dir, project_dir)
            if result is not None:
                print()
                print("  导入完成！你可以重新启动程序来启动该服务器。")
                print()
                input("  按 Enter 返回...")
                return   # 返回后外层重新扫描 servers/
            else:
                input("  按 Enter 返回菜单...")
                continue

        elif choice == "2":
            print()
            print("  [下载] 功能开发中。")
            print("  你可以手动从以下地址下载服务端核心：")
            print("    Paper:     https://papermc.io/downloads")
            print("    Purpur:    https://purpurmc.org/downloads")
            print("    Fabric:    https://fabricmc.net/use/server/")
            print("    NeoForge:  https://neoforged.net/")
            print()
            input("  按 Enter 返回菜单...")
            continue

        else:
            print(f"  无效选择，请输入 0-2。")


def select_server_interactive(
    servers: list[dict[str, Any]],
    servers_dir: str = "",
    project_dir: str = "",
) -> Optional[dict[str, Any]]:
    """
    列出服务器让用户选择，末尾附加导入/下载新服务器选项。

    返回选中服务器的配置字典。
    当用户选择导入/下载后，内部处理并更新 servers 列表后重新显示。
    传入 servers_dir / project_dir 才会显示导入/下载选项。
    """
    can_add_new = bool(servers_dir and project_dir)

    while True:
        print()
        print(f"  找到 {len(servers)} 个服务器：")
        print()
        for i, s in enumerate(servers, 1):
            ver = s.get("mc_version", "?")
            jar = s.get("jar", "?")
            mem = f"{s.get('min_mem', '?')} / {s.get('max_mem', '?')}"
            print(f"  [{i}] {s['name']}  (MC {ver} | {jar} | {mem})")

        idx_import = len(servers) + 1
        idx_download = len(servers) + 2
        max_choice = idx_download

        print()
        if can_add_new:
            print(f"  [{idx_import}] 导入新服务器（压缩包）")
            print(f"  [{idx_download}] 下载新服务器")
        print("  [0] 退出")
        print()

        prompt = f"  请选择 (0-{max_choice}): " if can_add_new else f"  请选择 (1-{len(servers)}): "
        choice = input(prompt).strip()

        if choice == "0":
            print("[退出] 用户选择退出")
            sys.exit(0)

        # 导入
        if can_add_new:
            try:
                int_choice = int(choice)
                if int_choice == idx_import:
                    zip_path = _prompt_zip_path()
                    if zip_path is None:
                        input("  按 Enter 返回菜单...")
                        continue
                    result = import_server_from_zip(zip_path, servers_dir, project_dir)
                    if result is not None:
                        servers.clear()
                        servers.extend(list_servers(servers_dir))
                    else:
                        input("  按 Enter 返回菜单...")
                    continue
                elif int_choice == idx_download:
                    result = show_download_server_menu(servers_dir, project_dir)
                    if result is not None:
                        servers.clear()
                        servers.extend(list_servers(servers_dir))
                    else:
                        input("  按 Enter 返回菜单...")
                    continue
            except ValueError:
                pass

        # 选择已有服务器
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                return servers[idx]
        except ValueError:
            pass

        print(f"  无效选择，请输入 0-{max_choice} 之间的数字。")


# =============================================================================
# 服务端下载模块（MSL API V4）
# =============================================================================

MSL_API_BASE = "https://api.mslmc.cn/v4"
MSL_USER_AGENT = "MinecraftServerLauncher/1.0"

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
    "paper": "Paper",
    "purpur": "Purpur",
    "leaf": "Leaf",
    "leaves": "Leaves",
    "spigot": "Spigot",
    "bukkit": "Bukkit",
    "folia": "Folia",
    "pufferfish": "Pufferfish",
    "pufferfish_purpur": "Pufferfish+Purpur",
    "spongevanilla": "SpongeVanilla",
    "arclight-forge": "Arclight (Forge)",
    "arclight-fabric": "Arclight (Fabric)",
    "arclight-neoforge": "Arclight (NeoForge)",
    "youer": "Youer",
    "mohist": "Mohist",
    "catserver": "CatServer",
    "banner": "Banner",
    "spongeforge": "SpongeForge",
    "neoforge": "NeoForge",
    "forge": "Forge",
    "fabric": "Fabric",
    "quilt": "Quilt",
    "vanilla": "Vanilla",
    "vanilla-snapshot": "Vanilla Snapshot",
    "bedrock-server": "Bedrock Server",
    "nukkitx": "NukkitX",
    "velocity": "Velocity",
    "bungeecord": "BungeeCord",
    "lightfall": "Lightfall",
    "travertine": "Travertine",
}


def _server_display_name(server_id: str) -> str:
    return _SERVER_DISPLAY_NAMES.get(server_id, server_id.capitalize())


# ---------------------------------------------------------------------------
# MSL API 缓存（每日刷新）
# ---------------------------------------------------------------------------

def _msl_cache_path(project_dir: str) -> str:
    return os.path.join(project_dir, "msl_cache.json")


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _msl_load_cache(project_dir: str) -> dict:
    """加载 MSL 缓存文件，不存在返回空字典。"""
    path = _msl_cache_path(project_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _msl_save_cache(cache: dict, project_dir: str) -> None:
    """保存 MSL 缓存文件。"""
    path = _msl_cache_path(project_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _msl_cache_fetch(
    cache_key: str,
    path: str,
    project_dir: str,
) -> Any:
    """缓存优先的 MSL 请求。同一天内使用缓存，跨天重新请求。"""
    cache = _msl_load_cache(project_dir)
    entry = cache.get(cache_key)
    today = _today_str()

    if entry and isinstance(entry, dict) and entry.get("cached_at") == today:
        print(f"  [缓存] 使用本地缓存 ({today})")
        return entry.get("data")

    import urllib.request as ureq
    import urllib.error as uerr

    url = f"{MSL_API_BASE}{path}"
    try:
        req = ureq.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with ureq.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except uerr.HTTPError as e:
        print(f"  [错误] MSL API 返回 HTTP {e.code}: {e.reason}")
        return None
    except uerr.URLError as e:
        print(f"  [错误] 网络请求失败: {e.reason}")
        return None
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [错误] 响应解析失败: {e}")
        return None

    if not isinstance(data, dict):
        print(f"  [错误] MSL API 返回格式异常")
        return None
    if data.get("code") != 200:
        print(f"  [错误] MSL API: {data.get('message', '未知错误')}")
        return None

    result = data.get("data")
    cache[cache_key] = {"cached_at": today, "data": result}
    _msl_save_cache(cache, project_dir)
    return result


def msl_get_server_types(project_dir: str = "") -> Optional[dict[str, list[str]]]:
    """获取（缓存优先）分类后的服务端类型列表。"""
    if project_dir:
        return _msl_cache_fetch("server_types", "/mirrors", project_dir)
    return _msl_request("/mirrors")


def msl_get_versions(server_type: str, project_dir: str = "") -> Optional[dict]:
    """获取（缓存优先）指定服务端支持的 MC 版本。"""
    cache_key = f"versions_{server_type}"
    if project_dir:
        return _msl_cache_fetch(cache_key, f"/mirrors/{server_type}", project_dir)
    return _msl_request(f"/mirrors/{server_type}")


def _msl_request(path: str) -> Any:
    """直接请求 MSL API（无缓存），用于下载地址等非缓存场景。"""
    import urllib.request as ureq
    import urllib.error as uerr
    url = f"{MSL_API_BASE}{path}"
    try:
        req = ureq.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with ureq.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except uerr.HTTPError as e:
        print(f"  [错误] MSL API 返回 HTTP {e.code}: {e.reason}")
        return None
    except uerr.URLError as e:
        print(f"  [错误] 网络请求失败: {e.reason}")
        return None
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [错误] 响应解析失败: {e}")
        return None
    if not isinstance(data, dict):
        print(f"  [错误] MSL API 返回格式异常")
        return None
    if data.get("code") != 200:
        print(f"  [错误] MSL API: {data.get('message', '未知错误')}")
        return None
    return data.get("data")


def msl_get_download_url(server_type: str, version: str) -> Optional[dict]:
    return _msl_request(f"/download/server/{server_type}/{version}")


def _download_with_progress(url: str, target_path: str, desc: str = "") -> bool:
    import urllib.request as ureq
    import urllib.error as uerr
    try:
        req = ureq.Request(url, headers={"User-Agent": MSL_USER_AGENT})
        with ureq.urlopen(req, timeout=300) as resp:
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
                        mb = downloaded / 1024 / 1024
                        print(f"    {desc} 已下载 {mb:.1f} MB...", end="\r", flush=True)
        print()
        return True
    except uerr.HTTPError as e:
        print(f"\n  [错误] 下载失败 (HTTP {e.code})")
        return False
    except uerr.URLError as e:
        print(f"\n  [错误] 下载失败: {e.reason}")
        return False
    except OSError as e:
        print(f"\n  [错误] 文件写入失败: {e}")
        return False


def _interactive_pick_server_type(categorized: dict[str, list[str]]) -> Optional[str]:
    all_servers: list[tuple[str, str]] = []
    for category, server_list in categorized.items():
        label = _CATEGORY_LABELS.get(category, category)
        if not isinstance(server_list, list):
            continue
        for sid in server_list:
            if isinstance(sid, str):
                all_servers.append((sid, label))
    if not all_servers:
        print("  [错误] MSL API 返回的服务端列表为空")
        return None
    print()
    print("  请选择服务端类型：")
    print()
    current_category = ""
    idx = 1
    index_map: list[tuple[int, str]] = []
    for sid, cat in all_servers:
        if cat != current_category:
            print(f"  [{cat}]")
            current_category = cat
        display = _server_display_name(sid)
        print(f"      [{idx}] {display}")
        index_map.append((idx, sid))
        idx += 1
    print()
    while True:
        try:
            choice = input(f"  请输入编号 (1-{len(index_map)}): ").strip()
            num = int(choice)
            for i, sid in index_map:
                if i == num:
                    return sid
        except ValueError:
            pass
        print(f"  无效选择，请输入 1-{len(index_map)} 之间的数字。")


def _interactive_pick_version(versions: list[str], description: str) -> Optional[str]:
    if not versions:
        print("  [错误] 该服务端没有可用的版本")
        return None
    print()
    if description:
        print(f"  {description}")
        print()
    print(f"  可用版本（共 {len(versions)} 个），默认下载最新版：")
    print()
    stable = [v for v in versions if not any(x in v.lower() for x in ("pre", "rc", "snapshot", "alpha", "beta"))]
    show = stable[:20] if stable else versions[:20]
    for i, ver in enumerate(show, 1):
        marker = " ← 最新" if i == 1 else ""
        print(f"  [{i}] {ver}{marker}")
    print(f"  [L] 直接下载最新版 ({show[0]})")
    print()
    while True:
        choice = input(f"  请输入编号 (1-{len(show)} 或 L): ").strip().lower()
        if choice == "l":
            return show[0]
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(show):
                return show[idx]
        except ValueError:
            pass
        print(f"  无效选择。")


def _prompt_post_download_config(server_name: str) -> dict:
    """下载完成后询问用户 Java 和内存配置。"""
    print()
    print("  服务器下载完成，请配置运行时参数（直接回车使用默认值）：")
    print()

    java_input = input(f"  Java 路径（回车自动检测）: ").strip()
    java_path = java_input if java_input else None

    while True:
        min_input = input(f"  最小内存 [1G]: ").strip()
        if not min_input:
            min_mem = "1G"
            break
        if min_input.upper().endswith(("G", "M")):
            min_mem = min_input.upper()
            break
        print("  格式错误，请输入如 1G、2048M")

    while True:
        max_input = input(f"  最大内存 [4G]: ").strip()
        if not max_input:
            max_mem = "4G"
            break
        if max_input.upper().endswith(("G", "M")):
            max_mem = max_input.upper()
            break
        print("  格式错误，请输入如 4G、4096M")

    return {
        "java_path": java_path,
        "min_mem": min_mem,
        "max_mem": max_mem,
    }


def show_download_server_menu(servers_dir: str, project_dir: str) -> Optional[str]:
    print()
    print("  [下载] 正在获取可用服务端列表...")
    print()
    categorized = msl_get_server_types(project_dir)
    if not categorized:
        print("  [错误] 无法获取服务端列表，请检查网络连接。")
        return None
    server_id = _interactive_pick_server_type(categorized)
    if server_id is None:
        return None
    print(f"  [下载] 正在获取 {_server_display_name(server_id)} 的版本列表...")
    version_data = msl_get_versions(server_id, project_dir)
    if not version_data:
        print(f"  [错误] 无法获取 {_server_display_name(server_id)} 的版本信息")
        return None
    versions = version_data.get("versions", [])
    description = version_data.get("description", "")
    selected_version = _interactive_pick_version(versions, description)
    if selected_version is None:
        return None
    print(f"  [下载] 已选择: {_server_display_name(server_id)} {selected_version}")
    print(f"  [下载] 正在获取下载地址...")
    download_info = msl_get_download_url(server_id, selected_version)
    if not download_info:
        print("  [错误] 无法获取下载地址")
        print("  MSL API 下载接口有频率限制：每小时 30 次，每天 60 次")
        return None
    url = download_info.get("url", "")
    if not url:
        print("  [错误] 下载地址为空")
        return None
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
    success = _download_with_progress(url, jar_path, desc="下载中")
    if not success:
        shutil.rmtree(server_dir, ignore_errors=True)
        return None
    if not os.path.isfile(jar_path) or os.path.getsize(jar_path) == 0:
        print("  [错误] 下载的文件无效")
        shutil.rmtree(server_dir, ignore_errors=True)
        return None
    post_cfg = _prompt_post_download_config(server_name)
    server_config = {
        "name": server_name,
        "mc_version": selected_version,
        "jar": jar_filename,
        "java_path": post_cfg["java_path"],
        "min_mem": post_cfg["min_mem"],
        "max_mem": post_cfg["max_mem"],
        "extra_jvm_args": [],
        "extra_server_args": [],
    }
    save_server_config(server_config, server_dir)
    size_mb = os.path.getsize(jar_path) / 1024 / 1024
    print(f"  [下载] 下载完成！({size_mb:.1f} MB)")
    if download_info.get("sha256"):
        print(f"          SHA256: {download_info['sha256']}")
    print(f"          服务器目录: {server_dir}")
    return server_dir


# =============================================================================
# Java 自动检测模块
# =============================================================================


@dataclass
class JavaInfo:
    """描述一个检测到的 Java 安装。"""
    path: str
    version: str
    detected_at: str  # ISO 8601 格式

    @staticmethod
    def from_dict(d: dict) -> "JavaInfo":
        return JavaInfo(
            path=d["path"],
            version=d["version"],
            detected_at=d.get("detected_at", ""),
        )


def _run_java_version(java_exe: str) -> Optional[str]:
    """执行 java -version，解析版本号。失败返回 None。"""
    try:
        result = subprocess.run(
            [java_exe, "-version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stderr or result.stdout
        for line in output.splitlines():
            line = line.strip()
            if '"' in line and "version" in line:
                start = line.index('"') + 1
                end = line.index('"', start)
                return line[start:end]
        return None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def _search_java_from_env() -> list[JavaInfo]:
    """从环境变量 JAVA_HOME / JDK_HOME / PATH 搜索 java。"""
    found: list[JavaInfo] = []
    seen = set()
    now = datetime.now(timezone.utc).isoformat()

    candidates = []

    for var in ("JAVA_HOME", "JDK_HOME", "JRE_HOME"):
        val = os.environ.get(var, "").strip()
        if val:
            candidates.append(os.path.join(val, "bin", "java.exe"))

    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        d = d.strip()
        if not d:
            continue
        java_candidate = os.path.join(d, "java.exe")
        if os.path.isfile(java_candidate):
            candidates.append(java_candidate)

    for cand in candidates:
        norm = os.path.normpath(cand)
        if norm in seen:
            continue
        if not os.path.isfile(norm):
            continue
        version = _run_java_version(norm)
        if version:
            seen.add(norm)
            found.append(JavaInfo(path=norm, version=version, detected_at=now))

    return found


def _search_java_from_default_paths() -> list[JavaInfo]:
    """扫描 Windows 上常见的 JDK/JRE 安装目录。"""
    found: list[JavaInfo] = []
    seen = set()
    now = datetime.now(timezone.utc).isoformat()

    search_roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Common"),
        os.path.join(os.environ.get("USERPROFILE", ""), ".jdks"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "JetBrains"),
    ]

    patterns = [
        "Java/jdk-*/bin/java.exe",
        "Java/jdk*/bin/java.exe",
        "Java/jre*/bin/java.exe",
        "Eclipse Adoptium/jdk-*/bin/java.exe",
        "Eclipse Adoptium/jre-*/bin/java.exe",
        "Microsoft/jdk-*/bin/java.exe",
        "AdoptOpenJDK/jdk-*/bin/java.exe",
        "Amazon Corretto/jdk*/bin/java.exe",
        "Amazon Corretto/jre*/bin/java.exe",
        "Zulu/zulu*/bin/java.exe",
        "Liberica JDK/jdk-*/bin/java.exe",
        "Common/Oracle/Java/javapath/java.exe",
    ]

    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for pattern in patterns:
            full_glob = os.path.join(root, pattern)
            for exe in glob.glob(full_glob):
                norm = os.path.normpath(exe)
                if norm in seen:
                    continue
                version = _run_java_version(norm)
                if version:
                    seen.add(norm)
                    found.append(JavaInfo(path=norm, version=version, detected_at=now))

    jbr_pattern = "*/jbr/bin/java.exe"
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for exe in glob.glob(os.path.join(root, jbr_pattern)):
            norm = os.path.normpath(exe)
            if norm in seen:
                continue
            version = _run_java_version(norm)
            if version:
                seen.add(norm)
                found.append(JavaInfo(path=norm, version=version, detected_at=now))

    return found


def detect_java_versions() -> list[JavaInfo]:
    """执行一次完整的 Java 扫描，返回去重后的结果列表。"""
    found_env = _search_java_from_env()
    found_paths = _search_java_from_default_paths()

    seen = set()
    merged: list[JavaInfo] = []
    for item in found_env + found_paths:
        norm = os.path.normpath(item.path)
        if norm not in seen:
            seen.add(norm)
            merged.append(item)

    merged.sort(key=lambda j: _version_sort_key(j.version), reverse=True)
    return merged


def _version_sort_key(version: str) -> tuple:
    """将 '21.0.9' 转为 (21, 0, 9, ...) 用于排序。非数字段放到最后。"""
    parts = []
    for segment in version.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(segment)
    return tuple(parts)


# ---------------------------------------------------------------------------
# 持久化：Java 列表保存到 JSON
# ---------------------------------------------------------------------------

def _java_list_path(storage_dir: str) -> str:
    return os.path.join(storage_dir, "java_list.json")


def load_java_list(storage_dir: str) -> list[JavaInfo]:
    """加载本地缓存的 Java 列表。"""
    path = _java_list_path(storage_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            raw = data.get("known_javas", [])
        elif isinstance(data, list):
            raw = data
        else:
            return []
        return [JavaInfo.from_dict(j) for j in raw if isinstance(j, dict)]
    except (json.JSONDecodeError, KeyError, OSError):
        return []


def save_java_list(javas: list[JavaInfo], storage_dir: str, last_selected: str = "") -> None:
    """保存 Java 列表到本地缓存。"""
    path = _java_list_path(storage_dir)
    os.makedirs(storage_dir, exist_ok=True)
    data = {
        "known_javas": [asdict(j) for j in javas],
        "last_selected": last_selected,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _interactive_select(javas: list[JavaInfo]) -> JavaInfo:
    """在终端列出多个 Java 版本让用户选择，返回选中的 JavaInfo。"""
    print()
    print("  检测到多个 Java 安装，请选择其中一个：")
    print()
    for i, j in enumerate(javas, 1):
        print(f"  [{i}] {j.path}")
        print(f"      版本: {j.version}")
    print()
    while True:
        try:
            choice = input("  请输入编号 (1-{}): ".format(len(javas))).strip()
            idx = int(choice) - 1
            if 0 <= idx < len(javas):
                return javas[idx]
        except ValueError:
            pass
        print(f"  无效选择，请输入 1-{len(javas)} 之间的数字。")


def resolve_java(
    configured_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
) -> str:
    """
    自动解析可用的 Java 路径。

    流程
    -----
    1. 如果 configured_path 存在且有效，直接返回。
    2. 加载本地缓存的 Java 列表，过滤出仍有效的项。
    3. 如果有效缓存中只有一项 → 直接使用。
    4. 如果有多项 → 询问用户选择。
    5. 如果没有有效缓存 → 执行完整扫描。
    6. 扫描后：找到多项则询问，找到一项直接使用，没找到报错。
    7. 将最终选中的路径保存到缓存。

    返回
    -----
    str
        可用的 java.exe 绝对路径。
    """
    if configured_path:
        abs_path = os.path.abspath(configured_path)
        if os.path.isfile(abs_path):
            return abs_path

    if storage_dir is None:
        storage_dir = os.getcwd()

    saved = load_java_list(storage_dir)
    valid_saved = [j for j in saved if os.path.isfile(j.path)]

    need_scan = False
    selected: Optional[JavaInfo] = None

    if len(valid_saved) == 1:
        selected = valid_saved[0]
        print(f"[Java] 使用本地记录的 Java: {selected.path} (版本 {selected.version})")
    elif len(valid_saved) > 1:
        print(f"[Java] 在本地记录中找到 {len(valid_saved)} 个可用的 Java")
        selected = _interactive_select(valid_saved)
    else:
        need_scan = True

    if need_scan:
        print("[Java] 未找到本地可用 Java 记录，正在扫描系统...")
        found = detect_java_versions()
        if not found:
            raise FileNotFoundError(
                "未在系统中找到任何 Java 安装。\n"
                "  请先安装 JDK 21 或更高版本：https://adoptium.net/"
            )

        if len(found) == 1:
            selected = found[0]
            print(f"[Java] 自动发现: {selected.path} (版本 {selected.version})")
        else:
            selected = _interactive_select(found)

        seen_paths = {os.path.normpath(j.path) for j in found}
        extra = [j for j in saved if os.path.isfile(j.path)
                 and os.path.normpath(j.path) not in seen_paths]
        merged = found + extra
        save_java_list(merged, storage_dir, last_selected=selected.path)
    else:
        save_java_list(saved, storage_dir, last_selected=selected.path)

    return selected.path


# =============================================================================
# 服务器启动模块
# =============================================================================


def start_minecraft_server(
    java_path: str,
    jar_path: str,
    min_mem: str = "1G",
    max_mem: str = "16G",
    nogui: bool = True,
    workdir: Optional[str] = None,
    extra_jvm_args: Optional[list[str]] = None,
    extra_server_args: Optional[list[str]] = None,
) -> int:
    """
    启动 Minecraft 服务器（非交互模式，仅输出日志）。

    参数
    ----------
    java_path : str
        Java 可执行文件的路径。
    jar_path : str
        服务端核心 jar 文件的路径。
    min_mem : str
        最小堆内存，默认 "1G"。
    max_mem : str
        最大堆内存，默认 "16G"。
    nogui : bool
        是否使用 nogui 模式，默认 True。
    workdir : str, optional
        服务器工作目录，默认使用 jar 所在目录。
    extra_jvm_args : list[str], optional
        额外的 JVM 参数。
    extra_server_args : list[str], optional
        额外的服务器参数。

    返回
    -------
    int
        服务器进程的退出码。

    异常
    ------
    FileNotFoundError
        java_path 或 jar_path 不存在。
    """
    java_path = os.path.abspath(java_path)
    jar_path = os.path.abspath(jar_path)

    if not os.path.isfile(java_path):
        raise FileNotFoundError(f"Java 可执行文件未找到: {java_path}")
    if not os.path.isfile(jar_path):
        raise FileNotFoundError(f"服务端核心文件未找到: {jar_path}")

    if workdir is None:
        workdir = os.path.dirname(jar_path)

    jvm_opens = [
        "--add-opens", "java.base/java.lang=ALL-UNNAMED",
        "--add-opens", "java.base/java.lang.reflect=ALL-UNNAMED",
        "--add-opens", "java.base/java.util=ALL-UNNAMED",
        "--add-opens", "java.base/java.io=ALL-UNNAMED",
        "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
    ]

    cmd = [java_path]
    cmd.append(f"-Xmx{max_mem}")
    cmd.append(f"-Xms{min_mem}")
    cmd.extend(jvm_opens)
    if extra_jvm_args:
        cmd.extend(extra_jvm_args)
    cmd.append("-jar")
    cmd.append(jar_path)
    if nogui:
        cmd.append("nogui")
    if extra_server_args:
        cmd.extend(extra_server_args)

    print(f"[启动] 工作目录: {workdir}")
    print(f"[启动] Java:    {java_path}")
    print(f"[启动] 核心:    {jar_path}")
    print(f"[启动] 内存:    最小 {min_mem} / 最大 {max_mem}")
    print(f"[启动] 命令:    {' '.join(cmd)}")
    print()

    process = subprocess.Popen(
        cmd,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )

    def _handle_sigint(sig, frame):
        print("\n[关闭] 收到 Ctrl+C，正在停止服务器...")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        sys.exit(0)

    original_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        for line in process.stdout:
            print(line, end="")
    except KeyboardInterrupt:
        _handle_sigint(None, None)

    process.wait()
    signal.signal(signal.SIGINT, original_sigint)

    return process.returncode


def start_server_interactive(
    java_path: str,
    jar_path: str,
    min_mem: str = "1G",
    max_mem: str = "16G",
    extra_jvm_args: Optional[list[str]] = None,
    extra_server_args: Optional[list[str]] = None,
) -> int:
    """
    启动 Minecraft 服务器并支持控制台交互输入（如 stop、say 等命令）。

    参数与 start_minecraft_server 一致，区别在于将 stdin 传给子进程，
    使得用户可以在终端输入服务器指令。
    """
    java_path = os.path.abspath(java_path)
    jar_path = os.path.abspath(jar_path)

    if not os.path.isfile(java_path):
        raise FileNotFoundError(f"Java 可执行文件未找到: {java_path}")
    if not os.path.isfile(jar_path):
        raise FileNotFoundError(f"服务端核心文件未找到: {jar_path}")

    workdir = os.path.dirname(jar_path)

    jvm_opens = [
        "--add-opens", "java.base/java.lang=ALL-UNNAMED",
        "--add-opens", "java.base/java.lang.reflect=ALL-UNNAMED",
        "--add-opens", "java.base/java.util=ALL-UNNAMED",
        "--add-opens", "java.base/java.io=ALL-UNNAMED",
        "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
    ]

    cmd = [java_path]
    cmd.append(f"-Xmx{max_mem}")
    cmd.append(f"-Xms{min_mem}")
    cmd.extend(jvm_opens)
    if extra_jvm_args:
        cmd.extend(extra_jvm_args)
    cmd.append("-jar")
    cmd.append(jar_path)
    cmd.append("nogui")
    if extra_server_args:
        cmd.extend(extra_server_args)

    print(f"[启动] 工作目录: {workdir}")
    print(f"[启动] Java:    {java_path}")
    print(f"[启动] 核心:    {jar_path}")
    print(f"[启动] 内存:    最小 {min_mem} / 最大 {max_mem}")
    print("[启动] 控制台交互已启用（输入 stop 关闭服务器）")
    print()

    return subprocess.call(cmd, cwd=workdir)
