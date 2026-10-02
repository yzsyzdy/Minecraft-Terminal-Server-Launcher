"""
Interactive menus: main menu, server list, import/download/export guide, plugin/mod management.
"""

import sys
import os
from typing import Any, Optional

from clear import clear_screen
from i18n import t, load_language, available_languages, detect_os_language
from config import load_config, save_config
from constants import REPO_URL
from server_manager import (
    list_servers,
    load_server_config,
    save_server_config,
    classify_server_type,
    ensure_servers_folder,
    EXPORT_CATEGORIES,
)
from core.errors import CoreError
from core import instances as inst
from core.transfer import abort_import, commit_import, export_instance, plan_import


# ---------------------------------------------------------------------------
# Generic jar management utilities
# ---------------------------------------------------------------------------

class _ConsoleReporter:
    """把 core 抛出来的事件画到终端上。

    core 只产出 i18n 键和数字，翻译和排版都在这里做。
    """

    _BAR_WIDTH = 30

    def log(self, key: str, **kwargs) -> None:
        print("  " + t(key, **kwargs))

    def warn(self, key: str, **kwargs) -> None:
        print("  " + t(key, **kwargs))

    def progress(self, *, label: str = "", current: Optional[int] = None,
                 total: Optional[int] = None, ratio: Optional[float] = None,
                 done: bool = False) -> None:
        if ratio is None and current is not None and total:
            ratio = current / total
        if ratio is None:
            if label:
                print("  " + label)
            return
        filled = int(self._BAR_WIDTH * ratio)
        bar = "\u2588" * filled + "\u2591" * (self._BAR_WIDTH - filled)
        prefix = f"  {label} " if label else "  "
        print(f"{prefix}[{bar}] {ratio * 100:5.1f}%",
              end="\n" if done else "\r", flush=True)


_REPORTER = _ConsoleReporter()


def _report_error(err: CoreError) -> None:
    """把 core 的业务错误翻成一行提示。"""
    print("  " + t(err.key, **err.kwargs))


def _delete_jar(directory: str) -> None:
    """交互式删除一个 jar。"""
    if inst.list_jars(directory).total == 0:
        print(t("menu.plugin.nothing_delete"))
        input(t("app.press_enter"))
        return
    target = input(t("menu.plugin.enter_filename")).strip()
    if not target:
        return
    if input(t("menu.plugin.confirm_delete", name=target)).strip().lower() != "y":
        print(t("menu.plugin.cancelled"))
        input(t("app.press_enter"))
        return
    try:
        inst.delete_jar(directory, target)
        print(t("menu.plugin.deleted", name=target))
    except CoreError as e:
        _report_error(e)
    input(t("app.press_enter"))


def _toggle_jar(directory: str) -> None:
    """交互式启用 / 禁用一个 jar（改后缀实现）。"""
    if inst.list_jars(directory).total == 0:
        print(t("menu.plugin.nothing_toggle"))
        input(t("app.press_enter"))
        return
    target = input(t("menu.plugin.enter_toggle_file")).strip()
    if not target:
        return
    turning_on = target.endswith(inst.DISABLED_SUFFIX)
    try:
        new_name = inst.set_jar_enabled(directory, target, enabled=turning_on)
        if turning_on:
            print(t("menu.plugin.enabled", name=new_name))
        else:
            print(t("menu.plugin.disabled", name=new_name))
    except CoreError as e:
        _report_error(e)
    input(t("app.press_enter"))


def _add_jar_from_file(directory: str) -> None:
    """交互式导入本地 jar。"""
    print()
    print(t("menu.plugin.add_prompt"))
    jar_path = input("  > ").strip().strip('"')
    if not jar_path:
        return
    try:
        name = inst.import_jar(directory, jar_path)
        print(t("menu.plugin.add_imported", name=name))
    except CoreError as e:
        _report_error(e)
    input(t("app.press_enter"))


# ---------------------------------------------------------------------------
# Plugin management
# ---------------------------------------------------------------------------

def _get_plugins_dir(server_dir: str) -> str:
    return inst.plugins_dir(server_dir)


def _search_and_download_plugin(server_name: str, plugins_dir: str) -> None:
    """Search and download plugins from SpigotMC, Modrinth, Hangar, sorted by downloads."""
    clear_screen()
    from plugin_sources import search_all, download_plugin

    print()
    query = input(t("search.query_plugin")).strip()
    if not query:
        return

    print(t("search.searching_plugins"))
    src_results = search_all(query, limit=5)

    flat: list[tuple[int, str, Any]] = []
    for label, items in src_results.items():
        for item in items:
            dl = item.get("downloads", 0) or 0
            flat.append((dl, label, item))
    flat.sort(key=lambda x: x[0], reverse=True)

    if not flat:
        print(t("search.no_results", type="plugin"))
        input(t("app.press_enter"))
        return

    print()
    print(t("search.results_found", count=len(flat)))
    print()
    for i, (dl, src, r) in enumerate(flat, 1):
        nm = r.get("name", "?")
        author = r.get("author", "?")
        desc = (r.get("description") or "")[:56]
        dl_str = f"{dl:,}" if dl >= 1000 else str(dl)
        print(t("search.result_line", i=i, src=src, name=nm))
        print(t("search.result_detail", author=author, dl=dl_str))
        if desc:
            print(t("search.result_desc", desc=desc))
        print()

    while True:
        try:
            c = input(t("search.download_prompt", max=len(flat))).strip()
            if c == "0":
                return
            idx = int(c) - 1
            if 0 <= idx < len(flat):
                _, src_label, result = flat[idx]
                print(t("search.downloading", src=src_label, name=result["name"]))
                filename = download_plugin(result, plugins_dir)
                if filename:
                    print(t("search.download_saved", name=filename))
                else:
                    print(t("search.download_failed"))
                input(t("app.press_enter"))
                return
        except ValueError:
            pass
        print(t("app.invalid_choice_short"))


# ---------------------------------------------------------------------------
# Generic jar management menu
# ---------------------------------------------------------------------------

def _manage_jars_menu(
    name: str,
    directory: str,
    label: str,
    search_fn: Optional[callable],
) -> None:
    """Generic plugin/mod management menu."""
    os.makedirs(directory, exist_ok=True)

    while True:
        jars = inst.list_jars(directory)
        clear_screen()
        print()
        print(f"  [{label}] {name}")
        print(t("menu.plugin.dir", dir=directory))
        print()
        if jars.total == 0:
            print(t("menu.plugin.empty"))
        else:
            print(t("menu.plugin.count", count=jars.total))
            print()
            for p in jars.enabled:
                print(f"      [E] {p}")
            for p in jars.disabled:
                print(f"      [D] {p}")
            print()
        print("  [E] = Enabled  [D] = Disabled")
        print()
        print(t("menu.plugin.delete", n=1))
        print(t("menu.plugin.toggle", n=2))
        print(t("menu.plugin.add", n=3))
        print(t("menu.plugin.back", n=0))
        print()
        choice = input(t("menu.plugin.prompt")).strip()

        if choice == "0":
            return
        elif choice == "1":
            _delete_jar(directory)
        elif choice == "2":
            _toggle_jar(directory)
        elif choice == "3":
            while True:
                print()
                print(f"  [Add {label}] {name}")
                print()
                print(t("menu.plugin.add_import", n=1))
                if search_fn:
                    print(t("menu.plugin.add_search", n=2))
                print(t("menu.plugin.back", n=0))
                print()
                sub = input("  " + t("menu.main.prompt", max=2)).strip()
                if sub == "0":
                    break
                elif sub == "1":
                    _add_jar_from_file(directory)
                elif sub == "2" and search_fn:
                    search_fn()
                else:
                    print(t("app.invalid_choice_short"))
        else:
            print(t("app.invalid_choice_short"))


def _show_plugin_menu(server_cfg: dict, server_dir: str) -> None:
    """Plugin management menu."""
    name = server_cfg.get("name", "?")
    plugins_dir = _get_plugins_dir(server_dir)
    _manage_jars_menu(
        name=name,
        directory=plugins_dir,
        label=t("menu.plugin.header"),
        search_fn=lambda: _search_and_download_plugin(name, plugins_dir),
    )


# ---------------------------------------------------------------------------
# Mod management
# ---------------------------------------------------------------------------

def _get_mods_dir(server_dir: str) -> str:
    return inst.mods_dir(server_dir)


def _search_and_download_mod(name: str, mods_dir: str,
                              mc_version: str, loader: str) -> None:
    """Search and download mods from Modrinth and CurseForge, filtered by version/loader."""
    clear_screen()
    from mod_sources import search_all_mods, download_mod, set_curseforge_api_key

    print()
    query = input(t("search.query_mod")).strip()
    if not query:
        return

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
    print(t("search.searching_mods", info=version_info))
    src_results = search_all_mods(query, mc_version, loader, limit=10)

    is_fallback = src_results.pop("_fallback", False)

    src_used = list(src_results.keys())
    if not src_used:
        print(t("search.no_results", type="mod"))
        if not cf_key:
            print(t("search.curseforge_hint"))
        input(t("app.press_enter"))
        return

    if not cf_key:
        print(t("search.modrinth_only"))
    if is_fallback:
        print(t("search.fallback_hint"))

    flat: list[tuple[int, str, dict]] = []
    for label, items in src_results.items():
        for item in items:
            dl = item.get("downloads", 0) or 0
            flat.append((dl, label, item))
    flat.sort(key=lambda x: x[0], reverse=True)

    print()
    print(t("search.results_found", count=len(flat)))
    print()
    for i, (dl, src, r) in enumerate(flat, 1):
        nm = r.get("name", "?")
        author = r.get("author", "?")
        desc = (r.get("description") or "")[:60]
        dl_str = f"{dl:,}" if dl >= 1000 else str(dl)
        print(t("search.result_line", i=i, src=src, name=nm))
        print(t("search.result_detail", author=author, dl=dl_str))
        if desc:
            print(t("search.result_desc", desc=desc))
        print()

    while True:
        try:
            c = input(t("search.download_prompt", max=len(flat))).strip()
            if c == "0":
                return
            idx = int(c) - 1
            if 0 <= idx < len(flat):
                _, src_label, result = flat[idx]
                print(t("search.downloading", src=src_label, name=result["name"]))
                filename = download_mod(result, mods_dir, mc_version, loader)
                if filename:
                    print(t("search.download_saved", name=filename))
                else:
                    print(t("search.download_failed"))
                input(t("app.press_enter"))
                return
        except ValueError:
            pass
        print(t("app.invalid_choice_short"))


def _show_mod_menu(server_cfg: dict, server_dir: str) -> None:
    """Mod management menu."""
    name = server_cfg.get("name", "?")
    mc_version = server_cfg.get("mc_version", "")
    from mod_sources import extract_mod_loader
    loader = extract_mod_loader(name)

    mods_dir = _get_mods_dir(server_dir)
    _manage_jars_menu(
        name=name,
        directory=mods_dir,
        label=t("menu.mod.header"),
        search_fn=lambda: _search_and_download_mod(name, mods_dir, mc_version, loader),
    )


# ---------------------------------------------------------------------------
# Server type dispatch
# ---------------------------------------------------------------------------

def _edit_server_config(server_cfg: dict, server_dir: str) -> None:
    """Edit server advanced settings: JVM args, priority, etc."""
    while True:
        clear_screen()
        print()
        print(t("menu.config.title", name=server_cfg.get("name", "?")))
        print()
        prio = server_cfg.get("process_priority", "normal")
        print(t("menu.config.1", n=1, val=prio))
        print(t("menu.config.2", n=2, val=" ".join(server_cfg.get("extra_jvm_args", []))))
        print(t("menu.config.3", n=3, val=" ".join(server_cfg.get("extra_server_args", []))))
        print(t("menu.plugin.back", n=0))
        print()
        c = input("  " + t("menu.main.prompt", max=3)).strip()
        if c == "0":
            return
        elif c == "1":
            print()
            print(t("menu.config.prio_prompt"))
            print(t("menu.config.prio_normal", n=1))
            print(t("menu.config.prio_below", n=2))
            print(t("menu.config.prio_low", n=3))
            p = input("  " + t("menu.main.prompt", max=3)).strip()
            mapping = {"1": "normal", "2": "below_normal", "3": "low"}
            if p in mapping:
                server_cfg["process_priority"] = mapping[p]
                save_server_config(server_cfg, server_dir)
                print(t("menu.config.saved"))
            else:
                print(t("menu.config.prio_invalid"))
        elif c == "2":
            print()
            cur = " ".join(server_cfg.get("extra_jvm_args", []))
            raw = input(t("menu.config.extra_prompt", cur=cur)).strip()
            server_cfg["extra_jvm_args"] = raw.split() if raw else []
            save_server_config(server_cfg, server_dir)
            print(t("menu.config.saved"))
        elif c == "3":
            print()
            cur = " ".join(server_cfg.get("extra_server_args", []))
            raw = input(t("menu.config.extra_prompt", cur=cur)).strip()
            server_cfg["extra_server_args"] = raw.split() if raw else []
            save_server_config(server_cfg, server_dir)
            print(t("menu.config.saved"))
        else:
            print(t("app.invalid_choice_short"))


def _run_management_for_server(server_cfg: dict, server_dir: str) -> None:
    """Check server type and jump to the appropriate management menu."""
    stype = classify_server_type(server_cfg, server_dir)

    if stype == "plugin":
        _show_plugin_menu(server_cfg, server_dir)
    elif stype == "mod":
        _show_mod_menu(server_cfg, server_dir)
    elif stype == "hybrid":
        print()
        print(t("menu.hybrid.title", name=server_cfg.get("name", "?")))
        print()
        print(t("menu.hybrid.manage_plugin", n=1))
        print(t("menu.hybrid.manage_mod", n=2))
        print(t("menu.plugin.back", n=0))
        print()
        while True:
            c = input("  " + t("menu.main.prompt", max=2)).strip()
            if c == "1":
                _show_plugin_menu(server_cfg, server_dir); return
            elif c == "2":
                _show_mod_menu(server_cfg, server_dir); return
            elif c == "0":
                return
            print(t("app.invalid_choice_short"))
    elif stype in ("vanilla", "proxy", "bedrock"):
        msg_key = {"vanilla": "menu.hybrid.unsupported_vanilla",
                    "proxy": "menu.hybrid.unsupported_proxy",
                    "bedrock": "menu.hybrid.unsupported_bedrock"}
        print()
        print(t(msg_key[stype], name=server_cfg.get("name", "?")))
        print()
        input(t("app.press_enter"))
    else:
        print()
        print(t("menu.hybrid.unsupported_unknown", name=server_cfg.get("name", "?")))
        print()
        input(t("app.press_enter"))


def _pick_server_for_management(servers: list[dict], servers_dir: str) -> None:
    """Let user pick a server to manage plugins/mods."""
    while True:
        clear_screen()
        print()
        print(t("menu.manage.title"))
        print()
        for i, s in enumerate(servers, 1):
            ver = s.get("mc_version", "?")
            jar = s.get("jar", "?")
            print(t("menu.manage.line", i=i, name=s["name"], ver=ver, jar=jar))
        print(t("menu.plugin.back", n=0))
        print()
        try:
            c = input(t("menu.manage.prompt", max=len(servers))).strip()
            if c == "0":
                return
            idx = int(c) - 1
            if 0 <= idx < len(servers):
                server_dir = servers[idx]["_path"]
                server_cfg = load_server_config(server_dir)
                print()
                print(t("menu.manage.sub_prompt"))
                print(t("menu.manage.sub_manage", n=1))
                print(t("menu.manage.sub_config", n=2))
                print(t("menu.plugin.back", n=0))
                print()
                sub_c = input("  " + t("menu.main.prompt", max=2)).strip()
                if sub_c == "1":
                    _run_management_for_server(server_cfg, server_dir)
                elif sub_c == "2":
                    _edit_server_config(server_cfg, server_dir)
                else:
                    print(t("app.invalid_choice_short"))
        except ValueError:
            print(t("app.invalid_choice_short"))


# ---------------------------------------------------------------------------
# Export server
# ---------------------------------------------------------------------------

def _export_server(servers: list[dict], servers_dir: str, project_dir: str) -> None:
    """Interactive server export to zip."""
    while True:
        clear_screen()
        print()
        print(t("menu.export.select"))
        print()
        for i, s in enumerate(servers, 1):
            print(f"  [{i}] {s['name']}")
        print(t("menu.export.back", n=0))
        print()
        try:
            c = input(t("menu.export.prompt_server", max=len(servers))).strip()
            if c == "0":
                return
            idx = int(c) - 1
            if 0 <= idx < len(servers):
                server_cfg = load_server_config(servers[idx]["_path"])
                server_dir = servers[idx]["_path"]
                name = server_cfg.get("name", "server")
                break
        except ValueError:
            pass
        print(t("app.invalid_choice_short"))

    selected: set[str] = {key for key, _, _, default in EXPORT_CATEGORIES if default}

    while True:
        clear_screen()
        print()
        print(t("menu.export.title", name=name))
        print(t("menu.export.categories"))
        print()
        for key, lbl_key, _, _ in EXPORT_CATEGORIES:
            mark = "[Y]" if key in selected else "[N]"
            print(f"    {mark} {t(lbl_key)}")
        print()
        print(t("menu.export.hint"))
        print()
        for i, (key, lbl_key, _, _) in enumerate(EXPORT_CATEGORIES, 1):
            mark = "Y" if key in selected else "N"
            print(f"    [{i}] {mark} {t(lbl_key)}")
        print(t("menu.export.all", n="A"))
        print(t("menu.export.none", n="N"))
        print(t("menu.export.cancel_btn", n=0))
        print()

        choice = input(t("menu.export.prompt")).strip().lower()
        if choice == "":
            break
        if choice == "0":
            return
        if choice == "a":
            selected = {key for key, _, _, _ in EXPORT_CATEGORIES}
            continue
        if choice == "n":
            selected.clear()
            continue
        try:
            num = int(choice)
            if 1 <= num <= len(EXPORT_CATEGORIES):
                key = EXPORT_CATEGORIES[num - 1][0]
                if key in selected:
                    selected.discard(key)
                else:
                    selected.add(key)
        except ValueError:
            pass

    if not selected:
        print(t("menu.export.nothing_selected"))
        input(t("app.press_enter"))
        return

    print()
    default_name = f"{name}-export.zip"
    print(t("menu.export.default_name", name=default_name))
    out = input(t("menu.export.output_prompt")).strip().strip('"')
    if not out:
        out = os.path.join(project_dir, default_name)
    else:
        out = os.path.abspath(out)
        if os.path.isdir(out):
            out = os.path.join(out, default_name)

    if os.path.isfile(out):
        confirm = input(t("menu.export.overwrite_confirm")).strip().lower()
        if confirm != "y":
            print(t("menu.plugin.cancelled"))
            input(t("app.press_enter"))
            return

    print(t("menu.export.starting", count=len(selected)))
    try:
        result = export_instance(server_dir, out, selected, reporter=_REPORTER)
        print(t("export.success", path=result.path,
                size=result.size_bytes / 1024 / 1024, files=result.file_count))
    except CoreError as e:
        _report_error(e)
    input(t("app.press_enter"))


# ---------------------------------------------------------------------------
# OpenFrp 内网穿透
# ---------------------------------------------------------------------------

from openfrp.tunnel_manager import (
    get_tunnels_by_token, get_node_list, create_tunnel, delete_tunnel,
    download_frpc, get_frpc_local_path, TunnelInfo,
)

from frp.tunnel_manager import (
    load_frp_config, save_frp_config, launch_frpc, stop_frpc,
    is_frpc_running, download_frpc as frp_download_frpc,
    get_frpc_bin_path, get_frpc_toml_path, get_latest_frp_version,
)


def _tunnel_menu(servers: list[dict], servers_dir: str, project_dir: str) -> None:
    """内网穿透总菜单: 选择 OpenFrp 或原版 frp。"""
    while True:
        clear_screen()
        print()
        print(t("tunnel.menu_title"))
        print()
        print(t("tunnel.openfrp", n=1))
        print(t("tunnel.frp", n=2))
        print(t("tunnel.back", n=0))
        print()
        choice = input(t("tunnel.prompt")).strip()

        if choice == "0":
            return
        elif choice == "1":
            _openfrp_menu(servers, servers_dir, project_dir)
        elif choice == "2":
            _frp_menu(project_dir)
        else:
            print(t("app.invalid_choice_short"))
            input(t("app.press_enter"))


def _openfrp_menu(servers: list[dict], servers_dir: str, project_dir: str) -> None:
    """OpenFrp tunnel management menu."""
    while True:
        clear_screen()
        print()
        print(t("openfrp.menu_title"))
        print(t("openfrp.community_notice"))
        print()
        print(t("openfrp.menu_list_tunnels", n=1))
        print(t("openfrp.menu_download_frpc", n=2))
        print(t("openfrp.menu_create_tunnel", n=3))
        print(t("openfrp.menu_bind_tunnel", n=4))
        print(t("openfrp.menu_back", n=0))
        print()
        choice = input(t("openfrp.menu_prompt")).strip()

        if choice == "0":
            return
        elif choice == "1":
            _openfrp_list_tunnels(project_dir)
        elif choice == "2":
            _openfrp_download_frpc(project_dir)
        elif choice == "3":
            _openfrp_create_tunnel(project_dir)
        elif choice == "4":
            _openfrp_bind_tunnel(servers, servers_dir, project_dir)
        else:
            print(t("app.invalid_choice_short"))


def _frp_menu(project_dir: str) -> None:
    """原版 frp 隧道管理菜单。"""
    while True:
        clear_screen()
        print()
        print(t("frp.menu_title"))
        print()
        if is_frpc_running():
            print(t("frp.status_running"))
        else:
            print(t("frp.status_stopped"))
        print()
        print(t("frp.menu_download", n=1))
        print(t("frp.menu_config", n=2))
        print(t("frp.menu_proxies", n=3))
        print(t("frp.menu_add_proxy", n=4))
        print(t("frp.menu_delete_proxy", n=5))
        print(t("frp.menu_start", n=6))
        print(t("frp.menu_stop", n=7))
        print(t("frp.menu_back", n=0))
        print()
        choice = input(t("frp.menu_prompt")).strip()

        if choice == "0":
            return
        elif choice == "1":
            _frp_download(project_dir)
        elif choice == "2":
            _frp_edit_config(project_dir)
        elif choice == "3":
            _frp_list_proxies(project_dir)
        elif choice == "4":
            _frp_add_proxy(project_dir)
        elif choice == "5":
            _frp_delete_proxy(project_dir)
        elif choice == "6":
            _frp_start(project_dir)
        elif choice == "7":
            stop_frpc()
            input(t("app.press_enter"))
        else:
            print(t("app.invalid_choice_short"))
            input(t("app.press_enter"))


def _frp_download(project_dir: str) -> None:
    """下载或更新原版 frpc。"""
    print()
    bin_path = get_frpc_bin_path(project_dir)
    if os.path.isfile(bin_path):
        size = os.path.getsize(bin_path) / 1024 / 1024
        print(t("frp.frpc_exists", path=bin_path, size=size))
        if input(t("openfrp.frpc_redownload")).strip().lower() != "y":
            return

    latest = get_latest_frp_version()
    if latest:
        print(t("frp.latest_version", ver=latest))
    path = frp_download_frpc(project_dir)
    if path:
        print(t("frp.frpc_downloaded", path=path))
    else:
        print(t("frp.frpc_download_failed"))
    input(t("app.press_enter"))


def _frp_edit_config(project_dir: str) -> None:
    """编辑 frpc.toml 服务器连接配置。"""
    cfg = load_frp_config(project_dir)
    print()
    print(t("frp.config_title"))
    print()
    addr = input(t("frp.config_server_addr", cur=cfg.get("serverAddr", ""))).strip()
    port = input(t("frp.config_server_port", cur=cfg.get("serverPort", 7000))).strip()
    token = input(t("frp.config_token", cur=cfg.get("auth", {}).get("token", ""))).strip()

    if addr:
        cfg["serverAddr"] = addr
    if port:
        try:
            cfg["serverPort"] = int(port)
        except ValueError:
            print(t("frp.config_port_invalid"))
    if token:
        cfg.setdefault("auth", {})["token"] = token

    if save_frp_config(project_dir, cfg):
        print(t("frp.config_saved", path=get_frpc_toml_path(project_dir)))
    else:
        print(t("frp.config_save_failed"))
    input(t("app.press_enter"))


def _frp_list_proxies(project_dir: str) -> None:
    """列出当前所有隧道。"""
    cfg = load_frp_config(project_dir)
    proxies = cfg.get("proxies", [])
    print()
    if not proxies:
        print(t("frp.no_proxies"))
    else:
        for i, p in enumerate(proxies, 1):
            name = p.get("name", "?")
            ptype = p.get("type", "?")
            local = f"{p.get('localIP', '127.0.0.1')}:{p.get('localPort', '?')}"
            remote = p.get("remotePort", "?")
            print(f"  [{i}] {name} ({ptype}) {local} -> :{remote}")
    print()
    print(t("frp.proxy_count", count=len(proxies)))
    input(t("app.press_enter"))


def _frp_add_proxy(project_dir: str) -> None:
    """添加新隧道。"""
    cfg = load_frp_config(project_dir)
    proxies = cfg.setdefault("proxies", [])

    print()
    print(t("frp.add_title"))
    name = input(t("frp.proxy_name")).strip()
    if not name:
        return
    ptype = input(t("frp.proxy_type", defval="tcp")).strip() or "tcp"
    local_ip = input(t("frp.proxy_local_ip", defval="127.0.0.1")).strip() or "127.0.0.1"
    local_port = input(t("frp.proxy_local_port")).strip()
    remote_port = input(t("frp.proxy_remote_port")).strip()
    if not local_port or not remote_port:
        print(t("frp.add_invalid"))
        input(t("app.press_enter"))
        return

    proxy = {
        "name": name,
        "type": ptype,
        "localIP": local_ip,
        "localPort": int(local_port),
    }
    if ptype == "tcp" and remote_port:
        proxy["remotePort"] = int(remote_port)
    elif ptype == "udp" and remote_port:
        proxy["remotePort"] = int(remote_port)
    elif ptype == "http":
        # frp 里 customDomains 是字符串数组，写成裸字符串 frpc 会解析失败
        proxy["customDomains"] = [name]

    proxies.append(proxy)
    if save_frp_config(project_dir, cfg):
        print(t("frp.add_saved", name=name))
    else:
        print(t("frp.add_save_failed"))
    input(t("app.press_enter"))


def _frp_delete_proxy(project_dir: str) -> None:
    """删除指定隧道。"""
    cfg = load_frp_config(project_dir)
    proxies = cfg.get("proxies", [])
    if not proxies:
        print()
        print(t("frp.no_proxies"))
        input(t("app.press_enter"))
        return

    print()
    for i, p in enumerate(proxies, 1):
        print(f"  [{i}] {p.get('name', '?')} ({p.get('type', '?')})")
    print(t("frp.delete_back", n=0))
    print()
    c = input(t("frp.delete_prompt")).strip()
    if c == "0":
        return
    try:
        idx = int(c) - 1
        if 0 <= idx < len(proxies):
            removed = proxies.pop(idx)
            cfg["proxies"] = proxies
            if save_frp_config(project_dir, cfg):
                print(t("frp.delete_saved", name=removed.get("name", "?")))
            else:
                print(t("frp.delete_save_failed"))
        else:
            print(t("app.invalid_choice_short"))
    except ValueError:
        print(t("app.invalid_choice_short"))
    input(t("app.press_enter"))


def _frp_start(project_dir: str) -> None:
    """启动原版 frpc。"""
    print()
    proc = launch_frpc(project_dir)
    if proc is None:
        print(t("frp.start_failed"))
        input(t("app.press_enter"))


def _openfrp_list_tunnels(project_dir: str) -> None:
    """List tunnels by user token."""
    print()
    token = input(t("openfrp.token_prompt")).strip()
    if not token:
        return
    print(t("openfrp.fetching_tunnels"))
    groups = get_tunnels_by_token(token)
    if groups is None:
        print(t("openfrp.fetch_tunnels_failed"))
        input(t("app.press_enter"))
        return
    print()
    for node_name, tunnels in groups:
        print(f"  [{node_name}]")
        for t_in in tunnels:
            online = "\u2713" if t_in.online else "\u2717"
            print(f"      [{online}] \u00a7{t_in.proxy_id} {t_in.name}  ({t_in.proxy_type}) {t_in.remote_addr}")
        print()
    total = sum(len(g) for _, g in groups)
    print(t("openfrp.tunnel_count", count=total))
    input(t("app.press_enter"))


def _openfrp_download_frpc(project_dir: str) -> None:
    """Download or update frpc binary."""
    existing = get_frpc_local_path(project_dir)
    if os.path.isfile(existing):
        size = os.path.getsize(existing) / 1024 / 1024
        print(t("openfrp.frpc_exists", path=existing, size=size))
        if input(t("openfrp.frpc_redownload")).strip().lower() != "y":
            return
    path = download_frpc(project_dir)
    if path:
        print(t("openfrp.frpc_downloaded", path=path))
    else:
        print(t("openfrp.frpc_download_failed"))
    input(t("app.press_enter"))


def _openfrp_create_tunnel(project_dir: str) -> None:
    """Interactive tunnel creation wizard for Minecraft servers."""
    from openfrp.tunnel_manager import get_node_list, create_tunnel

    print()
    auth = input(t("openfrp.auth_prompt")).strip()
    if not auth:
        return

    print(t("openfrp.fetching_nodes"))
    nodes = get_node_list(auth)
    if not nodes:
        print(t("openfrp.fetch_nodes_failed"))
        input(t("app.press_enter"))
        return

    # 过滤可用的 TCP 节点
    tcp_nodes = [n for n in nodes if n.protocol_support.get("tcp") and not n.fully_loaded and n.status == 200]
    if not tcp_nodes:
        print(t("openfrp.no_tcp_nodes"))
        input(t("app.press_enter"))
        return

    print()
    print(t("openfrp.select_node"))
    print()
    for i, n in enumerate(tcp_nodes[:20], 1):
        region = {1: "\u4e2d\u56fd\u5927\u9646", 2: "\u6e2f\u6fb3\u53f0", 3: "\u6d77\u5916"}.get(n.classify, "?")
        tag = "" if not n.need_realname else t("openfrp.realname_tag")
        print(f"  [{i}] {n.name} ({region}){tag}")
    print(t("openfrp.select_back", n=0))
    print()
    try:
        c = input(t("openfrp.select_node_prompt", max=min(len(tcp_nodes), 20))).strip()
        if c == "0":
            return
        idx = int(c) - 1
        if 0 <= idx < len(tcp_nodes):
            selected_node = tcp_nodes[idx]
        else:
            return
    except ValueError:
        return

    print()
    name = input(t("openfrp.tunnel_name")).strip() or f"mc-{selected_node.node_id}"
    local_port = input(t("openfrp.local_port")).strip() or "25565"
    remote_port = input(t("openfrp.remote_port")).strip()
    encrypt = input(t("openfrp.enable_encrypt")).strip().lower()
    compress = input(t("openfrp.enable_compress")).strip().lower()

    params = {
        "type": "tcp",
        "name": name,
        "local_addr": "127.0.0.1",
        "local_port": local_port,
        "node_id": selected_node.node_id,
        "dataEncrypt": encrypt != "n",
        "dataGzip": compress != "n",
        "domain_bind": "",
        "autoTls": "false",
        "forceHttps": False,
        "proxyProtocolVersion": False,
        "custom": "",
    }
    if remote_port:
        try:
            params["remote_port"] = int(remote_port)
        except ValueError:
            pass

    print()
    print(t("openfrp.creating_tunnel", name=name))
    msg = create_tunnel(auth, params)
    if msg is None:
        print(t("openfrp.create_failed"))
    else:
        print(t("openfrp.tunnel_created", msg=msg))
    input(t("app.press_enter"))


def _openfrp_bind_tunnel(servers: list[dict], servers_dir: str, project_dir: str) -> None:
    """Bind a tunnel to a server config."""
    while True:
        clear_screen()
        print()
        print(t("openfrp.bind_select_server"))
        print()
        for i, s in enumerate(servers, 1):
            of = s.get("openfrp", {}) or {}
            pid = of.get("proxy_id", 0)
            tag = t("openfrp.tunnel_tag", pid=pid) if pid else ""
            print(f"  [{i}] {s['name']}{tag}")
        print(t("openfrp.bind_back", n=0))
        print()
        try:
            c = input(t("openfrp.bind_select_prompt", max=len(servers))).strip()
            if c == "0":
                return
            idx = int(c) - 1
            if 0 <= idx < len(servers):
                server_path = servers[idx]["_path"]
                server_cfg = load_server_config(server_path)
                break
        except ValueError:
            pass
        print(t("app.invalid_choice_short"))

    print()
    token = input(t("openfrp.bind_token")).strip()
    pid_str = input(t("openfrp.bind_proxy_id")).strip()
    auto = input(t("openfrp.bind_auto_start")).strip().lower()

    of_cfg = server_cfg.get("openfrp", {}) or {}
    if token:
        of_cfg["token"] = token
    if pid_str:
        try:
            of_cfg["proxy_id"] = int(pid_str)
        except ValueError:
            print(t("openfrp.bind_pid_invalid"))
            input(t("app.press_enter"))
            return
    of_cfg["auto_start"] = (auto == "y")
    server_cfg["openfrp"] = of_cfg
    save_server_config(server_cfg, server_path)
    print(t("openfrp.bind_complete", proxy_id=of_cfg.get('proxy_id', 0), auto_start=of_cfg['auto_start']))
    input(t("app.press_enter"))


# ---------------------------------------------------------------------------
# Settings menu
# ---------------------------------------------------------------------------

def _settings_menu(project_dir: str) -> None:
    """Settings: language, download threads, update, auto-start."""
    from download_msl import set_download_threads

    while True:
        config = load_config(project_dir)
        current_lang = config.get("language", "") or "auto"
        current_threads = config.get("download_threads", 16)
        auto_server = config.get("auto_start_server", "") or "-"
        from startup import is_startup_enabled
        startup_state = t("settings.on") if is_startup_enabled() else t("settings.off")

        clear_screen()
        print()
        print(t("settings.menu_title"))
        print()
        print(t("settings.lang_option", n=1, lang=current_lang))
        print(t("settings.threads_option", n=2, count=current_threads))
        print(t("settings.update_option", n=3))
        print(t("settings.auto_start_option", n=4, name=auto_server))
        print(t("settings.startup_option", n=5, state=startup_state))
        print(t("settings.back", n=0))
        print()
        choice = input(t("settings.prompt")).strip()

        if choice == "0":
            return

        elif choice == "1":
            # 切换语言
            langs = available_languages()
            clear_screen()
            print()
            print(t("settings.lang_title"))
            print()
            for i, (code, display) in enumerate(langs, 1):
                print(f"  [{i}] {display} ({code})")
            print(f"  [A] {t('settings.lang_auto')}")
            print(t("settings.back", n=0))
            print()
            c = input(t("settings.lang_prompt", max=len(langs))).strip().lower()
            if c == "0":
                continue
            if c == "a":
                config["language"] = ""
            else:
                try:
                    idx = int(c) - 1
                    if 0 <= idx < len(langs):
                        config["language"] = langs[idx][0]
                except ValueError:
                    print(t("app.invalid_choice_short"))
                    continue
            save_config(config, project_dir)
            load_language(config["language"] or detect_os_language())
            print(t("settings.lang_saved"))
            input(t("app.press_enter"))

        elif choice == "2":
            # 设置下载线程数
            print()
            print(t("settings.threads_prompt", current=current_threads))
            c = input("  > ").strip()
            if c:
                try:
                    n = int(c)
                    if n < 1:
                        n = 1
                    config["download_threads"] = n
                    save_config(config, project_dir)
                    set_download_threads(n)
                    print(t("settings.threads_saved", count=n))
                except ValueError:
                    print(t("settings.threads_invalid"))
            input(t("app.press_enter"))

        elif choice == "3":
            from check_update import check_for_update
            has_update, cur_ver, latest_ver = check_for_update()
            if not cur_ver:
                print(t("settings.update_no_version"))
            elif has_update:
                print(t("settings.update_available", cur=cur_ver, latest=latest_ver))
                print(f"         {REPO_URL}/releases/tag/{latest_ver}")
            else:
                print(t("settings.update_latest", ver=cur_ver))
            input(t("app.press_enter"))

        elif choice == "4":
            # 设置启动时自动启动的服务器
            servers = list_servers(ensure_servers_folder(project_dir))
            clear_screen()
            print()
            print(t("settings.auto_start_title"))
            print()
            if servers:
                for i, s in enumerate(servers, 1):
                    print(f"  [{i}] {s['name']}")
            print(f"  [0] {t('settings.auto_start_none')}")
            print()
            c = input(t("settings.auto_start_prompt", max=len(servers))).strip()
            if c == "0":
                config["auto_start_server"] = ""
                save_config(config, project_dir)
                print(t("settings.auto_start_saved_none"))
            else:
                try:
                    idx = int(c) - 1
                    if 0 <= idx < len(servers):
                        config["auto_start_server"] = servers[idx]["name"]
                        save_config(config, project_dir)
                        print(t("settings.auto_start_saved", name=servers[idx]["name"]))
                    else:
                        print(t("app.invalid_choice_short"))
                except ValueError:
                    print(t("app.invalid_choice_short"))
            input(t("app.press_enter"))

        elif choice == "5":
            # 切换开机自启动
            from startup import is_startup_enabled, set_startup_enabled
            target = not is_startup_enabled()
            if set_startup_enabled(target):
                if target:
                    print(t("settings.startup_enabled"))
                else:
                    print(t("settings.startup_disabled"))
            else:
                print(t("settings.startup_unsupported"))
            input(t("app.press_enter"))

        else:
            print(t("app.invalid_choice_short"))


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Import / Main menu
# ---------------------------------------------------------------------------

def _prompt_zip_path() -> Optional[str]:
    """交互式问一个压缩包路径。"""
    print()
    print(t("import.prompt"))
    raw = input("  > ").strip()
    if not raw:
        return None
    path = os.path.abspath(raw.strip('"'))
    if not os.path.isfile(path):
        print(t("import.file_missing", path=path))
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".zip", ".jar"):
        print(t("import.unsupported_format", ext=ext))
        return None
    return path


def _pick_jar_interactive(jars: list[str]) -> Optional[str]:
    """候选核心 jar 多于一个时，让用户挑一个。"""
    print()
    print(t("import.select_jar", count=len(jars)))
    print()
    for i, j in enumerate(jars, 1):
        print(f"  [{i}] {j}")
    print()
    while True:
        try:
            choice = input(t("import.pick_prompt", max=len(jars))).strip()
            idx = int(choice) - 1
            if 0 <= idx < len(jars):
                return jars[idx]
        except ValueError:
            pass
        print(t("app.invalid_choice_short"))


def _import_server_flow(servers_dir: str, project_dir: str) -> None:
    """导入压缩包：取路径 → 解压计划 → 选核心 → 落地。"""
    zip_path = _prompt_zip_path()
    if zip_path is None:
        input(t("app.press_enter"))
        return

    try:
        plan = plan_import(zip_path, project_dir, reporter=_REPORTER)
    except CoreError as e:
        _report_error(e)
        input(t("app.press_enter"))
        return

    try:
        jar_rel = plan.auto_choice or _pick_jar_interactive(plan.candidates)
        if jar_rel is None:
            abort_import(plan)
            return
        detected_key = ("import.detected_auto" if plan.auto_choice
                        else "import.detected_selected")
        print(t("import.auto_detect", detected=t(detected_key), jar=jar_rel))
        server_dir = commit_import(plan, jar_rel, servers_dir, reporter=_REPORTER)
        print(t("import.dir_info", dir=server_dir))
    except CoreError as e:
        abort_import(plan)
        _report_error(e)
    input(t("app.press_enter"))


def show_main_menu(servers_dir: str, project_dir: str) -> Optional[dict[str, Any]]:
    """
    Main menu. Returns selected server config for launching, or None to exit.
    """
    while True:
        servers = list_servers(servers_dir)
        server_count = len(servers)

        clear_screen()
        print()
        print(f"  {t('app.title')}")
        print(t("app.server_dir", path=servers_dir))
        print()

        if server_count == 0:
            print(t("app.no_servers"))
        else:
            print(t("menu.main.start_server", n=1, count=server_count))
        print(t("menu.main.import", n=2))
        print(t("menu.main.download", n=3))
        print(t("menu.main.manage", n=4))
        print(t("menu.main.export", n=5))
        print(t("menu.main.tunnel", n=6))
        print(t("menu.main.settings", n=7))
        print(t("menu.main.exit", n=0))
        print()

        choice = input(t("menu.main.prompt", max=7)).strip()

        if choice == "0":
            print(t("app.exit_user"))
            sys.exit(0)

        elif choice == "1":
            if server_count == 0:
                print(t("app.no_servers_start"))
                input(t("app.press_enter"))
                continue
            selected = _select_server_for_launch(servers, servers_dir)
            if selected:
                return selected
            continue

        elif choice == "2":
            _import_server_flow(servers_dir, project_dir)
            continue

        elif choice == "3":
            from download_msl import show_download_server_menu
            show_download_server_menu(servers_dir, project_dir)
            input(t("app.press_enter"))
            continue

        elif choice == "4":
            servers_now = list_servers(servers_dir)
            if servers_now:
                _pick_server_for_management(servers_now, servers_dir)
            else:
                print(t("app.no_servers_manage"))
                input(t("app.press_enter"))
            continue

        elif choice == "5":
            servers_now = list_servers(servers_dir)
            if servers_now:
                _export_server(servers_now, servers_dir, project_dir)
            else:
                print(t("app.no_servers_export"))
                input(t("app.press_enter"))
            continue

        elif choice == "6":
            servers_now = list_servers(servers_dir)
            _tunnel_menu(servers_now, servers_dir, project_dir)
            continue

        elif choice == "7":
            _settings_menu(project_dir)
            continue

        else:
            print(t("app.invalid_choice", max=7))


def _select_server_for_launch(
    servers: list[dict[str, Any]],
    servers_dir: str,
) -> Optional[dict[str, Any]]:
    """List servers for launch selection. Returns selected config or None."""
    while True:
        clear_screen()
        print()
        print(t("menu.select_server.title"))
        print()
        for i, s in enumerate(servers, 1):
            ver = s.get("mc_version", "?")
            jar = s.get("jar", "?")
            mem = f"{s.get('min_mem', '?')} / {s.get('max_mem', '?')}"
            print(t("menu.select_server.line", i=i, name=s["name"], ver=ver, jar=jar, mem=mem))
        print(t("menu.select_server.back", n=0))
        print()

        choice = input(t("menu.select_server.prompt", max=len(servers))).strip()

        if choice == "0":
            return None

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                return servers[idx]
        except ValueError:
            pass
        print(t("app.invalid_choice", max=len(servers)))