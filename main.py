"""
MSTL — Minecraft Terminal Server Launcher
公共 API 导出模块。

所有子模块的公开接口在此汇集，方便 import 和管理。
"""

from config import load_config, save_config
from server_manager import (
    ensure_servers_folder,
    list_servers,
    load_server_config,
    save_server_config,
    import_server_from_zip,
    export_server_to_zip,
    classify_server_type,
    EXPORT_CATEGORIES,
)
from server_launcher import (
    start_minecraft_server,
    start_server_interactive,
)
from java_tools import resolve_java, detect_java_versions
from download_msl import show_download_server_menu
from menu import show_main_menu
from plugin_sources import search_all, download_plugin
from mod_sources import search_all_mods, download_mod, set_curseforge_api_key
from i18n import t, load_language, available_languages

# OpenFrp 隧道管理 (可选，由 start.py 按需导入)
# from openfrp.tunnel_manager import (launch_frpc, stop_frpc, ...)

__all__ = [
    "load_config", "save_config",
    "ensure_servers_folder", "list_servers",
    "load_server_config", "save_server_config",
    "import_server_from_zip", "export_server_to_zip",
    "classify_server_type", "EXPORT_CATEGORIES",
    "start_minecraft_server", "start_server_interactive",
    "resolve_java", "detect_java_versions",
    "show_download_server_menu",
    "show_main_menu",
    "search_all", "download_plugin",
    "search_all_mods", "download_mod", "set_curseforge_api_key",
    "t", "load_language", "available_languages",
]
