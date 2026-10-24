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
from core.errors import CoreError


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


def _ensure_utf8_stdout() -> None:
    """把标准输出与标准错误切成 UTF-8。

    Python 在真正的控制台上走 PEP 528（内部按 UTF-8 处理），但输出一旦被
    重定向到文件或管道，就会退回系统 ANSI 代码页——英文 Windows 上是
    cp1252，LANG=C 的 Linux 上是 ascii。那种环境里任何一句中文都会抛
    UnicodeEncodeError，程序死在第一条提示上，而这条路径恰好是
    `MSTL.exe > log.txt` 这种最常见的排障用法。

    errors="replace" 是兜底：真遇到编码不了的字符也只显示成问号，
    不该为了打印一行字把整个进程带走。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # 3.6 及更早没有 reconfigure
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


# ---------------------------------------------------------------------------
# 核心启动流程
# ---------------------------------------------------------------------------

def _maybe_start_frpc(server_cfg: dict, project_dir: str) -> None:
    """按实例配置里的 openfrp 段决定要不要先拉起 frpc 隧道。

    Minecraft 实例和通用实例共用这一段：隧道指向哪个本地端口是用户自己配的，
    和实例种类无关。判断逻辑本身已挪到 openfrp.tunnel_manager，好让网页面板
    与开机自动启动也走同一条，不再各写一份。
    """
    from openfrp.tunnel_manager import maybe_auto_start_for_instance

    maybe_auto_start_for_instance(project_dir, server_cfg)


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


def _print_usage() -> None:
    """把用法打出来。

    打包成 exe 之后用户是双击运行的，看不见任何命令；这里用 argv[0] 的
    文件名当程序名，好让提示在源码模式和 exe 模式下都说得通。
    """
    prog = os.path.basename(sys.argv[0] or "") or "start.py"
    print()
    print(t("usage.title", prog=prog))
    print()
    print(t("usage.menu"))
    print(t("usage.web", n=8))
    print(t("usage.help"))
    print()
    print(t("usage.hint", host="127.0.0.1", port=8765))
    print()


def _dispatch_args(argv: list[str]) -> bool:
    """处理命令行参数：web 子命令、--help、以及认不出来的参数。

    返回 True 表示参数已经消化完，调用方直接退出即可。
    """
    if not argv:
        return False

    first = argv[0].lower()
    if first in ("-h", "--help", "help", "/?"):
        _print_usage()
        return True

    if first in ("web", "serve", "--web"):
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
                    print("  " + t("web.menu.bad_port_arg", value=value))
                i += 2
                continue
            i += 1

        from web.server import run_web
        # 面板没起来（比如端口被占）就是失败，别回一个 0 让脚本以为成功了
        if not run_web(_project_dir(), host=host, port=port):
            sys.exit(1)
        return True

    # 认不出来就说清楚，别默默进菜单——原来写错参数会毫无提示地打开终端菜单，
    # 用户只会以为自己写对了
    print("  " + t("usage.unknown", arg=argv[0]))
    _print_usage()
    sys.exit(2)


def _auto_start_instances(servers_dir: str, project_dir: str, config: dict) -> None:
    """把标了「随软件启动」的实例在后台拉起来。

    走的是 RuntimeManager（PIPE + 读线程），不是前台交互模式：这样它们有
    控制台缓冲、有 logs/mstl-console.log，网页面板里能看能管，终端还留在
    菜单上。config.auto_start_server 指定的那一台仍走原来的前台流程，这里
    跳过它，免得同一台服被拉起两次。
    """
    from web.server import shared_manager

    foreground = config.get("auto_start_server", "")
    targets = [s for s in list_servers(servers_dir)
               if s.get("auto_start") and s.get("name") != foreground]
    if not targets:
        return

    manager = shared_manager(project_dir)
    started: list[str] = []
    for entry in targets:
        path = entry["_path"]
        name = entry.get("name") or os.path.basename(os.path.normpath(path))
        cfg = load_server_config(path)
        # 恢复上次在跑的实例可能已经先它一步拉起来了，别又启一遍
        current = manager.state_of(name)
        if current and current.get("state") != "stopped":
            continue
        try:
            manager.start(cfg, path)
            started.append(name)
        except CoreError as e:
            print("  " + t(e.key, **e.kwargs))
        except Exception as e:  # noqa: BLE001
            print(f"  [警告] 自动启动 {name} 失败: {e}")
            continue
        # 随实例开隧道：这条后台启动路径以前完全不碰它，
        # 于是「随软件启动」的实例配了隧道也不会开
        _maybe_start_frpc(cfg, project_dir)

    if started:
        print(t("app.auto_started", names=", ".join(started)))
        print(t("app.auto_started_hint"))
        print()
        # 进程退出时把这些后台实例一并停掉，别留下没人管的 java 进程
        import atexit
        atexit.register(lambda: manager.stop_all(timeout=10))


def _pause_before_exit() -> None:
    """等用户按一下再退出。

    双击 exe 启动时，进程一结束控制台窗口就没了；启动阶段要是直接报错退出，
    用户看不到任何原因。stdin 被重定向（脚本、CI）时 input() 会抛 EOFError，
    那种场合本来也不需要停，吞掉即可。
    """
    try:
        input(t("app.press_enter"))
    except (EOFError, OSError, KeyboardInterrupt):
        pass


def main():
    """程序入口。"""
    # 必须在任何一句 print 之前：否则重定向输出时中文会直接抛
    # UnicodeEncodeError（见 _ensure_utf8_stdout 的说明）
    _ensure_utf8_stdout()

    project_dir = _project_dir()

    # 语言要在处理参数之前就位，否则 --help 与参数报错会显示成一串 i18n key
    config = load_config(project_dir)
    load_language(config.get("language", "") or detect_os_language())

    # 下载线程数是存在 config.json 里的，每次启动都得重新应用一次：
    # 菜单里改它只影响当次进程，不在这里补上，重启后又回到 16，
    # 用户会觉得「我明明设置过了」。
    try:
        from download_msl import set_download_threads
        set_download_threads(int(config.get("download_threads") or 16))
    except (TypeError, ValueError):
        pass

    # 下载源偏好同理：菜单里改了只影响当次进程，下次启动得重新应用
    try:
        from download_msl import set_source_preference
        set_source_preference(bool(config.get("prefer_bmcl")))
    except Exception:  # noqa: BLE001
        pass

    if _dispatch_args(sys.argv[1:]):
        return

    from clear import clear_screen
    clear_screen()

    # 数据目录必须先能写：config.json、servers/、日志全落在那里。打包版要是
    # 被解压到 Program Files 这类当前用户没有写权限的地方，这里会抛
    # PermissionError——而双击启动时控制台一闪就关了，用户只会觉得
    # 「点了没任何反应」。所以：明确说清楚，并且停一下等用户看完。
    # 放在更新检查之前：目录都写不了就没必要先去联网转一圈。
    try:
        save_config(config, project_dir)
        servers_dir = ensure_servers_folder(project_dir)
    except OSError as e:
        print()
        print(t("app.data_dir_failed", path=project_dir, msg=str(e)))
        print()
        _pause_before_exit()
        return 1

    # 版本更新检查 (静默执行，不阻塞启动流程)
    from check_update import check_for_update
    has_update, cur_ver, latest_ver = check_for_update()
    if has_update:
        print(f"  [更新] 新版本 {latest_ver} 已发布 (当前: {cur_ver})")
        print(f"        下载: {REPO_URL}/releases/tag/{latest_ver}")
        print()

    # 面板随软件启动：守护线程里跑，不挡住后面的菜单与服务器
    from web.server import start_background, web_auto_start

    if web_auto_start(config):
        start_background(project_dir)
        print()

    # 实例自己的「随软件启动」：后台拉起，面板里能看能管
    _auto_start_instances(servers_dir, project_dir, config)

    # 上次退出时还在跑的实例也拉回来。两个入口（这里和 start_background）
    # 都可能调到，RuntimeManager 只管第一次。
    from web.server import restore_instances

    restored = restore_instances(project_dir)
    if restored:
        print(t("web.cli.restored", names="\u3001".join(restored)))
        print()

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
    # 把 main() 的返回值交给进程退出码：main() 里启动阶段的致命错误会
    # return 1，原来直接调用 main() 会把它丢掉，进程照样退出 0，
    # 脚本/计划任务那边看不出其实没起来。
    sys.exit(main())
