"""
Minecraft 服务器启动器

从 config.json 读取配置，管理 servers/ 下的多服务器实例。
"""

import os
import sys

from config import load_config, save_config
from server_manager import ensure_servers_folder, list_servers, load_server_config
from menu import show_no_server_menu, select_server_interactive
from java_tools import resolve_java
from server_launcher import start_minecraft_server, start_server_interactive


def _is_eula_unagreed(server_path: str) -> bool:
    """检查 eula.txt 是否存在且内容为 eula=false。"""
    eula_path = os.path.join(server_path, "eula.txt")
    if not os.path.isfile(eula_path):
        return False
    try:
        with open(eula_path, "r", encoding="utf-8") as f:
            content = f.read()
        return "eula=false" in content
    except OSError:
        return False


def _agree_to_eula(server_path: str) -> bool:
    """将 eula.txt 中的 eula=false 改为 eula=true。成功返回 True。"""
    eula_path = os.path.join(server_path, "eula.txt")
    try:
        with open(eula_path, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("eula=false", "eula=true")
        with open(eula_path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError:
        return False


def _prompt_eula(name: str) -> bool:
    """询问用户是否同意 Minecraft EULA。返回 True 表示同意。"""
    print()
    print("  =============================================")
    print("   Minecraft 最终用户许可协议 (EULA)")
    print("   https://aka.ms/MinecraftEULA")
    print("  =============================================")
    print()
    print(f"  服务器 \"{name}\" 需要您同意 EULA 才能运行。")
    print()
    ans = input("  是否同意 Minecraft EULA？(y/N): ").strip().lower()
    return ans == "y"


def main():
    from clear import clear_screen
    clear_screen()
    project_dir = os.path.dirname(os.path.abspath(__file__))

    config = load_config(project_dir)
    save_config(config, project_dir)

    servers_dir = ensure_servers_folder(project_dir)
    print("=" * 50)
    print("  Minecraft 服务器启动器")
    print(f"  服务器目录: {servers_dir}")
    print("=" * 50)

    servers = list_servers(servers_dir)
    if not servers:
        show_no_server_menu(servers_dir, project_dir)
        servers = list_servers(servers_dir)
        if not servers:
            print("[退出] servers/ 中没有服务器，程序退出。")
            sys.exit(0)

    selected = select_server_interactive(servers, servers_dir=servers_dir, project_dir=project_dir)
    server_path = selected["_path"]
    server_cfg = load_server_config(server_path)

    server_java = server_cfg.get("java_path") or config.get("java_path")
    try:
        mc_ver = server_cfg.get("mc_version", "")
        java_abs = resolve_java(
            configured_path=server_java,
            storage_dir=project_dir,
            mc_version=mc_ver,
            project_dir=project_dir,
        )
    except FileNotFoundError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    jar_rel = server_cfg.get("jar", "server.jar")
    jar_abs = os.path.abspath(os.path.join(server_path, jar_rel))
    if not os.path.isfile(jar_abs):
        print(f"[错误] 服务器核心文件未找到: {jar_abs}")
        sys.exit(1)

    min_mem = server_cfg.get("min_mem", "1G")
    max_mem = server_cfg.get("max_mem", "4G")
    extra_jvm = server_cfg.get("extra_jvm_args") or None
    extra_srv = server_cfg.get("extra_server_args") or None
    interactive = config.get("interactive", True)
    name = server_cfg.get("name", "?")

    print()
    print(f"  服务器:    {name}")
    print(f"  MC 版本:   {server_cfg.get('mc_version', '?')}")
    print(f"  Java:      {java_abs}")
    print(f"  核心:      {jar_abs}")
    print(f"  内存:      最小 {min_mem} / 最大 {max_mem}")
    print()

    # 启动循环（支持 EULA 同意后自动重启）
    first_start = True
    while True:
        try:
            if interactive:
                exit_code = start_server_interactive(
                    java_path=java_abs, jar_path=jar_abs,
                    min_mem=min_mem, max_mem=max_mem,
                    extra_jvm_args=extra_jvm, extra_server_args=extra_srv,
                )
            else:
                exit_code = start_minecraft_server(
                    java_path=java_abs, jar_path=jar_abs,
                    min_mem=min_mem, max_mem=max_mem,
                    extra_jvm_args=extra_jvm, extra_server_args=extra_srv,
                )
        except FileNotFoundError as e:
            print(f"[错误] {e}")
            sys.exit(1)
        except KeyboardInterrupt:
            print("\n[关闭] 用户中断")
            sys.exit(0)

        # 检查 EULA
        if _is_eula_unagreed(server_path):
            print()
            print(f"  [EULA] 服务器因未同意 EULA 而退出。")
            if _prompt_eula(name):
                if _agree_to_eula(server_path):
                    print("  [EULA] 已同意，正在自动重启服务器...")
                    print()
                    first_start = False
                    continue  # 自动重启
                else:
                    print("  [EULA] 修改 eula.txt 失败，请手动修改。")
            else:
                print("  [EULA] 已拒绝。")

        # 正常退出或无需 EULA 处理
        break

    if first_start:
        print(f"\n[完成] 服务器 \"{name}\" 已关闭，退出码: {exit_code}")
    else:
        print(f"\n[完成] 服务器 \"{name}\" 已关闭，退出码: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
