"""
服务器管理（版本隔离）

管理 servers/ 目录下的多服务器实例：创建、扫描、配置读写、
从压缩包导入服务器。
"""

import os
import json
import zipfile
import shutil
from typing import Any, Optional


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


def ensure_servers_folder(storage_dir: str) -> str:
    """确保 servers/ 文件夹存在，返回其绝对路径。"""
    path = os.path.join(storage_dir, "servers")
    os.makedirs(path, exist_ok=True)
    return path


def list_servers(servers_dir: str) -> list[dict[str, Any]]:
    """扫描 servers/ 目录，返回所有有效服务器的配置列表。"""
    servers: list[dict[str, Any]] = []
    if not os.path.isdir(servers_dir):
        return servers
    for entry in sorted(os.listdir(servers_dir)):
        server_path = os.path.join(servers_dir, entry)
        if not os.path.isdir(server_path):
            continue
        config_path = os.path.join(server_path, ".server.json")
        if not os.path.isfile(config_path):
            continue
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                continue
            merged = dict(DEFAULT_SERVER_CONFIG)
            merged.update(cfg)
            merged["_path"] = server_path
            merged["_config_path"] = config_path
            servers.append(merged)
        except (json.JSONDecodeError, OSError):
            continue
    return servers


def load_server_config(server_dir: str) -> dict[str, Any]:
    """加载指定服务器目录的 .server.json。"""
    config_path = os.path.join(server_dir, ".server.json")
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
    for key in ("_path", "_config_path"):
        merged.pop(key, None)
    config_path = os.path.join(server_dir, ".server.json")
    os.makedirs(server_dir, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 导入压缩包
# ---------------------------------------------------------------------------

def _prompt_zip_path() -> Optional[str]:
    """交互式询问压缩包路径。"""
    print()
    print("  请输入压缩包路径（支持拖拽文件到窗口）：")
    raw = input("  > ").strip()
    if not raw:
        return None
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
    """在第一层查找 .jar，无则检查唯一子目录。"""
    jars: list[str] = []
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return jars
    for entry in entries:
        ep = os.path.join(directory, entry)
        if os.path.isfile(ep) and entry.lower().endswith(".jar"):
            jars.append(entry)
    if jars:
        return jars
    subdirs = [e for e in entries if os.path.isdir(os.path.join(directory, e))]
    if len(subdirs) == 1:
        sub = subdirs[0]
        sp = os.path.join(directory, sub)
        try:
            for entry in sorted(os.listdir(sp)):
                if os.path.isfile(os.path.join(sp, entry)) and entry.lower().endswith(".jar"):
                    jars.append(os.path.join(sub, entry))
        except OSError:
            pass
    return jars


def _pick_jar_interactive(jars: list[str]) -> str:
    """多 jar 时让用户选择。"""
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
    """解压 zip 并显示进度条。"""
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
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
                print(f"    \u89e3\u538b [{bar}] {pct * 100:5.1f}%", end="\r", flush=True)
        print()


# ---------------------------------------------------------------------------
# 服务端类型分类
# ---------------------------------------------------------------------------

_PLUGIN_IDS = {"paper", "purpur", "leaf", "leaves", "spigot", "bukkit", "folia",
               "pufferfish", "pufferfish_purpur", "spongevanilla"}
_HYBRID_IDS = {"arclight", "arclight-forge", "arclight-fabric", "arclight-neoforge",
               "mohist", "catserver", "youer", "banner", "spongeforge"}
_MOD_IDS = {"neoforge", "forge", "fabric", "quilt"}
_VANILLA_IDS = {"vanilla", "vanilla-snapshot"}
_PROXY_IDS = {"velocity", "bungeecord", "lightfall", "travertine"}
_BEDROCK_IDS = {"bedrock-server", "nukkitx"}

# jar 文件名到类型的映射（用于导入的服务器）
_JAR_HINTS: dict[str, str] = {
    "paper": "plugin", "purpur": "plugin", "leaves": "plugin", "spigot": "plugin",
    "bukkit": "plugin", "folia": "plugin", "pufferfish": "plugin",
    "fabric-server": "mod", "forge-": "mod", "neoforge-": "mod",
    "quilt-server": "mod",
    "mohist": "hybrid", "catserver": "hybrid", "arclight": "hybrid",
    "server": "vanilla",
    "velocity": "proxy", "bungeecord": "proxy",
}


def classify_server_type(server_cfg: dict, server_dir: str) -> str:
    """
    判断服务器类型。返回以下之一：
    "plugin", "mod", "hybrid", "vanilla", "proxy", "bedrock", "unknown"
    """
    name = (server_cfg.get("name") or "").lower()
    jar = (server_cfg.get("jar") or "").lower()

    # 从 name 提取可能的 server_id（格式：paper-1.21.1）
    parts = name.split("-")
    candidate = parts[0] if parts else ""

    # 查分类表
    if candidate in _PLUGIN_IDS:
        return "plugin"
    if candidate in _HYBRID_IDS:
        return "hybrid"
    if candidate in _MOD_IDS:
        return "mod"
    if candidate in _VANILLA_IDS:
        return "vanilla"
    if candidate in _PROXY_IDS:
        return "proxy"
    if candidate in _BEDROCK_IDS:
        return "bedrock"

    # 从 jar 文件名猜测
    for hint, stype in _JAR_HINTS.items():
        if hint in jar:
            return stype

    return "unknown"


def import_server_from_zip(zip_path: str, servers_dir: str, project_dir: str) -> Optional[str]:
    """从压缩包导入服务器。返回服务器目录路径，失败返回 None。"""
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        print(f"  [错误] 文件不存在: {zip_path}")
        return None

    temp_dir = os.path.join(project_dir, ".import_temp")
    try:
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

        jars = _find_first_level_jars(temp_dir)
        if not jars:
            print("  [错误] 压缩包第一层未找到任何 .jar 文件。")
            return None

        selected = jars[0] if len(jars) == 1 else _pick_jar_interactive(jars)
        jar_name = os.path.basename(selected)
        print(f"  [导入] {'自动识别' if len(jars) == 1 else '已选择'}核心: {selected}")

        base_name = os.path.splitext(os.path.basename(zip_path))[0]
        server_dir = os.path.join(servers_dir, base_name)
        counter = 1
        while os.path.exists(server_dir):
            server_dir = os.path.join(servers_dir, f"{base_name}_{counter}")
            counter += 1
        os.makedirs(server_dir)

        jar_rel_dir = os.path.dirname(selected)
        if jar_rel_dir:
            source = os.path.join(temp_dir, jar_rel_dir)
            for item in os.listdir(source):
                shutil.move(os.path.join(source, item), server_dir)
        else:
            for item in os.listdir(temp_dir):
                shutil.move(os.path.join(temp_dir, item), server_dir)

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
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 导出服务器
# ---------------------------------------------------------------------------

EXPORT_CATEGORIES: list[tuple[str, str, list[str], bool]] = [
    # (key, label, patterns, default_selected)
    ("world", "世界数据 (world/ nether/ end)", ["world", "world_nether", "world_the_end"], True),
    ("datapacks", "数据包 (datapacks)", ["world/datapacks"], True),
    ("plugins", "插件 (jar 文件)", ["plugins/*.jar"], True),
    ("plugindata", "插件数据 (配置文件夹)", ["plugins/*/"], True),
    ("playerdata", "玩家数据 (playerdata/stats/白名单等)", ["world/playerdata", "world/stats", "world/advancements",
                                                  "usercache.json", "ops.json", "whitelist.json",
                                                  "banned-players.json", "banned-ips.json"], False),
    ("icon", "服务器图标", ["server-icon.png"], True),
    ("config", "服务器配置 (server.properties/yml/eula)", ["server.properties", "bukkit.yml", "spigot.yml",
                                                        "paper.yml", "pufferfish.yml", "purpur.yml",
                                                        "eula.txt", "commands.yml", "permissions.yml",
                                                        "help.yml", ".server.json"], True),
]


def _export_collect_files(server_dir: str, selected: set[str]) -> list[tuple[str, str]]:
    """
    根据选中的分类收集文件。
    返回 [(arcname, abs_path)]，arcname 相对于服务器目录。
    """
    files: list[tuple[str, str]] = []
    seen = set()

    for key, _, patterns, _ in EXPORT_CATEGORIES:
        if key not in selected:
            continue
        for pattern in patterns:
            matched = False
            full_pattern = os.path.join(server_dir, pattern)

            if "*" not in pattern:
                # 精确路径
                if os.path.isfile(full_pattern):
                    rel = os.path.relpath(full_pattern, server_dir)
                    norm = rel.replace("\\", "/")
                    if norm not in seen:
                        seen.add(norm)
                        files.append((norm, full_pattern))
                        matched = True
                elif os.path.isdir(full_pattern):
                    for root, dirs, fnames in os.walk(full_pattern):
                        for fn in fnames:
                            fp = os.path.join(root, fn)
                            rel = os.path.relpath(fp, server_dir)
                            norm = rel.replace("\\", "/")
                            if norm not in seen:
                                seen.add(norm)
                                files.append((norm, fp))
                    matched = True
            else:
                # glob 模式
                import glob as glob_mod
                for match in glob_mod.glob(full_pattern, recursive=False):
                    if os.path.isfile(match):
                        rel = os.path.relpath(match, server_dir)
                        norm = rel.replace("\\", "/")
                        if norm not in seen:
                            seen.add(norm)
                            files.append((norm, match))
                    matched = True
                # 目录模式：plugins/*/ → 匹配子目录
                if pattern.endswith("/*/"):
                    base_dir = os.path.join(server_dir, pattern[:-2])
                    if os.path.isdir(base_dir):
                        for entry in sorted(os.listdir(base_dir)):
                            sub = os.path.join(base_dir, entry)
                            if os.path.isdir(sub):
                                for root, dirs, fnames in os.walk(sub):
                                    for fn in fnames:
                                        fp = os.path.join(root, fn)
                                        rel = os.path.relpath(fp, server_dir)
                                        norm = rel.replace("\\", "/")
                                        if norm not in seen:
                                            seen.add(norm)
                                            files.append((norm, fp))
                                        matched = True

    return files


def export_server_to_zip(server_dir: str, output_path: str,
                          selected_categories: set[str]) -> Optional[str]:
    """
    将服务器导出为 zip 文件。
    
    server_dir: 服务器目录
    output_path: 目标 zip 路径
    selected_categories: 选中的分类 key 集合
    
    返回 output_path，失败返回 None。
    """
    files = _export_collect_files(server_dir, selected_categories)
    if not files:
        print("  [错误] 所选分类中没有找到任何文件。")
        return None

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    total = len(files)
    bar_width = 30

    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, (arcname, abs_path) in enumerate(files, 1):
                zf.write(abs_path, arcname)
                if total > 0:
                    pct = i / total
                    filled = int(bar_width * pct)
                    bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                    print(f"    导出 [{bar}] {pct * 100:5.1f}%  ({i}/{total})",
                          end="\r", flush=True)
        print()
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"  [导出] 成功: {output_path} ({size_mb:.1f} MB, {total} 个文件)")
        return output_path
    except (OSError, zipfile.BadZipFile) as e:
        print(f"\n  [错误] 导出失败: {e}")
        return None
