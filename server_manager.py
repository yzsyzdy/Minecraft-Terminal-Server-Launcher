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
