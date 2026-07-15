"""
服务器管理（版本隔离）

管理 servers/ 目录下的多服务器实例：创建、扫描、配置读写、
从压缩包导入服务器。
"""

import os
import json
import zipfile
import shutil
import glob
from typing import Any, Optional

from i18n import t


DEFAULT_SERVER_CONFIG: dict[str, Any] = {
    "name": "",
    "mc_version": "",
    "jar": "server.jar",
    "java_path": None,
    "min_mem": "1G",
    "max_mem": "4G",
    "extra_jvm_args": [],
    "extra_server_args": [],
    "openfrp": {
        "token": "",
        "proxy_id": 0,
        "auto_start": False,
    },
}


def ensure_servers_folder(storage_dir: str) -> str:
    """Ensure servers/ folder exists, return its absolute path."""
    path = os.path.join(storage_dir, "servers")
    os.makedirs(path, exist_ok=True)
    return path


def list_servers(servers_dir: str) -> list[dict[str, Any]]:
    """Scan servers/ directory and return all valid server configs."""
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
    """Load .server.json from a server directory."""
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
    """Save server config to .server.json."""
    merged = dict(DEFAULT_SERVER_CONFIG)
    merged.update(config)
    for key in ("_path", "_config_path"):
        merged.pop(key, None)
    config_path = os.path.join(server_dir, ".server.json")
    os.makedirs(server_dir, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Import from zip
# ---------------------------------------------------------------------------

def _prompt_zip_path() -> Optional[str]:
    """Prompt for a zip file path interactively."""
    print()
    print(t("import.prompt"))
    raw = input("  > ").strip()
    if not raw:
        return None
    raw = raw.strip('"')
    path = os.path.abspath(raw)
    if not os.path.isfile(path):
        print(t("import.file_missing", path=path))
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".zip", ".jar"):
        print(t("import.unsupported_format", ext=ext))
        return None
    return path


def _find_first_level_jars(directory: str) -> list[str]:
    """Find .jar files at the first level, or inside a single subdirectory."""
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
    """Let user pick from multiple jars interactively."""
    print()
    print(t("import.select_jar", count=len(jars)))
    print()
    for i, j in enumerate(jars, 1):
        print(f"  [{i}] {j}")
    print()
    while True:
        try:
            choice = input(t("import.pick_prompt", max=len(jars))).strip()
            idx = int(choice) - 1
            if 0 <= idx < len(jars):
                return jars[idx]
        except ValueError:
            pass
        print(t("app.invalid_choice_short"))


def _extract_with_progress(zip_path: str, target_dir: str) -> None:
    """Extract zip with progress bar and zip slip protection."""
    target_abs = os.path.abspath(target_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        total = sum(m.file_size for m in members if not m.is_dir())
        extracted = 0
        bar_width = 30
        print()
        for member in members:
            # Zip slip protection
            member_path = os.path.abspath(os.path.join(target_abs, member.filename))
            if not member_path.startswith(target_abs + os.sep):
                print(f"\n    [skip] path traversal blocked: {member.filename}")
                continue
            zf.extract(member, target_abs)
            if not member.is_dir():
                extracted += member.file_size
            if total > 0:
                pct = extracted / total
                filled = int(bar_width * pct)
                bar = "\u2588" * filled + "\u2591" * (bar_width - filled)
                print(f"    [{bar}] {pct * 100:5.1f}%", end="\r", flush=True)
        print()


# ---------------------------------------------------------------------------
# Server type classification
# ---------------------------------------------------------------------------

_PLUGIN_IDS = {"paper", "purpur", "leaf", "leaves", "spigot", "bukkit", "folia",
               "pufferfish", "pufferfish_purpur", "spongevanilla"}
_HYBRID_IDS = {"arclight", "arclight-forge", "arclight-fabric", "arclight-neoforge",
               "mohist", "catserver", "youer", "banner", "spongeforge"}
_MOD_IDS = {"neoforge", "forge", "fabric", "quilt"}
_VANILLA_IDS = {"vanilla", "vanilla-snapshot"}
_PROXY_IDS = {"velocity", "bungeecord", "lightfall", "travertine"}
_BEDROCK_IDS = {"bedrock-server", "nukkitx"}

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
    Detect server type. Returns one of:
    "plugin", "mod", "hybrid", "vanilla", "proxy", "bedrock", "unknown"
    """
    name = (server_cfg.get("name") or "").lower()
    jar = (server_cfg.get("jar") or "").lower()

    parts = name.split("-")
    candidate = parts[0] if parts else ""

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

    for hint, stype in _JAR_HINTS.items():
        if hint in jar:
            return stype

    return "unknown"


def import_server_from_zip(zip_path: str, servers_dir: str, project_dir: str) -> Optional[str]:
    """Import a server from a zip archive. Returns server directory or None."""
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        print(t("import.file_missing", path=zip_path))
        return None

    temp_dir = os.path.join(project_dir, ".import_temp")
    try:
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir)

        zip_name = os.path.basename(zip_path)
        print(t("import.extracting", name=zip_name))
        try:
            _extract_with_progress(zip_path, temp_dir)
        except zipfile.BadZipFile:
            print(t("import.bad_zip", name=zip_name))
            return None

        jars = _find_first_level_jars(temp_dir)
        if not jars:
            print(t("import.no_jar"))
            return None

        selected = jars[0] if len(jars) == 1 else _pick_jar_interactive(jars)
        jar_name = os.path.basename(selected)
        auto = len(jars) == 1
        print(t("import.auto_detect", detected="Auto-detected" if auto else "Selected", jar=selected))

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

        print(t("import.success", name=base_name))
        print(t("import.dir_info", dir=server_dir))
        return server_dir
    finally:
        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Export server
# ---------------------------------------------------------------------------

EXPORT_CATEGORIES: list[tuple[str, str, list[str], bool]] = [
    ("world",     "export.category.world",     ["world", "world_nether", "world_the_end"], True),
    ("datapacks", "export.category.datapacks",   ["world/datapacks"], True),
    ("plugins",   "export.category.plugins",    ["plugins/*.jar"], True),
    ("plugindata","export.category.plugindata",  ["plugins/*/"], True),
    ("playerdata","export.category.playerdata",  ["world/playerdata", "world/stats", "world/advancements",
                                                   "usercache.json", "ops.json", "whitelist.json",
                                                   "banned-players.json", "banned-ips.json"], False),
    ("icon",      "export.category.icon",       ["server-icon.png"], True),
    ("config",    "export.category.config",     ["server.properties", "bukkit.yml", "spigot.yml",
                                                   "paper.yml", "pufferfish.yml", "purpur.yml",
                                                   "eula.txt", "commands.yml", "permissions.yml",
                                                   "help.yml", ".server.json"], True),
]


def _export_collect_files(server_dir: str, selected: set[str]) -> list[tuple[str, str]]:
    """Collect files based on selected categories. Returns [(arcname, abs_path)]."""
    files: list[tuple[str, str]] = []
    seen = set()

    for key, _, patterns, _ in EXPORT_CATEGORIES:
        if key not in selected:
            continue
        for pattern in patterns:
            full_pattern = os.path.join(server_dir, pattern)

            if "*" not in pattern:
                if os.path.isfile(full_pattern):
                    rel = os.path.relpath(full_pattern, server_dir).replace("\\", "/")
                    if rel not in seen:
                        seen.add(rel)
                        files.append((rel, full_pattern))
                elif os.path.isdir(full_pattern):
                    for root, dirs, fnames in os.walk(full_pattern):
                        for fn in fnames:
                            fp = os.path.join(root, fn)
                            rel = os.path.relpath(fp, server_dir).replace("\\", "/")
                            if rel not in seen:
                                seen.add(rel)
                                files.append((rel, fp))
            else:
                for match in glob.glob(full_pattern, recursive=False):
                    if os.path.isfile(match):
                        rel = os.path.relpath(match, server_dir).replace("\\", "/")
                        if rel not in seen:
                            seen.add(rel)
                            files.append((rel, match))
                if pattern.endswith("/*/"):
                    base_dir = os.path.join(server_dir, pattern[:-2])
                    if os.path.isdir(base_dir):
                        for entry in sorted(os.listdir(base_dir)):
                            sub = os.path.join(base_dir, entry)
                            if os.path.isdir(sub):
                                for root, dirs, fnames in os.walk(sub):
                                    for fn in fnames:
                                        fp = os.path.join(root, fn)
                                        rel = os.path.relpath(fp, server_dir).replace("\\", "/")
                                        if rel not in seen:
                                            seen.add(rel)
                                            files.append((rel, fp))
    return files


def export_server_to_zip(server_dir: str, output_path: str,
                          selected_categories: set[str]) -> Optional[str]:
    """Export a server to a zip archive. Returns output_path or None."""
    files = _export_collect_files(server_dir, selected_categories)
    if not files:
        print(t("export.no_files"))
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
                    print(t("export.progress", bar=bar, pct=pct * 100, i=i, total=total),
                          end="\r", flush=True)
        print()
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(t("export.success", path=output_path, size=size_mb, files=total))
        return output_path
    except (OSError, zipfile.BadZipFile) as e:
        print(t("export.failed", msg=str(e)))
        return None
