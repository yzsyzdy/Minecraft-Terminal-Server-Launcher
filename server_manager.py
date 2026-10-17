"""
服务器实例的存储层。

负责 servers/ 下的多实例：扫描、.server.json 读写、服务端类型识别，
以及导出时的文件收集。

这一层不做界面，也不做交互：解压与打包的编排在 core/transfer.py，
删除/启用 jar 在 core/instances.py。
"""

import os
import json
import glob
import tempfile
import threading
import time
from typing import Any

# 实例种类。minecraft 走原来的「解析 Java → 拼 JVM 命令」，generic 只认下面
# 那份启动配置，不碰 Java 也不碰 jar。缺省按 minecraft 处理，旧 .server.json
# 不需要改。
KIND_MINECRAFT = "minecraft"
KIND_GENERIC = "generic"
INSTANCE_KINDS = (KIND_MINECRAFT, KIND_GENERIC)

# 通用实例的启动方式
LAUNCH_EXEC = "exec"          # 直接运行一个可执行文件 + 参数
LAUNCH_CMD = "cmd"            # 交给 cmd.exe /c
LAUNCH_POWERSHELL = "powershell"  # 交给 powershell -Command
LAUNCH_MODES = (LAUNCH_EXEC, LAUNCH_CMD, LAUNCH_POWERSHELL)

DEFAULT_GENERIC_LAUNCH: dict[str, Any] = {
    "mode": LAUNCH_EXEC,
    "program": "",   # mode=exec：可执行文件路径，相对路径按实例目录算
    "args": [],      # mode=exec：参数，一项一个，不做再切分
    "command": "",   # mode=cmd / powershell：要执行的命令原文
    "workdir": "",   # 工作目录，相对实例目录；留空即实例目录
}


DEFAULT_SERVER_CONFIG: dict[str, Any] = {
    "name": "",
    "kind": KIND_MINECRAFT,
    "mc_version": "",
    "jar": "server.jar",
    "java_path": None,
    "min_mem": "1G",
    "max_mem": "4G",
    "extra_jvm_args": [],
    "extra_server_args": [],
    "process_priority": "normal",
    # 随软件启动而启动：软件一起来就在后台把它拉起（网页面板里能看能管）
    "auto_start": False,
    # 显式造一份新的（args 是列表，不能用 dict(DEFAULT_GENERIC_LAUNCH) 直接共用）
    "launch": {**DEFAULT_GENERIC_LAUNCH, "args": []},
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
    extra_server_args / launch / openfrp 会与模块级常量共用同一个对象。
    界面层只要就地改一下 server_cfg["openfrp"]，改动就留在了默认值里，之后
    加载的每个实例都会继承上一个实例的隧道绑定（start.py 会照着它去自动
    开隧道）。

    显式复制那几个可变容器，而不是 copy.deepcopy 整个字典：结果一样，但
    快得多——这个函数在 list_servers() 里每个实例都要调一次。
    **以后往 DEFAULT_SERVER_CONFIG 里加可变值，记得在这里也复制一份。**
    """
    fresh = dict(DEFAULT_SERVER_CONFIG)
    fresh["extra_jvm_args"] = []
    fresh["extra_server_args"] = []
    fresh["launch"] = dict(DEFAULT_GENERIC_LAUNCH)
    fresh["launch"]["args"] = []
    fresh["openfrp"] = dict(DEFAULT_SERVER_CONFIG["openfrp"])
    return fresh


def _clone_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """复制一条实例条目，确保调用方改它不会污染缓存或别的调用方。

    extra_jvm_args / extra_server_args / launch(含 args) / openfrp 都是可变
    容器。历史上就出过「改一个实例的隧道绑定污染了默认值、进而影响之后
    加载的其它实例」这一类问题，所以这里必须真的复制，不能只 dict(entry)。
    """
    clone = dict(entry)
    clone["extra_jvm_args"] = list(entry.get("extra_jvm_args") or [])
    clone["extra_server_args"] = list(entry.get("extra_server_args") or [])
    launch = dict(entry.get("launch") or {})
    launch["args"] = list(launch.get("args") or [])
    clone["launch"] = launch
    clone["openfrp"] = dict(entry.get("openfrp") or {})
    return clone


# .server.json 的解析缓存：配置路径 -> (mtime_ns, size, 已合并并归一化的条目)
#
# list_servers() 被调得很勤：/api/state 每次一遍，每个文件管理操作解析实例
# 目录时还要一遍。每次都要打开、解析每个 .server.json，再造一份默认配置
# 合并、归一化——100 个实例时单次约 30ms。缓存按「文件 mtime + 大小」判断
# 是否还有效，未变动的实例直接复用上次的结果。
_ENTRY_CACHE: dict[str, tuple[int, int, float, dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()

# 缓存最长存活时间。
#
# 光靠 mtime 判断在 Windows 上有个坑：文件时间戳取自系统时钟，粒度约 15ms，
# 所以「上一次读」和「紧接着的一次改写」有可能拿到同一个 mtime，大小又没变
# （比如把 "4G" 改成 "6G"），于是缓存看起来还有效。自家写盘会在 save 之后
# 显式清缓存，所以受影响的是「用户在编辑器里手改 .server.json」这种情况：
# 加了这个上限，面板最多看到 2 秒前的值，而不是一直看旧值。
_CACHE_TTL = 2.0


def _cache_key(config_path: str) -> str:
    return os.path.normcase(os.path.abspath(config_path))


def invalidate_server_cache(server_dir: str = "") -> None:
    """丢掉实例配置缓存。传实例目录只丢那一个，不传就全清。

    save_server_config() 写完会调它。光靠 mtime 判断是不够的：FAT/exFAT 与
    某些网络盘的时间戳粒度是 1~2 秒，同一秒内的改写可能看起来「没变」，
    那样面板改完设置再读就会拿到旧值——正是我们要避免的那类 bug。
    """
    with _CACHE_LOCK:
        if not server_dir:
            _ENTRY_CACHE.clear()
            return
        _ENTRY_CACHE.pop(_cache_key(os.path.join(server_dir, ".server.json")), None)


def list_servers(servers_dir: str) -> list[dict[str, Any]]:
    """扫描 servers/ 目录，返回所有合法实例的配置。

    返回的是副本，调用方可以放心就地修改。
    """
    servers: list[dict[str, Any]] = []
    if not os.path.isdir(servers_dir):
        return servers
    seen: set[str] = set()
    for entry in sorted(os.listdir(servers_dir)):
        server_path = os.path.join(servers_dir, entry)
        if not os.path.isdir(server_path):
            continue
        config_path = os.path.join(server_path, ".server.json")
        # 用一次 stat 同时拿到「在不在」和 mtime/大小，省掉原来的 isfile
        try:
            st = os.stat(config_path)
        except OSError:
            continue
        key = _cache_key(config_path)
        seen.add(key)
        with _CACHE_LOCK:
            cached = _ENTRY_CACHE.get(key)
        fresh = (cached is not None
                 and (time.monotonic() - cached[2]) <= _CACHE_TTL
                 and cached[0] == st.st_mtime_ns
                 and cached[1] == st.st_size)
        if fresh:
            servers.append(_clone_entry(cached[3]))
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
            normalized = _normalize_server_config(merged)
        except (json.JSONDecodeError, OSError):
            continue
        with _CACHE_LOCK:
            _ENTRY_CACHE[key] = (st.st_mtime_ns, st.st_size,
                                 time.monotonic(), normalized)
        servers.append(_clone_entry(normalized))

    # 顺手把已经不存在的实例从缓存里清掉，别让它无限长
    with _CACHE_LOCK:
        for stale in [k for k in _ENTRY_CACHE if k not in seen]:
            _ENTRY_CACHE.pop(stale, None)
    return servers


def _as_bool(value: Any) -> bool:
    """把 JSON 里读到的值收敛成真布尔。

    bool("false") 是 True——手改过 .server.json、或者配置文件是别的工具生成
    的（那边把布尔写成了字符串），都会让「随软件自启」在用户明确写了 false
    的情况下被打开。所以字符串要按字面意思认，不能按「非空即真」认。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "y")


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

    kind = str(cfg.get("kind") or KIND_MINECRAFT).strip().lower()
    cfg["kind"] = kind if kind in INSTANCE_KINDS else KIND_MINECRAFT
    cfg["auto_start"] = _as_bool(cfg.get("auto_start"))

    launch = cfg.get("launch")
    if not isinstance(launch, dict):
        launch = {}
    mode = str(launch.get("mode") or LAUNCH_EXEC).strip().lower()
    args = launch.get("args")
    if isinstance(args, str):
        # 手改 JSON 时容易把参数写成一行字符串；这里按空白切开，
        # 但不像 extra_jvm_args 那样静默拆字符——args 本来就该是列表
        args = args.split()
    elif not isinstance(args, list):
        args = []
    cfg["launch"] = {
        "mode": mode if mode in LAUNCH_MODES else LAUNCH_EXEC,
        "program": str(launch.get("program") or ""),
        "args": [str(item) for item in args],
        "command": str(launch.get("command") or ""),
        "workdir": str(launch.get("workdir") or ""),
    }

    openfrp = cfg.get("openfrp")
    if not isinstance(openfrp, dict):
        # 造一份新的，别把模块级默认值那个 dict 直接挂到实例上
        openfrp = dict(DEFAULT_SERVER_CONFIG["openfrp"])
    else:
        openfrp = dict(openfrp)
        try:
            openfrp["proxy_id"] = int(openfrp.get("proxy_id") or 0)
        except (TypeError, ValueError):
            openfrp["proxy_id"] = 0
        openfrp["token"] = str(openfrp.get("token") or "")
        openfrp["auto_start"] = _as_bool(openfrp.get("auto_start"))
    cfg["openfrp"] = openfrp
    return cfg


def ensure_servers_folder(storage_dir: str) -> str:
    """确保 servers/ 存在，返回其绝对路径。"""
    path = os.path.join(storage_dir, "servers")
    os.makedirs(path, exist_ok=True)
    return path


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
    # 先写临时文件再 os.replace：直接 open(path, "w") 会截断原文件，写到一半
    # 断电或抛异常就留下残缺 JSON，而 load_server_config 解析失败会静默回退
    # 到默认配置——实例在界面上「消失」成一份空白配置，比报错难查得多。
    #
    # 临时名还必须是唯一的。以前固定写 ".server.json.mstl-tmp"，两个请求同时
    # 保存同一个实例时，后开的那个会把前一个正在写的内容截断，先收工的一方
    # 再 os.replace 上去，落盘的就是别人的半截 JSON。mkstemp 保证唯一，并且
    # 落在同一个目录里——os.replace 要求源和目标在同一个文件系统上。
    fd, tmp_path = tempfile.mkstemp(prefix=".server.json-", suffix=".mstl-tmp",
                                    dir=server_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, config_path)
    except OSError:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    # 写盘之后必须让缓存失效，否则紧接着的 list_servers() 会拿回旧值
    invalidate_server_cache(server_dir)


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

    "plugin", "mod", "hybrid", "vanilla", "proxy", "bedrock", "generic", "unknown"
    """
    # 通用实例不看名字也不看 jar：它根本不是 Minecraft 服务端
    if (server_cfg.get("kind") or KIND_MINECRAFT) == KIND_GENERIC:
        return "generic"

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
