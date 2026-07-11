"""
main.py — 模块入口（re-export）

将所有子模块的公开 API 集中导入，方便外部调用。
实际实现分布在以下模块：
  config.py         配置管理
  server_manager.py 服务器目录管理、导入压缩包
  download_msl.py   MSL 服务端下载
  java_tools.py     Java 检测、JDK 下载、版本兼容
  server_launcher.py 服务器启动
  menu.py           交互菜单
"""

from config import load_config, save_config, DEFAULT_CONFIG
from server_manager import (
    ensure_servers_folder,
    list_servers,
    load_server_config,
    save_server_config,
    import_server_from_zip,
    classify_server_type,
    DEFAULT_SERVER_CONFIG,
)
from download_msl import (
    msl_get_server_types,
    msl_get_versions,
    msl_get_download_url,
    show_download_server_menu,
)
from java_tools import (
    JavaInfo,
    detect_java_versions,
    load_java_list,
    save_java_list,
    resolve_java,
    get_java_requirement,
    MC_JAVA_COMPAT,
    JDK_MIRROR_URLS,
)
from server_launcher import start_minecraft_server, start_server_interactive
from menu import show_no_server_menu, select_server_interactive
