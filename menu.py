"""
交互式菜单：服务器列表选择、无服务器时的导入/下载引导、插件/模组管理。
"""

import sys
import os
import shutil
from typing import Any, Optional

from config import load_config
from clear import clear_screen
from server_manager import (
    _prompt_zip_path,
    import_server_from_zip,
    list_servers,
    load_server_config,
    classify_server_type,
)


# ---------------------------------------------------------------------------
# 通用 jar 管理
# ---------------------------------------------------------------------------

def _list_jars(directory: str) -> tuple[list[str], list[str]]:
    """扫描目录，返回 (启用的 .jar 文件名列表, 禁用的 .jar.disabled 文件名列表)。"""
    enabled: list[str] = []
    disabled: list[str] = []
    if not os.path.isdir(directory):
        return enabled, disabled
    try:
        for entry in sorted(os.listdir(directory)):
            low = entry.lower()
            if low.endswith(".jar") and not entry.endswith(".disabled"):
                enabled.append(entry)
            elif low.endswith(".jar.disabled"):
                disabled.append(entry)
            elif low.endswith(".disabled"):
                base = entry[:-9]
                if base.lower().endswith(".jar"):
                    disabled.append(entry)
    except OSError:
        pass
    return enabled, disabled


def _delete_jar(directory: str) -> None:
    """交互式删除 jar 文件。"""
    enabled, disabled = _list_jars(directory)
    if not enabled and not disabled:
        print("  没有文件可删除。")
        input("  按 Enter 返回...")
        return
    target = input("  输入要删除的文件名: ").strip()
    if not target:
        return
    full_path = os.path.join(directory, target)
    if not os.path.isfile(full_path):
        print(f"  未找到文件: {target}")
        input("  按 Enter 返回...")
        return
    confirm = input(f"  确认删除 {target} ? (y/N): ").strip().lower()
    if confirm == "y":
        try:
            os.remove(full_path)
            print(f"  已删除: {target}")
        except OSError as e:
            print(f"  删除失败: {e}")
    else:
        print("  已取消。")
    input("  按 Enter 返回...")


def _toggle_jar(directory: str) -> None:
    """交互式禁用/启用 jar 文件（.jar <-> .jar.disabled）。"""
    enabled, disabled = _list_jars(directory)
    if not enabled and not disabled:
        print("  没有文件可操作。")
        input("  按 Enter 返回...")
        return
    target = input("  输入文件名: ").strip()
    if not target:
        return
    full_path = os.path.join(directory, target)
    if not os.path.isfile(full_path):
        print(f"  未找到文件: {target}")
        input("  按 Enter 返回...")
        return
    if target.endswith(".disabled"):
        new_name = target[:-9]
        if new_name == target:
            print("  无法识别状态。")
            input("  按 Enter 返回...")
            return
        try:
            os.rename(full_path, os.path.join(directory, new_name))
            print(f"  已启用: {new_name}")
        except OSError as e:
            print(f"  操作失败: {e}")
    else:
        try:
            os.rename(full_path, os.path.join(directory, target + ".disabled"))
            print(f"  已禁用: {target + '.disabled'}")
        except OSError as e:
            print(f"  操作失败: {e}")
    input("  按 Enter 返回...")


def _add_jar_from_file(directory: str) -> None:
    """交互式导入本地 jar 文件。"""
    print()
    print("  请输入 jar 文件路径（支持拖拽）：")
    jar_path = input("  > ").strip().strip('"')
    if not jar_path:
        return
    jar_abs = os.path.abspath(jar_path)
    if not os.path.isfile(jar_abs):
        print(f"  文件不存在: {jar_abs}")
        input("  按 Enter 返回...")
        return
    if not jar_abs.lower().endswith(".jar"):
        print("  文件不是 .jar 格式。")
        input("  按 Enter 返回...")
        return
    try:
        shutil.copy2(jar_abs, directory)
        print(f"  已导入: {os.path.basename(jar_abs)}")
    except OSError as e:
        print(f"  复制失败: {e}")
    input("  按 Enter 返回...")


# ---------------------------------------------------------------------------
# 插件管理
# ---------------------------------------------------------------------------

def _get_plugins_dir(server_dir: str) -> str:
    return os.path.join(server_dir, "plugins")


def _search_and_download_plugin(server_name: str, plugins_dir: str) -> None:
    """搜索插件并下载。自动查询 SpigotMC、Modrinth、Hangar，按下载量排序。"""
    clear_screen()
    from plugin_sources import search_all, download_plugin

    print()
    query = input("  请输入插件名称或关键词: ").strip()
    if not query:
        return

    print(f"  [搜索] 正在查询 SpigotMC + Modrinth + Hangar ...")
    src_results = search_all(query, limit=5)

    flat: list[tuple[int, str, Any]] = []
    for label, items in src_results.items():
        for item in items:
            dl = item.get("downloads", 0) or 0
            flat.append((dl, label, item))
    flat.sort(key=lambda x: x[0], reverse=True)

    if not flat:
        print("  未找到匹配的插件。")
        input("  按 Enter 返回...")
        return

    print()
    print(f"  找到 {len(flat)} 个结果（按下载量排序）：")
    print()
    for i, (dl, src, r) in enumerate(flat, 1):
        nm = r.get("name", "?")
        author = r.get("author", "?")
        desc = (r.get("description") or "")[:56]
        dl_str = f"{dl:,}" if dl >= 1000 else str(dl)
        print(f"  [{i:2d}] [{src:9s}] {nm}")
        print(f"        作者: {author}  |  下载: {dl_str}")
        if desc:
            print(f"        {desc}")
        print()

    while True:
        try:
            c = input(f"  输入编号下载 (1-{len(flat)}, 0 返回): ").strip()
            if c == "0":
                return
            idx = int(c) - 1
            if 0 <= idx < len(flat):
                _, src_label, result = flat[idx]
                print(f"  [下载] 正在从 {src_label} 下载 {result['name']} ...")
                filename = download_plugin(result, plugins_dir)
                if filename:
                    print(f"  [下载] 已保存: {filename}")
                else:
                    print(f"  [错误] 下载失败，可能需要手动下载。")
                input("  按 Enter 返回...")
                return
        except ValueError:
            pass
        print("  无效选择。")


def _manage_jars_menu(
    name: str,
    directory: str,
    header: str,
    search_fn: Any,
) -> None:
    """通用的插件/模组管理菜单。"""
    os.makedirs(directory, exist_ok=True)

    while True:
        enabled, disabled = _list_jars(directory)
        total = len(enabled) + len(disabled)
        clear_screen()
        print()
        print(f"  [{header}] 服务器: {name}")
        print(f"  目录: {directory}")
        print()
        if total == 0:
            print("  尚未安装任何文件。")
        else:
            print(f"  共 {total} 个：")
            print()
            for p in enabled:
                print(f"      [E] {p}")
            for p in disabled:
                print(f"      [D] {p}")
            print()
        print("  [E] = 启用  [D] = 已禁用")
        print()
        print("  [1] 删除")
        print("  [2] 禁用/启用")
        print("  [3] 添加")
        print("  [0] 返回")
        print()
        choice = input("  请选择 (0-3): ").strip()

        if choice == "0":
            return
        elif choice == "1":
            _delete_jar(directory)
        elif choice == "2":
            _toggle_jar(directory)
        elif choice == "3":
            while True:
                print()
                print(f"  [添加{header}] 服务器: {name}")
                print()
                print("  [1] 导入 jar 文件")
                if search_fn:
                    print("  [2] 搜索下载")
                print("  [0] 返回")
                print()
                sub = input("  请选择 (0-2): ").strip()
                if sub == "0":
                    break
                elif sub == "1":
                    _add_jar_from_file(directory)
                elif sub == "2" and search_fn:
                    search_fn()
                else:
                    print("  无效选择。")
        else:
            print("  无效选择。")


def _show_plugin_menu(server_cfg: dict, server_dir: str) -> None:
    """插件管理菜单。"""
    name = server_cfg.get("name", "?")
    plugins_dir = _get_plugins_dir(server_dir)
    _manage_jars_menu(
        name=name,
        directory=plugins_dir,
        header="插件管理",
        search_fn=lambda: _search_and_download_plugin(name, plugins_dir),
    )


# ---------------------------------------------------------------------------
# 模组管理
# ---------------------------------------------------------------------------

def _get_mods_dir(server_dir: str) -> str:
    return os.path.join(server_dir, "mods")


def _search_and_download_mod(name: str, mods_dir: str,
                              mc_version: str, loader: str) -> None:
    """搜索模组并下载，自动按服务器版本和加载器过滤。"""
    clear_screen()
    from mod_sources import search_all_mods, download_mod, set_curseforge_api_key

    print()
    query = input("  请输入模组名称或关键词: ").strip()
    if not query:
        return

    # 通过向上查找 config.json 来确定项目目录
    def _find_project_dir(path: str) -> str:
        current = os.path.abspath(path)
        while True:
            if os.path.isfile(os.path.join(current, "config.json")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                return os.getcwd()
            current = parent

    cfg = load_config(_find_project_dir(mods_dir))
    cf_key = cfg.get("curseforge_api_key", "")
    if cf_key:
        set_curseforge_api_key(cf_key)

    version_info = f"{loader} {mc_version}" if mc_version else loader
    print(f"  [搜索] 正在查询 Modrinth + CurseForge（过滤: {version_info}）...")
    src_results = search_all_mods(query, mc_version, loader, limit=10)

    is_fallback = src_results.pop("_fallback", False)

    src_used = list(src_results.keys())
    if not src_used:
        print(f"  未找到匹配的模组。")
        if not cf_key:
            print("  提示：如需搜索 CurseForge，请在 config.json 中配置 curseforge_api_key")
        input("  按 Enter 返回...")
        return

    if not cf_key:
        print("  提示：仅搜索了 Modrinth（未配置 CurseForge API key）")
    if is_fallback:
        print(f"  [提示] 在当前版本范围未找到足够结果，已自动放宽搜索范围。")

    flat: list[tuple[int, str, dict]] = []
    for label, items in src_results.items():
        for item in items:
            dl = item.get("downloads", 0) or 0
            flat.append((dl, label, item))
    flat.sort(key=lambda x: x[0], reverse=True)

    print()
    print(f"  找到 {len(flat)} 个结果（按下载量排序）：")
    print()
    for i, (dl, src, r) in enumerate(flat, 1):
        nm = r.get("name", "?")
        author = r.get("author", "?")
        desc = (r.get("description") or "")[:60]
        dl_str = f"{dl:,}" if dl >= 1000 else str(dl)
        print(f"  [{i:2d}] [{src:10s}] {nm}")
        print(f"        作者: {author}  |  下载: {dl_str}")
        if desc:
            print(f"        {desc}")
        print()

    while True:
        try:
            c = input(f"  输入编号下载 (1-{len(flat)}, 0 返回): ").strip()
            if c == "0":
                return
            idx = int(c) - 1
            if 0 <= idx < len(flat):
                _, src_label, result = flat[idx]
                print(f"  [下载] 正在从 {src_label} 下载 {result['name']} ...")
                filename = download_mod(result, mods_dir, mc_version, loader)
                if filename:
                    print(f"  [下载] 已保存: {filename}")
                else:
                    print(f"  [错误] 下载失败，可能需要手动下载。")
                input("  按 Enter 返回...")
                return
        except ValueError:
            pass
        print("  无效选择。")


def _show_mod_menu(server_cfg: dict, server_dir: str) -> None:
    """模组管理菜单。"""
    name = server_cfg.get("name", "?")
    mc_version = server_cfg.get("mc_version", "")
    from mod_sources import extract_mod_loader
    loader = extract_mod_loader(name)

    mods_dir = _get_mods_dir(server_dir)
    _manage_jars_menu(
        name=name,
        directory=mods_dir,
        header="模组管理",
        search_fn=lambda: _search_and_download_mod(name, mods_dir, mc_version, loader),
    )


# ---------------------------------------------------------------------------
# 服务器类型分发
# ---------------------------------------------------------------------------

def _run_management_for_server(server_cfg: dict, server_dir: str) -> None:
    """检查服务器类型并跳转到对应的管理菜单。"""
    stype = classify_server_type(server_cfg, server_dir)

    if stype == "plugin":
        _show_plugin_menu(server_cfg, server_dir)
    elif stype == "mod":
        _show_mod_menu(server_cfg, server_dir)
    elif stype == "hybrid":
        print()
        print(f"  [{server_cfg.get('name', '?')}] 检测到混合端（同时支持插件和模组）")
        print()
        print("  [1] 管理插件")
        print("  [2] 管理模组")
        print("  [0] 返回")
        print()
        while True:
            c = input("  请选择 (0-2): ").strip()
            if c == "1":
                _show_plugin_menu(server_cfg, server_dir); return
            elif c == "2":
                _show_mod_menu(server_cfg, server_dir); return
            elif c == "0":
                return
            print("  无效选择。")
    elif stype in ("vanilla", "proxy", "bedrock"):
        msg = {"vanilla": "原版服务端不支持插件或模组。",
               "proxy": "代理端不支持插件或模组管理。",
               "bedrock": "基岩版服务端不支持此功能。"}
        print()
        print(f"  [{server_cfg.get('name', '?')}] {msg[stype]}")
        print()
        input("  按 Enter 返回...")
    else:
        print()
        print(f"  [{server_cfg.get('name', '?')}] 无法识别服务端类型。")
        print()
        input("  按 Enter 返回...")


def _pick_server_for_management(servers: list[dict], servers_dir: str) -> None:
    """让用户选择要管理插件/模组的服务器。"""
    while True:
        clear_screen()
        print()
        print("  选择要管理的服务器：")
        print()
        for i, s in enumerate(servers, 1):
            ver = s.get("mc_version", "?")
            jar = s.get("jar", "?")
            print(f"  [{i}] {s['name']}  (MC {ver} | {jar})")
        print("  [0] 返回")
        print()
        try:
            c = input(f"  请输入编号 (0-{len(servers)}): ").strip()
            if c == "0":
                return
            idx = int(c) - 1
            if 0 <= idx < len(servers):
                server_dir = servers[idx]["_path"]
                server_cfg = load_server_config(server_dir)
                _run_management_for_server(server_cfg, server_dir)
        except ValueError:
            pass
        print("  无效选择。")


# ---------------------------------------------------------------------------
# 主菜单
# ---------------------------------------------------------------------------

def show_main_menu(servers_dir: str, project_dir: str) -> Optional[dict[str, Any]]:
    """
    主菜单。
    返回选中的服务器配置（启动服务器）或 None（退出）。
    """
    while True:
        servers = list_servers(servers_dir)
        server_count = len(servers)

        clear_screen()
        print()
        print(f"  MSTL — Minecraft Terminal Server Launcher")
        print(f"  服务器目录: {servers_dir}")
        print()

        if server_count == 0:
            print("  当前没有已配置的服务器。")
        else:
            print(f"  [{1}] 启动服务器 ({server_count} 个可用)")
        print("  [2] 导入服务器压缩包")
        print("  [3] 下载服务器")
        print("  [4] 管理插件/模组")
        print("  [0] 退出")
        print()

        choice = input("  请选择 (0-4): ").strip()

        if choice == "0":
            print("[退出] 用户选择退出")
            sys.exit(0)

        elif choice == "1":
            if server_count == 0:
                print("  没有可用服务器，请先导入或下载。")
                input("  按 Enter 返回...")
                continue
            selected = _select_server_for_launch(servers, servers_dir)
            if selected:
                return selected
            continue

        elif choice == "2":
            zip_path = _prompt_zip_path()
            if zip_path is None:
                input("  按 Enter 返回...")
                continue
            import_server_from_zip(zip_path, servers_dir, project_dir)
            input("  按 Enter 返回...")
            continue

        elif choice == "3":
            from download_msl import show_download_server_menu
            show_download_server_menu(servers_dir, project_dir)
            input("  按 Enter 返回...")
            continue

        elif choice == "4":
            servers_now = list_servers(servers_dir)
            if servers_now:
                _pick_server_for_management(servers_now, servers_dir)
            else:
                print("  没有可管理的服务器。")
                input("  按 Enter 返回...")
            continue

        else:
            print("  无效选择，请输入 0-4。")


def _select_server_for_launch(
    servers: list[dict[str, Any]],
    servers_dir: str,
) -> Optional[dict[str, Any]]:
    """列出服务器供用户选择启动，返回选中服务器的配置。"""
    while True:
        clear_screen()
        print()
        print("  选择要启动的服务器：")
        print()
        for i, s in enumerate(servers, 1):
            ver = s.get("mc_version", "?")
            jar = s.get("jar", "?")
            mem = f"{s.get('min_mem', '?')} / {s.get('max_mem', '?')}"
            print(f"  [{i}] {s['name']}  (MC {ver} | {jar} | {mem})")
        print("  [0] 返回")
        print()

        choice = input(f"  请选择 (0-{len(servers)}): ").strip()

        if choice == "0":
            return None

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                return servers[idx]
        except ValueError:
            pass
        print(f"  无效选择，请输入 0-{len(servers)} 之间的数字。")
