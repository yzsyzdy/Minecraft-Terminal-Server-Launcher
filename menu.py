"""
Interactive menus: main menu, server list, import/download/export guide, plugin/mod management.
"""

import sys
import os
import shutil
from typing import Any, Optional

from clear import clear_screen
from i18n import t
from config import load_config
from server_manager import (
    _prompt_zip_path,
    import_server_from_zip,
    list_servers,
    load_server_config,
    save_server_config,
    classify_server_type,
    export_server_to_zip,
    EXPORT_CATEGORIES,
)


# ---------------------------------------------------------------------------
# Generic jar management utilities
# ---------------------------------------------------------------------------

def _list_jars(directory: str) -> tuple[list[str], list[str]]:
    """Scan directory, return (enabled .jar names, disabled .jar.disabled names)."""
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
    """Interactively delete a jar file."""
    enabled, disabled = _list_jars(directory)
    if not enabled and not disabled:
        print(t("menu.plugin.nothing_delete"))
        input(t("app.press_enter"))
        return
    target = input(t("menu.plugin.enter_filename")).strip()
    if not target:
        return
    full_path = os.path.join(directory, target)
    if not os.path.isfile(full_path):
        print(t("menu.plugin.add_file_missing", path=target))
        input(t("app.press_enter"))
        return
    confirm = input(t("menu.plugin.confirm_delete", name=target)).strip().lower()
    if confirm == "y":
        try:
            os.remove(full_path)
            print(t("menu.plugin.deleted", name=target))
        except OSError as e:
            print(t("menu.plugin.delete_failed", msg=str(e)))
    else:
        print(t("menu.plugin.cancelled"))
    input(t("app.press_enter"))


def _toggle_jar(directory: str) -> None:
    """Interactively disable/enable a jar (.jar <-> .jar.disabled)."""
    enabled, disabled = _list_jars(directory)
    if not enabled and not disabled:
        print(t("menu.plugin.nothing_toggle"))
        input(t("app.press_enter"))
        return
    target = input(t("menu.plugin.enter_toggle_file")).strip()
    if not target:
        return
    full_path = os.path.join(directory, target)
    if not os.path.isfile(full_path):
        print(t("menu.plugin.add_file_missing", path=target))
        input(t("app.press_enter"))
        return
    if target.endswith(".disabled"):
        new_name = target[:-9]
        if new_name == target:
            print(t("menu.plugin.state_unrecognized"))
            input(t("app.press_enter"))
            return
        try:
            os.rename(full_path, os.path.join(directory, new_name))
            print(t("menu.plugin.enabled", name=new_name))
        except OSError as e:
            print(t("menu.plugin.toggle_failed", msg=str(e)))
    else:
        try:
            os.rename(full_path, os.path.join(directory, target + ".disabled"))
            print(t("menu.plugin.disabled", name=target + ".disabled"))
        except OSError as e:
            print(t("menu.plugin.toggle_failed", msg=str(e)))
    input(t("app.press_enter"))


def _add_jar_from_file(directory: str) -> None:
    """Interactively import a local jar file."""
    print()
    print(t("menu.plugin.add_prompt"))
    jar_path = input("  > ").strip().strip('"')
    if not jar_path:
        return
    jar_abs = os.path.abspath(jar_path)
    if not os.path.isfile(jar_abs):
        print(t("menu.plugin.add_file_missing", path=jar_abs))
        input(t("app.press_enter"))
        return
    if not jar_abs.lower().endswith(".jar"):
        print(t("menu.plugin.add_not_jar"))
        input(t("app.press_enter"))
        return
    try:
        shutil.copy2(jar_abs, directory)
        print(t("menu.plugin.add_imported", name=os.path.basename(jar_abs)))
    except OSError as e:
        print(t("menu.plugin.add_copy_failed", msg=str(e)))
    input(t("app.press_enter"))


# ---------------------------------------------------------------------------
# Plugin management
# ---------------------------------------------------------------------------

def _get_plugins_dir(server_dir: str) -> str:
    return os.path.join(server_dir, "plugins")


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
        enabled, disabled = _list_jars(directory)
        total = len(enabled) + len(disabled)
        clear_screen()
        print()
        print(f"  [{label}] {name}")
        print(t("menu.plugin.dir", dir=directory))
        print()
        if total == 0:
            print(t("menu.plugin.empty"))
        else:
            print(t("menu.plugin.count", count=total))
            print()
            for p in enabled:
                print(f"      [E] {p}")
            for p in disabled:
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
    return os.path.join(server_dir, "mods")


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
    export_server_to_zip(server_dir, out, selected)
    input(t("app.press_enter"))


# ---------------------------------------------------------------------------
# OpenFrp 内网穿透
# ---------------------------------------------------------------------------

from openfrp.tunnel_manager import (
    get_tunnels_by_token, get_node_list, create_tunnel, delete_tunnel,
    download_frpc, get_frpc_local_path, TunnelInfo,
)


def _openfrp_menu(servers: list[dict], servers_dir: str, project_dir: str) -> None:
    """OpenFrp tunnel management menu."""
    while True:
        clear_screen()
        print()
        print("  [OpenFrp] 内网穿透")
        print()
        print("  [1] 查看隧道列表 (token)")
        print("  [2] 下载/updaddr frpc")
        print("  [3] 创建 Minecraft 隧道")
        print("  [4] 绑定隧道到服务器")
        print("  [0] 返回")
        print()
        choice = input("  请选择 (0-4): ").strip()

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


def _openfrp_list_tunnels(project_dir: str) -> None:
    """List tunnels by user token."""
    print()
    token = input("  请输入用户 token: ").strip()
    if not token:
        return
    print("  [OpenFrp] 正在获取隧道列表...")
    groups = get_tunnels_by_token(token)
    if groups is None:
        print("  [OpenFrp] " + t("app.error", msg="无法获取隧道列表，请检查 token"))
        input(t("app.press_enter"))
        return
    print()
    for node_name, tunnels in groups:
        print(f"  [{node_name}]")
        for t_in in tunnels:
            online = "\u2713" if t_in.online else "\u2717"
            print(f"      [{online}] \u00a7{t_in.proxy_id} {t_in.name}  ({t_in.proxy_type}) {t_in.remote_addr}")
        print()
    print(f"  \u5171 {sum(len(g) for _, g in groups)} \u6761隧道")
    input(t("app.press_enter"))


def _openfrp_download_frpc(project_dir: str) -> None:
    """Download or update frpc binary."""
    existing = get_frpc_local_path(project_dir)
    if os.path.isfile(existing):
        size = os.path.getsize(existing) / 1024 / 1024
        print(f"  [OpenFrp] \u5df2存在: {existing} ({size:.1f} MB)")
        if input("  重新下载? (y/N): ").strip().lower() != "y":
            return
    path = download_frpc(project_dir)
    if path:
        print(f"  [OpenFrp] \u4e0b载完成: {path}")
    else:
        print(f"  [OpenFrp] " + t("app.error", msg="下载失败"))
    input(t("app.press_enter"))


def _openfrp_create_tunnel(project_dir: str) -> None:
    """Interactive tunnel creation wizard for Minecraft servers."""
    from openfrp.tunnel_manager import get_node_list, create_tunnel

    print()
    auth = input("  请输入 Authorization (从面板个人中心复制): ").strip()
    if not auth:
        return

    print("  [OpenFrp] 正在获取节点列表...")
    nodes = get_node_list(auth)
    if not nodes:
        print("  [OpenFrp] " + t("app.error", msg="无法获取节点列表，请检查 Authorization"))
        input(t("app.press_enter"))
        return

    # 过滤可用的 TCP 节点
    tcp_nodes = [n for n in nodes if n.protocol_support.get("tcp") and not n.fully_loaded and n.status == 200]
    if not tcp_nodes:
        print("  [OpenFrp] " + t("app.error", msg="没有可用的 TCP 节点"))
        input(t("app.press_enter"))
        return

    print()
    print("  选择节点（仅显示可用 TCP 节点）：")
    print()
    for i, n in enumerate(tcp_nodes[:20], 1):
        region = {1: "\u4e2d\u56fd\u5927\u9646", 2: "\u6e2f\u6fb3\u53f0", 3: "\u6d77\u5916"}.get(n.classify, "?")
        tag = "" if not n.need_realname else " [\u9700\u5b9e\u540d]"
        print(f"  [{i}] {n.name} ({region}){tag}")
    print("  [0] 返回")
    print()
    try:
        c = input(f"  请选择 (0-{min(len(tcp_nodes), 20)}): ").strip()
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
    name = input("  隧道名称 (留空自动生成): ").strip() or f"mc-{selected_node.node_id}"
    local_port = input("  本地端口 [25565]: ").strip() or "25565"
    remote_port = input("  远程端口 (留空随机): ").strip()
    encrypt = input("  启用加密? (Y/n): ").strip().lower()
    compress = input("  启用压缩? (Y/n): ").strip().lower()

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
    print(f"  [OpenFrp] 正在创建隧道 '{name}' ...")
    msg = create_tunnel(auth, params)
    if msg:
        print(f"  [OpenFrp] {msg}")
    else:
        print(f"  [OpenFrp] " + t("app.error", msg="创建失败，请检查 Authorization 是否有效"))
    input(t("app.press_enter"))


def _openfrp_bind_tunnel(servers: list[dict], servers_dir: str, project_dir: str) -> None:
    """Bind a tunnel to a server config."""
    while True:
        clear_screen()
        print()
        print("  选择要绑定隧道的服务器:")
        print()
        for i, s in enumerate(servers, 1):
            of = s.get("openfrp", {}) or {}
            pid = of.get("proxy_id", 0)
            tag = f" [\u96a7\u9053 #{pid}]" if pid else ""
            print(f"  [{i}] {s['name']}{tag}")
        print("  [0] 返回")
        print()
        try:
            c = input(f"  请选择 (0-{len(servers)}): ").strip()
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
    token = input("  请输入 OpenFrp token (留空保持现有): ").strip()
    pid_str = input("  隧道 ID (留空保持现有): ").strip()
    auto = input("  随服务器启动自动启动 frpc? (y/N): ").strip().lower()

    of_cfg = server_cfg.get("openfrp", {}) or {}
    if token:
        of_cfg["token"] = token
    if pid_str:
        try:
            of_cfg["proxy_id"] = int(pid_str)
        except ValueError:
            pass
    of_cfg["auto_start"] = (auto == "y")
    server_cfg["openfrp"] = of_cfg
    save_server_config(server_cfg, server_path)
    print(f"  [OpenFrp] \u7ed1\u5b9a\u5b8c\u6210: proxy_id={of_cfg.get('proxy_id', 0)}, auto_start={of_cfg['auto_start']}")
    input(t("app.press_enter"))


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

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
        print("  [6] OpenFrp \u5185\u7f51\u7a7f\u900f")
        print(t("menu.main.exit", n=0))
        print()

        choice = input(t("menu.main.prompt", max=5)).strip()

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
            zip_path = _prompt_zip_path()
            if zip_path is None:
                input(t("app.press_enter"))
                continue
            import_server_from_zip(zip_path, servers_dir, project_dir)
            input(t("app.press_enter"))
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
            _openfrp_menu(servers_now, servers_dir, project_dir)
            continue

        else:
            print(t("app.invalid_choice", max=6))


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
