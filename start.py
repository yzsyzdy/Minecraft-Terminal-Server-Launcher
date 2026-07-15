"""
MSTL — Minecraft Terminal Server Launcher

Copyright (C) 2026  yzsyzdy

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import os
import sys

from config import load_config, save_config
from server_manager import ensure_servers_folder, load_server_config
from menu import show_main_menu
from java_tools import resolve_java
from server_launcher import start_minecraft_server, start_server_interactive
from i18n import t, load_language, detect_os_language


def _is_eula_unagreed(server_path: str) -> bool:
    eula_path = os.path.join(server_path, "eula.txt")
    if not os.path.isfile(eula_path):
        return False
    try:
        with open(eula_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip() == "eula=false":
                    return True
        return False
    except OSError:
        return False


def _agree_to_eula(server_path: str) -> bool:
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
    print()
    print(t("eula.notice"))
    print()
    print(t("eula.prompt", name=name))
    print()
    ans = input(t("eula.ask")).strip().lower()
    return ans == "y"



def _project_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _start_server(server_cfg: dict, config: dict, project_dir: str) -> int:
    server_path = server_cfg["_path"]
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

    # OpenFrp auto-start
    of_cfg = server_cfg.get("openfrp", {}) or {}
    frpc_proc = None
    if of_cfg.get("auto_start") and of_cfg.get("token") and of_cfg.get("proxy_id"):
        from openfrp.tunnel_manager import launch_frpc
        frpc_proc = launch_frpc(project_dir, server_cfg)
    elif of_cfg.get("auto_start"):
        print("  [OpenFrp] auto_start enabled but token or proxy_id missing, skipping.")

    print()
    print(t("server.info_name", name=name))
    print(t("server.info_mc", ver=server_cfg.get("mc_version", "?")))
    print(t("server.info_java", path=java_abs))
    print(t("server.info_jar", path=jar_abs))
    print(t("server.info_memory", min=min_mem, max=max_mem))
    print()

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
            print(t("app.error_file_not_found", msg=str(e)))
            return 1
        except KeyboardInterrupt:
            print(t("app.cancel"))
            return 0

        if not _is_eula_unagreed(server_path):
            break

        print()
        print(t("eula.exit_reason"))
        if _prompt_eula(name):
            if _agree_to_eula(server_path):
                print(t("eula.agreed"))
                print()
                continue
            else:
                print(t("eula.failed"))
        else:
            print(t("eula.refused"))
        break

    return exit_code


def main():
    from clear import clear_screen
    clear_screen()
    project_dir = _project_dir()

    config = load_config(project_dir)
    lang = config.get("language", "") or detect_os_language()
    load_language(lang)
    save_config(config, project_dir)

    servers_dir = ensure_servers_folder(project_dir)

    selected = show_main_menu(servers_dir, project_dir)
    if selected is None:
        sys.exit(0)

    server_cfg = load_server_config(selected["_path"])

    exit_code = _start_server(server_cfg, config, project_dir)
    from openfrp.tunnel_manager import stop_frpc
    stop_frpc()

    name = server_cfg.get("name", "?")
    print(t("app.done", name=name, code=exit_code))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
