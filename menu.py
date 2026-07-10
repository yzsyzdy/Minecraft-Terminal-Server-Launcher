"""
交互式菜单：服务器列表选择、无服务器时的导入/下载引导。
"""

import sys
from typing import Any, Optional

from server_manager import (
    _prompt_zip_path,
    import_server_from_zip,
    list_servers,
)


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
    """列出服务器，末尾附加导入/下载选项。返回选中服务器的配置。"""
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
        max_choice = idx_download
        print()
        if can_add_new:
            print(f"  [{idx_import}] 导入新服务器（压缩包）")
            print(f"  [{idx_download}] 下载新服务器")
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
            except ValueError:
                pass

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                return servers[idx]
        except ValueError:
            pass
        print(f"  无效选择，请输入 0-{max_choice} 之间的数字。")
