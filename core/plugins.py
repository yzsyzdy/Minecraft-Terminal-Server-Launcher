"""面板插件：把「需要第三方库」的功能放到核心之外。

MSTL 承诺零依赖，但终端模拟这类功能天然需要额外库（pywinpty、xterm.js）。
插件机制让核心保持零依赖，缺插件时功能优雅降级，而不是把依赖硬塞进来。

目录约定（打包版与源码版一致）：

    plugins/<id>/
        plugin.json      清单：名称、版本、入口、前端资源
        plugin.py        后端入口，必须暴露 register(ctx)
        web/             可选：要挂到面板上的静态资源

清单字段：

    {
      "id": "terminal",
      "name": "终端",
      "version": "1.0.0",
      "entry": "plugin.py",
      "requires": ["pywinpty"],       # 缺了就判定为不可用
      "web": ["xterm.js", "xterm.css"],  # 要注入 index.html 的资源
      "api": "terminal"                # 挂载前缀 /api/plugin/terminal/*
    }

安全边界：`plugins/` 目录和 `servers/` 一样是**用户可写的本地目录**，
插件代码以本进程权限运行。装插件 = 在这台机器上执行任意代码，和「通用实例
能跑任意命令」「面板能改文件」是同一条信任边界，没有额外风险也没有额外保障。
所以插件不自动加载，必须显式启用（`config.json` 的 `plugins.enabled`）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

MANIFEST_NAME = "plugin.json"
ENTRY_NAME = "plugin.py"


@dataclass
class Plugin:
    """一个已加载（或加载失败）的插件。"""

    id: str
    dir: str
    name: str = ""
    version: str = ""
    error: str = ""
    # 缺失的依赖名（requires 里 import 不上的）
    missing: list[str] = field(default_factory=list)
    # 要注入 index.html 的前端资源（相对插件 web/ 目录）
    assets: list[str] = field(default_factory=list)
    # 后端注册的路由前缀（不含 /api/plugin/）
    api: str = ""
    # 由 plugin.register(ctx) 填进来的处理函数表：path -> handler
    handlers: dict[str, Callable] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return not self.error and not self.missing

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "version": self.version,
            "available": self.available,
            "error": self.error,
            "missing": list(self.missing),
            "assets": list(self.assets),
            "api": self.api,
        }


def plugins_dir(project_dir: str) -> str:
    return os.path.join(project_dir, "plugins")


def _read_manifest(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _has_module(name: str) -> bool:
    """依赖是否可导入。只做 find_spec，不真的 import（避免副作用）。"""
    import importlib.util as util
    try:
        return util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class PluginContext:
    """交给插件的上下文。

    插件不直接碰 Handler 类，只注册路径处理函数，签名：
        handler(query: dict, body: Optional[dict]) -> (status, content_type, bytes)
    这样插件不需要知道面板的鉴权、线程模型和 JSON 约定。
    """

    def __init__(self, plugin: Plugin, project_dir: str) -> None:
        self.plugin = plugin
        self.project_dir = project_dir
        self.plugin_dir = plugin.dir

    def route(self, path: str, handler: Callable) -> None:
        """注册一个路由。path 相对于 /api/plugin/<id>/。"""
        key = path.strip("/")
        self.plugin.handlers[key] = handler

    def asset_path(self, name: str) -> str:
        return os.path.join(self.plugin_dir, "web", name)


class PluginManager:
    """扫描、加载、登记插件。一个进程一份。"""

    def __init__(self, project_dir: str) -> None:
        self.project_dir = project_dir
        self._plugins: dict[str, Plugin] = {}
        self._lock = threading.RLock()
        self._loaded = False

    # -- 扫描 --------------------------------------------------------------

    def scan(self, enabled: Optional[list[str]] = None) -> list[Plugin]:
        """扫描 plugins/ 目录。

        enabled 为 None 表示全部不启用（只看清单，给设置页展示用）；
        传了列表就只加载列表里的 id。
        """
        root = plugins_dir(self.project_dir)
        found: dict[str, Plugin] = {}
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                path = os.path.join(root, name)
                if not os.path.isdir(path):
                    continue
                manifest = _read_manifest(os.path.join(path, MANIFEST_NAME))
                if not manifest:
                    continue
                pid = str(manifest.get("id") or name).strip()
                if not pid:
                    continue
                plugin = Plugin(
                    id=pid,
                    dir=path,
                    name=str(manifest.get("name") or pid),
                    version=str(manifest.get("version") or ""),
                    assets=[str(a) for a in (manifest.get("web") or [])
                            if str(a).strip()],
                    api=str(manifest.get("api") or pid),
                )
                requires = [str(r) for r in (manifest.get("requires") or [])
                            if str(r).strip()]
                plugin.missing = [r for r in requires if not _has_module(r)]
                found[pid] = plugin
        with self._lock:
            self._plugins = found
            self._loaded = True
        if enabled:
            for pid in enabled:
                plugin = found.get(pid)
                if plugin and plugin.available:
                    self._load_entry(plugin)
        return list(found.values())

    # -- 加载单个插件 ------------------------------------------------------

    def _load_entry(self, plugin: Plugin) -> None:
        """把 plugin.py 作为模块执行，调它的 register(ctx)。"""
        entry = os.path.join(plugin.dir, ENTRY_NAME)
        if not os.path.isfile(entry):
            plugin.error = "缺少 plugin.py"
            return
        module_name = f"mstl_plugin_{plugin.id}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, entry)
            if spec is None or spec.loader is None:
                plugin.error = "无法加载 plugin.py"
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:                      # noqa: BLE001
            # 插件代码是第三方写的，任何异常都不能让面板启动失败
            plugin.error = f"{type(e).__name__}: {e}"
            sys.modules.pop(module_name, None)
            return
        register = getattr(module, "register", None)
        if not callable(register):
            plugin.error = "plugin.py 缺少 register(ctx)"
            return
        try:
            register(PluginContext(plugin, self.project_dir))
        except Exception as e:                      # noqa: BLE001
            plugin.error = f"register 失败: {type(e).__name__}: {e}"

    # -- 查询 --------------------------------------------------------------

    def get(self, pid: str) -> Optional[Plugin]:
        with self._lock:
            return self._plugins.get(pid)

    def all(self) -> list[Plugin]:
        with self._lock:
            return list(self._plugins.values())

    def available(self) -> list[Plugin]:
        return [p for p in self.all() if p.available]

    def dispatch(self, pid: str, path: str, query: dict,
                 body: Optional[dict]) -> Optional[tuple[int, str, bytes]]:
        """把请求交给插件。返回 None 表示这个插件不认这个路径。"""
        plugin = self.get(pid)
        if not plugin or not plugin.available:
            return None
        handler = plugin.handlers.get(path.strip("/"))
        if handler is None:
            return None
        try:
            result = handler(query, body)
        except Exception as e:                      # noqa: BLE001
            return (500, "application/json",
                    ('{"ok":false,"message":"%s"}'
                     % str(e).replace('"', "'")).encode("utf-8"))
        return result

    # -- 前端资源 ----------------------------------------------------------

    def asset_file(self, pid: str, name: str) -> Optional[str]:
        """插件 web/ 目录下的文件绝对路径；不合法返回 None。"""
        plugin = self.get(pid)
        if not plugin:
            return None
        name = str(name or "")
        if (not name or "/" in name or "\\" in name or ".." in name):
            return None
        path = os.path.join(plugin.dir, "web", name)
        return path if os.path.isfile(path) else None


# ---------------------------------------------------------------------------
# 进程级单例（面板启动时创建，Handler 闭包里用）
# ---------------------------------------------------------------------------

_MANAGER: Optional[PluginManager] = None
_MANAGER_LOCK = threading.Lock()


def shared_manager(project_dir: str) -> PluginManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None or _MANAGER.project_dir != project_dir:
            _MANAGER = PluginManager(project_dir)
        return _MANAGER


__all__ = ["Plugin", "PluginManager", "PluginContext", "plugins_dir",
           "shared_manager", "MANIFEST_NAME", "ENTRY_NAME"]
