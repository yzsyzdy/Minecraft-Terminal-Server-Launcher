"""
MSTL — Minecraft Terminal Server Launcher

启动入口。完整流程:
  1. 加载 config.json，检测语言
  2. 显示主菜单（show_main_menu）
  3. 用户选择服务器后进入 _start_server
  4. _start_server 按需启动 OpenFrp frpc 隧道
  5. 解析 Java、验证核心 jar
  6. 启动 Minecraft 服务端（交互或非交互模式）
  7. 处理 EULA 协议（首次运行时需要同意）
  8. 服务器退出后清理 frpc 进程

Copyright (C) 2026 yzsyzdy
License: GNU General Public License v3.0
"""

import os
import sys

from config import load_config, save_config
from server_manager import ensure_servers_folder, load_server_config, list_servers
from menu import show_main_menu
from java_tools import resolve_java
from server_launcher import start_minecraft_server, start_server_interactive
from i18n import t, load_language, detect_os_language
from constants import REPO_URL


# ---------------------------------------------------------------------------
# EULA 协议处理
# Mojang 要求首次运行服务器前同意 EULA
# ---------------------------------------------------------------------------

def _is_eula_unagreed(server_path: str) -> bool:
    """检查 eula.txt 中是否包含 eula=false。

    服务端首次运行会自动生成 eula.txt。如果用户还没同意，
    文件内容就是 eula=false。检测到后提示用户同意。
    """
    eula_path = os.path.join(server_path, "eula.txt")
    if not os.path.isfile(eula_path):
        return False  # 还没生成文件，服务器还没跑过
    try:
        with open(eula_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() == "eula=false":
                    return True
        return False
    except OSError:
        return False


def _agree_to_eula(server_path: str) -> bool:
    """将 eula.txt 中的 eula=false 改为 eula=true。"""
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
    """向用户显示 EULA 协议并确认。"""
    print()
    print(t("eula.notice"))
    print()
    print(t("eula.prompt", name=name))
    print()
    ans = input(t("eula.ask")).strip().lower()
    return ans == "y"


def _project_dir() -> str:
    """获取项目根目录（用户数据存放位置）。"""
    from paths import app_dir
    return app_dir()


# ---------------------------------------------------------------------------
# 核心启动流程
# ---------------------------------------------------------------------------

def _maybe_start_frpc(server_cfg: dict, project_dir: str) -> None:
    """按实例配置里的 openfrp 段决定要不要先拉起 frpc 隧道。

    Minecraft 实例和通用实例共用这一段：隧道指向哪个本地端口是用户自己配的，
    和实例种类无关。
    """
    of_cfg = server_cfg.get("openfrp", {}) or {}
    if not of_cfg.get("auto_start"):
        return
    if not (of_cfg.get("token") and of_cfg.get("proxy_id")):
        print("  [OpenFrp] 已开启自动启动，但 token 或隧道 ID 没填，跳过。")
        return

    from openfrp.tunnel_manager import launch_frpc

    print()
    if launch_frpc(project_dir, server_cfg) is None:
        print("  [警告] frpc 启动失败，隧道可能不可用")
    print()


def _start_generic_instance(server_cfg: dict, server_path: str,
                            config: dict, project_dir: str) -> int:
    """启动一个通用实例：不解析 Java、不校验 jar，按启动配置直接跑。

    退出后不重试、也不看 EULA——那套自动重启是给 Minecraft 服务端首启动写的，
    通用实例的退出码就是它自己的退出码。
    """
    from core.errors import CoreError
    from core.runtime import build_launch_plan
    from server_launcher import start_command

    try:
        plan = build_launch_plan(server_cfg, server_path, project_dir)
    except CoreError as e:
        print("  " + t(e.key, **e.kwargs))
        return 1

    _maybe_start_frpc(server_cfg, project_dir)

    name = server_cfg.get("name", "?")
    proc_prio = server_cfg.get("process_priority", "normal")
    print("━" * 50)
    print()
    print(t("server.info_name", name=name))
    print(t("server.info_command", cmd=" ".join(plan.cmd)))
    print(t("server.info_workdir", path=plan.workdir))
    if proc_prio != "normal":
        print(t("server.info_priority", priority=proc_prio))
    print()

    try:
        return start_command(
            plan.cmd, plan.workdir, proc_prio,
            interactive=config.get("interactive", True),
            title=name,
        )
    except FileNotFoundError as e:
        print(t("app.error_file_not_found", msg=str(e)))
        return 1
    except KeyboardInterrupt:
        print(t("app.cancel"))
        return 0


def _start_server(server_cfg: dict, server_path: str,
                  config: dict, project_dir: str) -> int:
    """启动一个实例的完整流程。

    Minecraft 实例:
      1. 解析 Java 路径（自动检测/缓存/下载）
      2. 验证核心 jar 存在
      3. 按需启动 OpenFrp frpc 隧道
      4. 显示服务器信息摘要
      5. 启动 MC 服务端（交互/非交互）
      6. 处理 EULA 协议（如果需要）
      7. 返回服务端退出码

    通用实例只走自己的启动配置（见 _start_generic_instance）。
    """
    if (server_cfg.get("kind") or "minecraft") == "generic":
        return _start_generic_instance(server_cfg, server_path, config, project_dir)

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
        print(t("app.error_file_not_found", msg=str(e)))
        return 1

    jar_rel = server_cfg.get("jar", "server.jar")
    jar_abs = os.path.abspath(os.path.join(server_path, jar_rel))
    if not os.path.isfile(jar_abs):
        print(t("app.error_jar_missing", path=jar_abs))
        return 1

    min_mem = server_cfg.get("min_mem", "1G")
    max_mem = server_cfg.get("max_mem", "4G")
    extra_jvm = server_cfg.get("extra_jvm_args") or None
    extra_srv = server_cfg.get("extra_server_args") or None
    interactive = config.get("interactive", True)
    name = server_cfg.get("name", "?")

    # 如果需要内网穿透，先在 MC 服务器之前启动 frpc
    _maybe_start_frpc(server_cfg, project_dir)

    # 用分隔线把 frpc 输出和服务器信息分开
    print("━" * 50)
    print()
    print(t("server.info_name", name=name))
    print(t("server.info_mc", ver=server_cfg.get("mc_version", "?")))
    print(t("server.info_java", path=java_abs))
    print(t("server.info_jar", path=jar_abs))
    proc_prio = server_cfg.get("process_priority", "normal")
    print(t("server.info_memory", min=min_mem, max=max_mem))
    if proc_prio != "normal":
        print(t("server.info_priority", priority=proc_prio))
    print()

    # 主循环: 启动 → 检查 EULA → 自动重启
    while True:
        try:
            if interactive:
                exit_code = start_server_interactive(
                    java_path=java_abs, jar_path=jar_abs,
                    min_mem=min_mem, max_mem=max_mem,
                    extra_jvm_args=extra_jvm, extra_server_args=extra_srv,
                    process_priority=proc_prio,
                )
            else:
                exit_code = start_minecraft_server(
                    java_path=java_abs, jar_path=jar_abs,
                    min_mem=min_mem, max_mem=max_mem,
                    extra_jvm_args=extra_jvm, extra_server_args=extra_srv,
                    process_priority=proc_prio,
                )
        except FileNotFoundError as e:
            print(t("app.error_file_not_found", msg=str(e)))
            return 1
        except KeyboardInterrupt:
            # 由 server_launcher 中的 SIGINT 处理器抛出，
            # 子进程已被终止，安全返回
            print(t("app.cancel"))
            return 0

        # EULA 检查: 如果服务器因为 eula=false 退出，提示用户同意
        if not _is_eula_unagreed(server_path):
            break

        print()
        print(t("eula.exit_reason"))
        if _prompt_eula(name):
            if _agree_to_eula(server_path):
                print(t("eula.agreed"))
                print()
                continue  # 自动重启服务器
            else:
                print(t("eula.failed"))
        else:
            print(t("eula.refused"))
        break

    return exit_code


def _dispatch_web(argv: list[str]) -> bool:
    """`python start.py web [--host H] [--port P]` 直接进网页面板。

    终端菜单是默认形态，只有显式写了 web 才切过去，不影响现有用法。
    """
    if not argv or argv[0].lower() not in ("web", "serve", "--web"):
        return False

    host = None
    port = None
    rest = argv[1:]
    i = 0
    while i < len(rest):
        flag = rest[i]
        value = rest[i + 1] if i + 1 < len(rest) else None
        if flag in ("--host", "-H") and value:
            host = value
            i += 2
            continue
        if flag in ("--port", "-p") and value:
            try:
                port = int(value)
            except ValueError:
                print(f"  [警告] 忽略无效端口: {value}")
            i += 2
            continue
        i += 1

    from web.server import run_web
    run_web(_project_dir(), host=host, port=port)
    return True


def main():
    """程序入口。"""
    if _dispatch_web(sys.argv[1:]):
        return

    from clear import clear_screen
    clear_screen()
    project_dir = _project_dir()

    # 版本更新检查 (静默执行，不阻塞启动流程)
    from check_update import check_for_update
    has_update, cur_ver, latest_ver = check_for_update()
    if has_update:
        print(f"  [更新] 新版本 {latest_ver} 已发布 (当前: {cur_ver})")
        print(f"        下载: {REPO_URL}/releases/tag/{latest_ver}")
        print()

    # 加载配置和语言
    config = load_config(project_dir)
    lang = config.get("language", "") or detect_os_language()
    load_language(lang)
    save_config(config, project_dir)

    servers_dir = ensure_servers_folder(project_dir)

    # 如果配置了自动启动的服务器，直接跳过主菜单
    selected = None
    auto_name = config.get("auto_start_server", "")
    if auto_name:
        for s in list_servers(servers_dir):
            if s.get("name") == auto_name:
                selected = s
                print(f"  [Auto] 自动启动服务器: {auto_name}")
                print()
                break
        if selected is None:
            print(f"  [警告] 配置的自动启动服务器 '{auto_name}' 不存在，进入主菜单")
            print()

    # 未配置自动启动或服务器不存在时，显示主菜单
    if selected is None:
        selected = show_main_menu(servers_dir, project_dir)
        if selected is None:
            sys.exit(0)

    # selected 是 list_servers 返回的条目（含 _path），
    # 需要重新加载完整配置
    server_cfg = load_server_config(selected["_path"])
    server_path = selected["_path"]

    exit_code = _start_server(server_cfg, server_path, config, project_dir)

    # 清理: 无论服务器如何退出，都要停掉 frpc。
    # 两套隧道各管一个进程：菜单里手动拉起的是原版 frp，_start_server 里按
    # openfrp 配置拉起的是 OpenFrp 那份。原来只停后者，于是「先在隧道菜单开
    # 原版 frp、再启动服务器」这条路径上，frpc 会一直留在后台占着远程端口。
    from frp.tunnel_manager import stop_frpc as stop_frp_tunnel
    from openfrp.tunnel_manager import stop_frpc as stop_openfrp_tunnel

    stop_openfrp_tunnel()
    stop_frp_tunnel()

    name = server_cfg.get("name", "?")
    print(t("app.done", name=name, code=exit_code))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
