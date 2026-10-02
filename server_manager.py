"""
服务器实例的存储层。

负责 servers/ 下的多实例：扫描、.server.json 读写、服务端类型识别，
以及导出时的文件收集。

这一层不做界面，也不做交互：解压与打包的编排在 core/transfer.py，
删除/启用 jar 在 core/instances.py。
"""

import copy
import os
import json
import glob
from typing import Any


DEFAULT_SERVER_CONFIG: dict[str, Any] = {
    "name": "",
    "mc_version": "",
    "jar": "server.jar",
    "java_path": None,
    "min_mem": "1G",
    "max_mem": "4G",
    "extra_jvm_args": [],
    "extra_server_args": [],
    "process_priority": "normal",
    "openfrp": {
        "token": "",
        "proxy_id": 0,
        "auto_start": False,
    },
}


# 这些字段必须是字符串列表。手改 .server.json 时很容易写成 null 或一个字符串，
# 而 " ".join(None) 会直接抛 TypeError、cmd.extend("abc") 会把参数拆成单个字符，
# 所以在这里统一收敛，后面所有消费方都可以假定类型是对的。
_LIST_FIELDS = ("extra_jvm_args", "extra_server_args")


def _default_server_config() -> dict[str, Any]:
    """返回一份独立的默认配置。

    不能写成 dict(DEFAULT_SERVER_CONFIG)：那是浅拷贝，extra_jvm_args /
    extra_server_args / openfrp 会与模块级常量共用同一个对象。界面层只要
    就地改一下 server_cfg["openfrp"]，改动就留在了默认值里，之后加载的每个
    实例都会继承上一个实例的隧道绑定（start.py 会照着它去自动开隧道）。
    """
    return copy.deepcopy(DEFAULT_SERVER_CONFIG)


def _normalize_server_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """把外部读进来的配置收敛成可信的类型。就地修改并返回同一个 dict。"""
    for key in _LIST_FIELDS:
        value = cfg.get(key)
        if value is None:
            cfg[key] = []
        elif isinstance(value, str):
            cfg[key] = value.split()
        elif isinstance(value, list):
            cfg[key] = [str(item) for item in value]
        else:
            cfg[key] = []

    openfrp = cfg.get("openfrp")
    if not isinstance(openfrp, dict):
        openfrp = copy.deepcopy(DEFAULT_SERVER_CONFIG["openfrp"])
    else:
        openfrp = dict(openfrp)
        try:
            openfrp["proxy_id"] = int(openfrp.get("proxy_id") or 0)
        except (TypeError, ValueError):
            openfrp["proxy_id"] = 0
        openfrp["token"] = str(openfrp.get("token") or "")
        openfrp["auto_start"] = bool(openfrp.get("auto_start"))
    cfg["openfrp"] = openfrp
    return cfg


def ensure_servers_folder(storage_dir: str) -> str:
    """确保 servers/ 存在，返回其绝对路径。"""
    path = os.path.join(storage_dir, "servers")
    os.makedirs(path, exist_ok=True)
    return path


def list_servers(servers_dir: str) -> list[dict[str, Any]]:
    """扫描 servers/ 目录，返回所有合法实例的配置。"""
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
            merged = _default_server_config()
            merged.update(cfg)
            merged["_path"] = server_path
            merged["_config_path"] = config_path
            servers.append(_normalize_server_config(merged))
        except (json.JSONDecodeError, OSError):
            continue
    return servers


def load_server_config(server_dir: str) -> dict[str, Any]:
    """读取实例目录下的 .server.json。"""
    config_path = os.path.join(server_dir, ".server.json")
    cfg = _default_server_config()
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                cfg.update(loaded)
        except (json.JSONDecodeError, OSError):
            pass
    return _normalize_server_config(cfg)


def save_server_config(config: dict[str, Any], server_dir: str) -> None:
    """把实例配置写回 .server.json。"""
    merged = _default_server_config()
    merged.update(config)
    for key in ("_path", "_config_path"):
        merged.pop(key, None)
    _normalize_server_config(merged)
    config_path = os.path.join(server_dir, ".server.json")
    os.makedirs(server_dir, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 服务端类型识别
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
    """识别服务端类型。返回其中之一：

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


# ---------------------------------------------------------------------------
# 导出：分类定义与文件收集
# ---------------------------------------------------------------------------

EXPORT_CATEGORIES: list[tuple[str, str, list[str], bool]] = [
    ("world",     "export.category.world",     ["world", "world_nether", "world_the_end"], True),
    ("datapacks", "export.category.datapacks",   ["world/datapacks"], True),
    ("plugins",   "export.category.plugins",    ["plugins/*.jar"], True),
    ("plugindata","export.category.plugindata",  ["plugins/*/"], True),
    ("playerdata", "export.category.playerdata",
     ["world/playerdata", "world/stats", "world/advancements",
      "usercache.json", "ops.json", "whitelist.json",
      "banned-players.json", "banned-ips.json"], False),
    ("icon",      "export.category.icon",       ["server-icon.png"], True),
    ("config",    "export.category.config",     ["server.properties", "bukkit.yml", "spigot.yml",
                                                   "paper.yml", "pufferfish.yml", "purpur.yml",
                                                   "eula.txt", "commands.yml", "permissions.yml",
                                                   "help.yml", ".server.json"], True),
]


def collect_export_files(server_dir: str, selected: set[str]) -> list[tuple[str, str]]:
    """按选中的分类收集文件，返回 [(zip 内路径, 磁盘绝对路径)]。

    路径里的通配符按 glob 展开；以 /*/ 结尾的模式表示「子目录们」，
    会整棵递归进去（plugins/*/ 就是各插件的数据目录）。
    """
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
                    for root, _dirs, fnames in os.walk(full_pattern):
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
                                for root, _dirs, fnames in os.walk(sub):
                                    for fn in fnames:
                                        fp = os.path.join(root, fn)
                                        rel = os.path.relpath(fp, server_dir).replace("\\", "/")
                                        if rel not in seen:
                                            seen.add(rel)
                                            files.append((rel, fp))
    return files
