"""
实例层：服务器目录、.server.json、以及 jar 的启用状态。

这一层只碰文件和数据。需要用户拿主意的地方（删哪个、装哪个），
都通过参数或返回值交给界面层，自己不提问。
"""

from __future__ import annotations

import os
import shutil
from typing import Any, NamedTuple

from .errors import CoreError
from server_manager import (
    classify_server_type,
    ensure_servers_folder,
    list_servers,
    load_server_config,
    save_server_config,
)

DISABLED_SUFFIX = ".disabled"


class JarSet(NamedTuple):
    """某个目录下 jar 的两份清单。

    disabled 里的名字是带 .disabled 后缀的原始文件名，
    这样界面层展示和回传时不需要再拼一次后缀。
    """

    enabled: list[str]
    disabled: list[str]

    @property
    def total(self) -> int:
        return len(self.enabled) + len(self.disabled)

    def all(self) -> list[str]:
        return list(self.enabled) + list(self.disabled)


def plugins_dir(server_dir: str) -> str:
    return os.path.join(server_dir, "plugins")


def mods_dir(server_dir: str) -> str:
    return os.path.join(server_dir, "mods")


def list_jars(directory: str) -> JarSet:
    """扫描目录，分出启用与禁用的 jar。目录不存在时返回两个空清单。"""
    enabled: list[str] = []
    disabled: list[str] = []
    if not os.path.isdir(directory):
        return JarSet(enabled, disabled)
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return JarSet(enabled, disabled)
    for entry in entries:
        low = entry.lower()
        if low.endswith(".jar.disabled"):
            disabled.append(entry)
        elif low.endswith(".jar"):
            enabled.append(entry)
    return JarSet(enabled, disabled)


def set_jar_enabled(directory: str, filename: str, enabled: bool) -> str:
    """启用或禁用一个 jar（靠改后缀），返回改名后的文件名。

    enabled=True 走 .jar.disabled -> .jar，False 走反向。
    当前状态与目标状态一致时抛 CoreError，避免界面层自己判断。
    """
    full_path = os.path.join(directory, filename)
    if not os.path.isfile(full_path):
        raise CoreError("menu.plugin.add_file_missing", path=filename)

    if enabled:
        if not filename.endswith(DISABLED_SUFFIX):
            raise CoreError("menu.plugin.state_unrecognized")
        new_name = filename[: -len(DISABLED_SUFFIX)]
    else:
        if filename.endswith(DISABLED_SUFFIX):
            raise CoreError("menu.plugin.state_unrecognized")
        new_name = filename + DISABLED_SUFFIX

    new_path = os.path.join(directory, new_name)
    # 重名保护。以前这里直接 os.rename，而 Foo.jar 与 Foo.jar.disabled 是
    # 可以同时存在的（重新导入插件只按原名落盘，不会去看有没有禁用副本），
    # 于是同一个 jar 的两个状态文件撞在一起：POSIX 上 os.rename 静默覆盖，
    # 用户禁用插件就把那份启用的 jar 直接抹掉了；Windows 上抛
    # FileExistsError，被下面的 OSError 分支翻译成「切换失败」，错误信息
    # 指着一个根本不是原因的原因。两边都不对，所以先查重再改名。
    # 只放行「同一个文件仅改大小写」——那种情况两个路径指向同一个文件。
    if os.path.exists(new_path) and (
            os.path.normcase(os.path.abspath(new_path))
            != os.path.normcase(os.path.abspath(full_path))):
        raise CoreError("menu.plugin.toggle_conflict", path=new_name)

    try:
        os.rename(full_path, new_path)
    except OSError as e:
        raise CoreError("menu.plugin.toggle_failed", msg=str(e)) from e
    return new_name


def delete_jar(directory: str, filename: str) -> None:
    """删除一个 jar。文件不存在或删除失败都抛 CoreError。"""
    full_path = os.path.join(directory, filename)
    if not os.path.isfile(full_path):
        raise CoreError("menu.plugin.add_file_missing", path=filename)
    try:
        os.remove(full_path)
    except OSError as e:
        raise CoreError("menu.plugin.delete_failed", msg=str(e)) from e


def import_jar(directory: str, src_path: str) -> str:
    """把磁盘上的一个 jar 复制进目标目录，返回落地后的文件名。"""
    src_abs = os.path.abspath(src_path)
    if not os.path.isfile(src_abs):
        raise CoreError("menu.plugin.add_file_missing", path=src_abs)
    if not src_abs.lower().endswith(".jar"):
        raise CoreError("menu.plugin.add_not_jar")
    try:
        os.makedirs(directory, exist_ok=True)
        shutil.copy2(src_abs, directory)
    except OSError as e:
        raise CoreError("menu.plugin.add_copy_failed", msg=str(e)) from e
    return os.path.basename(src_abs)


def instance_kind(server_cfg: dict[str, Any], server_dir: str) -> str:
    """实例类型：plugin / mod / hybrid / vanilla / proxy / bedrock / unknown。"""
    return classify_server_type(server_cfg, server_dir)


__all__ = [
    "DISABLED_SUFFIX",
    "JarSet",
    "ensure_servers_folder",
    "list_servers",
    "load_server_config",
    "save_server_config",
    "plugins_dir",
    "mods_dir",
    "list_jars",
    "set_jar_enabled",
    "delete_jar",
    "import_jar",
    "instance_kind",
]
