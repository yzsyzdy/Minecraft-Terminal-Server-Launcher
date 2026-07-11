"""
交互式菜单：服务器列表选择、无服务器时的导入/下载引导、插件/模组管理。
"""

import sys
from typing import Any, Optional

from server_manager import (
    _prompt_zip_path,
    import_server_from_zip,
    list_servers,
    load_server_config,
    classify_server_type,
)


# ---------------------------------------------------------------------------
# 插件/模组管理（stub）
# ---------------------------------------------------------------------------

import os
import shutil


def _get_plugins_dir(server_dir: str) -> str:
    return os.path.join(server_dir, "plugins")


def _list_plugins(plugins_dir: str) -> tuple[list[str], list[str]]:
    """
    扫描 plugins/ 目录。
    返回 (启用的 .jar 文件名列表, 禁用的 .jar.disabled 文件名列表)。
    """
    enabled: list[str] = []
    disabled: list[str] = []
    if not os.path.isdir(plugins_dir):
        return enabled, disabled
    try:
        for entry in sorted(os.listdir(plugins_dir)):
            low = entry.lower()
            if low.endswith(".jar") and not low.endswith(".jar.disabled"):
                # 排除 .jar.disabled（由更精确的检查处理）
                if not entry.endswith(".disabled"):
                    enabled.append(entry)
            elif low.endswith(".jar.disabled"):
                disabled.append(entry)
            elif low.endswith(".disabled"):
                # 可能是 xxx.jar.disabled
                base = entry[:-9]  # 去掉 .disabled
                if base.lower().endswith(".jar"):
                    disabled.append(entry)
    except OSError:
        pass
    return enabled, disabled


def _search_and_download_plugin(server_name: str, plugins_dir: str) -> None:
    """搜索插件并下载。自动查询 SpigotMC、Modrinth、Hangar，按下载量排序。"""
    from plugin_sources import search_all, download_plugin

    print()
    query = input("  请输入插件名称或关键词: ").strip()
    if not query:
        return

    print(f"  [搜索] 正在查询 SpigotMC + Modrinth + Hangar ...")
    src_results = search_all(query, limit=5)

    # 合并为 (下载量, 源标签, 结果) 并排序
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

    # 展示
    print()
    print(f"  找到 {len(flat)} 个结果（按下载量排序）：")
    print()
    for i, (dl, src, r) in enumerate(flat, 1):
        name = r.get("name", "?")
        author = r.get("author", "?")
        desc = (r.get("description") or "")[:56]
        dl_str = f"{dl:,}" if dl >= 1000 else str(dl)
        print(f"  [{i:2d}] [{src:9s}] {name}")
        print(f"        作者: {author}  |  下载: {dl_str}")
        if desc:
            print(f"        {desc}")
        print()

    # 选择下载
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


def _show_plugin_menu(server_cfg: dict, server_dir: str) -> None:
    """插件管理菜单。"""
    name = server_cfg.get("name", "?")
    plugins_dir = _get_plugins_dir(server_dir)

    # 如果 plugins 目录不存在，先创建
    os.makedirs(plugins_dir, exist_ok=True)

    while True:
        enabled, disabled = _list_plugins(plugins_dir)
        total = len(enabled) + len(disabled)

        print()
        print(f"  [插件管理] 服务器: {name}")
        print(f"  插件目录: {plugins_dir}")
        print()

        if total == 0:
            print("  该服务器尚未安装任何插件。")
        else:
            print(f"  插件列表（共 {total} 个）：")
            print()
            for p in enabled:
                print(f"      [E] {p}")
            for p in disabled:
                print(f"      [D] {p}")
            print()
        print("  [E] = 启用  [D] = 已禁用")
        print()
        print("  [1] 删除插件")
        print("  [2] 禁用/启用插件")
        print("  [3] 添加插件")
        print("  [0] 返回")
        print()

        choice = input("  请选择 (0-3): ").strip()

        if choice == "0":
            return

        elif choice == "1":
            # 删除插件
            if total == 0:
                print("  没有插件可删除。")
                input("  按 Enter 返回...")
                continue
            target = input("  输入要删除的插件文件名: ").strip()
            if not target:
                continue
            full_path = os.path.join(plugins_dir, target)
            if not os.path.isfile(full_path):
                print(f"  未找到文件: {target}")
                input("  按 Enter 返回...")
                continue
            print(f"  确认删除 {target} ?")
            confirm = input("  输入 y 确认: ").strip().lower()
            if confirm == "y":
                try:
                    os.remove(full_path)
                    print(f"  已删除: {target}")
                except OSError as e:
                    print(f"  删除失败: {e}")
            else:
                print("  已取消。")
            input("  按 Enter 返回...")

        elif choice == "2":
            # 禁用/启用插件
            if total == 0:
                print("  没有插件可操作。")
                input("  按 Enter 返回...")
                continue
            target = input("  输入插件文件名: ").strip()
            if not target:
                continue
            full_path = os.path.join(plugins_dir, target)
            if not os.path.isfile(full_path):
                print(f"  未找到文件: {target}")
                input("  按 Enter 返回...")
                continue

            # 判断当前状态并切换
            if target.lower().endswith(".jar.disabled") or target.endswith(".disabled"):
                # 禁用 → 启用：去掉 .disabled
                new_name = target[:-9] if target.endswith(".disabled") else target
                if new_name == target:
                    print("  无法识别插件状态。")
                    input("  按 Enter 返回...")
                    continue
                try:
                    os.rename(full_path, os.path.join(plugins_dir, new_name))
                    print(f"  已启用: {new_name}")
                except OSError as e:
                    print(f"  操作失败: {e}")
            else:
                # 启用 → 禁用：添加 .disabled
                new_name = target + ".disabled"
                try:
                    os.rename(full_path, os.path.join(plugins_dir, new_name))
                    print(f"  已禁用: {new_name}")
                except OSError as e:
                    print(f"  操作失败: {e}")
            input("  按 Enter 返回...")

        elif choice == "3":
            # 添加插件 — 子菜单
            while True:
                print()
                print(f"  [添加插件] 服务器: {name}")
                print()
                print("  [1] 导入 jar 文件")
                print("  [1] 导入 jar 文件")
                print("  [2] 从 SpigotMC/Modrinth/Hangar 搜索下载")
                print("  [0] 返回")
                print()
                sub = input("  请选择 (0-2): ").strip()

                if sub == "0":
                    break

                elif sub == "1":
                    print()
                    print("  请输入 jar 文件路径（支持拖拽）：")
                    jar_path = input("  > ").strip().strip('"')
                    if not jar_path:
                        continue
                    jar_abs = os.path.abspath(jar_path)
                    if not os.path.isfile(jar_abs):
                        print(f"  文件不存在: {jar_abs}")
                        input("  按 Enter 返回...")
                        continue
                    if not jar_abs.lower().endswith(".jar"):
                        print("  文件不是 .jar 格式。")
                        input("  按 Enter 返回...")
                        continue
                    try:
                        shutil.copy2(jar_abs, plugins_dir)
                        print(f"  已导入: {os.path.basename(jar_abs)}")
                    except OSError as e:
                        print(f"  复制失败: {e}")
                    input("  按 Enter 返回...")

                elif sub == "2":
                    _search_and_download_plugin(name, plugins_dir)

                else:
                    print("  无效选择。")

        else:
            print(f"  无效选择。")


def _show_mod_menu(server_cfg: dict, server_dir: str) -> None:
    """模组管理菜单（stub）。"""
    name = server_cfg.get("name", "?")
    print()
    print(f"  [模组管理] 服务器: {name}")
    print()
    print("  功能开发中。")
    print()
    input("  按 Enter 返回...")


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
    elif stype == "vanilla":
        print()
        print(f"  [{server_cfg.get('name', '?')}] 原版服务端不支持插件或模组。")
        print()
        input("  按 Enter 返回...")
    elif stype == "proxy":
        print()
        print(f"  [{server_cfg.get('name', '?')}] 代理端不支持插件或模组管理。")
        print()
        input("  按 Enter 返回...")
    elif stype == "bedrock":
        print()
        print(f"  [{server_cfg.get('name', '?')}] 基岩版服务端不支持此功能。")
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
                # 管理完成后回到子菜单
                continue
        except ValueError:
            pass
        print("  无效选择。")


# ---------------------------------------------------------------------------
# 主菜单
# ---------------------------------------------------------------------------

def show_no_server_menu(servers_dir: str, project_dir: str) -> None:
    """servers/ 为空时显示导入/下载选项。"""
    while True:
        print()
        print("  servers/ 文件夹中没有找到任何服务器。")
        print()
        print("  [1] 导入服务器压缩包")
        print("  [2] 下载服务器")
        print("  [0] 退出")
        print()
        choice = input("  请选择 (0-2): ").strip()

        if choice == "0":
            print("[退出] 用户选择退出")
            sys.exit(0)

        elif choice == "1":
            zip_path = _prompt_zip_path()
            if zip_path is None:
                input("  按 Enter 返回菜单...")
                continue
            if import_server_from_zip(zip_path, servers_dir, project_dir) is not None:
                print(); print("  导入完成！"); print()
                input("  按 Enter 返回...")
                return
            input("  按 Enter 返回菜单...")
            continue

        elif choice == "2":
            from download_msl import show_download_server_menu
            result = show_download_server_menu(servers_dir, project_dir)
            if result is not None:
                print(); print("  下载完成！"); print()
                input("  按 Enter 返回...")
                return
            input("  按 Enter 返回菜单...")
            continue

        else:
            print(f"  无效选择，请输入 0-2。")


def select_server_interactive(
    servers: list[dict[str, Any]],
    servers_dir: str = "",
    project_dir: str = "",
) -> Optional[dict[str, Any]]:
    """
    列出服务器，末尾附加导入/下载/管理选项。
    返回选中服务器的配置，或 None 表示返回重新扫描。
    """
    can_add_new = bool(servers_dir and project_dir)

    while True:
        print()
        print(f"  找到 {len(servers)} 个服务器：")
        print()
        for i, s in enumerate(servers, 1):
            ver = s.get("mc_version", "?")
            jar = s.get("jar", "?")
            mem = f"{s.get('min_mem', '?')} / {s.get('max_mem', '?')}"
            print(f"  [{i}] {s['name']}  (MC {ver} | {jar} | {mem})")

        idx_import = len(servers) + 1
        idx_download = len(servers) + 2
        idx_manage = len(servers) + 3
        max_choice = idx_manage
        print()
        if can_add_new:
            print(f"  [{idx_import}] 导入新服务器（压缩包）")
            print(f"  [{idx_download}] 下载新服务器")
            print(f"  [{idx_manage}] 管理插件/模组")
        print("  [0] 退出")
        print()

        prompt = f"  请选择 (0-{max_choice}): " if can_add_new else f"  请选择 (1-{len(servers)}): "
        choice = input(prompt).strip()

        if choice == "0":
            print("[退出] 用户选择退出")
            sys.exit(0)

        if can_add_new:
            try:
                int_c = int(choice)
                if int_c == idx_import:
                    zp = _prompt_zip_path()
                    if zp is None:
                        input("  按 Enter 返回菜单..."); continue
                    if import_server_from_zip(zp, servers_dir, project_dir) is not None:
                        servers.clear(); servers.extend(list_servers(servers_dir))
                    else:
                        input("  按 Enter 返回菜单...")
                    continue
                elif int_c == idx_download:
                    from download_msl import show_download_server_menu
                    r = show_download_server_menu(servers_dir, project_dir)
                    if r is not None:
                        servers.clear(); servers.extend(list_servers(servers_dir))
                    else:
                        input("  按 Enter 返回菜单...")
                    continue
                elif int_c == idx_manage:
                    _pick_server_for_management(servers, servers_dir)
                    continue
            except ValueError:
                pass

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                return servers[idx]
        except ValueError:
            pass
        print(f"  无效选择，请输入 0-{max_choice} 之间的数字。")
