"""
新建实例：建目录、装服务端核心、导入存档。

CLI 里这三件事分别散在 download_msl 的交互菜单和 transfer 的两步导入里。
网页面板不能停在原地等人按序号选核心，所以这里给出完全不提问的版本：

    create_instance(...)   建一个实例目录并写好 .server.json
    install_core(...)      从 MSL 镜像下载核心 jar 放进实例目录
    scan_archive(...)      解压存档并列出候选核心（对应 transfer.plan_import）
    import_archive(...)    用选定的核心把存档落地成实例

核心候选多于一个时，这里不替用户拍板——scan 把候选交回界面层，由界面层
问完再用 import 落地。这和 core/transfer.py 的「计划 → 落地」是同一条路。
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

from .errors import CoreError
from .report import Reporter, as_reporter
from .transfer import ImportPlan, abort_import, commit_import, plan_import
from download_msl import (
    _CATEGORY_LABELS,
    _server_display_name,
    msl_get_download_url,
    msl_get_server_types,
    msl_get_versions,
    multithreaded_download,
    safe_filename,
)
from server_manager import (
    KIND_GENERIC,
    KIND_MINECRAFT,
    LAUNCH_EXEC,
    LAUNCH_MODES,
    load_server_config,
    save_server_config,
)

# 实例名里不能出现的字符：Windows 全量非法字符 + 控制字符
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
MAX_NAME_LENGTH = 64

# 这些标记出现在版本号里就当作测试版，默认不往面板里放
_PRERELEASE_MARKERS = ("pre", "rc", "snapshot", "alpha", "beta")


def validate_instance_name(name: str) -> str:
    """校验并返回清理过的实例名，不合法就抛 CoreError。"""
    cleaned = (name or "").strip()
    if not cleaned:
        raise CoreError("web.error.name_required")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise CoreError("web.error.name_too_long", max=MAX_NAME_LENGTH)
    if cleaned in (".", ".."):
        raise CoreError("web.error.bad_instance_name", name=name)
    if _INVALID_NAME_CHARS.search(cleaned):
        raise CoreError("web.error.bad_instance_name", name=name)
    # Windows 不允许文件名以点或空格结尾，去掉比事后失败强
    cleaned = cleaned.rstrip(" .")
    if not cleaned:
        raise CoreError("web.error.bad_instance_name", name=name)
    if cleaned.split(".")[0].lower() in _RESERVED_NAMES:
        raise CoreError("web.error.bad_instance_name", name=name)
    return cleaned


def instance_path(servers_dir: str, name: str) -> str:
    """返回实例目录；名字不合法或不存在都抛 CoreError。"""
    cleaned = validate_instance_name(name)
    path = os.path.join(servers_dir, cleaned)
    if not os.path.isdir(path):
        raise CoreError("web.error.unknown_instance", name=name)
    return path


def normalize_launch(value) -> dict[str, Any]:
    """把界面层传来的启动配置收敛成可信结构。

    这里不校验「字段填全了没有」——先建实例、稍后再配是允许的，
    真正启动时 build_generic_plan 才会报缺什么。
    """
    raw = value if isinstance(value, dict) else {}
    mode = str(raw.get("mode") or LAUNCH_EXEC).strip().lower()
    args = raw.get("args")
    if isinstance(args, str):
        args = args.split()
    elif not isinstance(args, list):
        args = []
    return {
        "mode": mode if mode in LAUNCH_MODES else LAUNCH_EXEC,
        "program": str(raw.get("program") or "").strip(),
        "args": [str(item) for item in args],
        "command": str(raw.get("command") or "").strip(),
        "workdir": str(raw.get("workdir") or "").strip(),
    }


# 「改配置」能动的字段白名单。
# kind 与 name 故意不在里面：改名会让 .server.json 里的 name 与目录名对不上
# （面板和 CLI 都是先扫目录、再比 name），切换种类等于把实例换成另一个东西，
# 两者都不该混在「保存设置」里悄悄发生。
_PATCH_FIELDS = (
    "mc_version", "jar", "java_path", "min_mem", "max_mem",
    "extra_jvm_args", "extra_server_args", "process_priority",
    "launch", "openfrp", "auto_start",
    # 崩溃自动重启。放在这里而不是只写进 .server.json：面板保存设置走的是
    # update_instance，字段不在白名单里就会被静默丢掉，用户勾了没反应。
    "auto_restart", "restart_max",
)


def update_instance(server_dir: str, patch: dict) -> dict[str, Any]:
    """把 patch 里认识的字段合并进 .server.json，返回合并后的完整配置。"""
    cfg = load_server_config(server_dir)
    for key in _PATCH_FIELDS:
        if key in patch and patch[key] is not None:
            cfg[key] = patch[key]
    if "launch" in patch:
        cfg["launch"] = normalize_launch(patch["launch"])
    save_server_config(cfg, server_dir)
    return load_server_config(server_dir)


def create_instance(
    servers_dir: str,
    name: str,
    *,
    kind: str = KIND_MINECRAFT,
    mc_version: str = "",
    min_mem: str = "1G",
    max_mem: str = "4G",
    jar_name: Optional[str] = None,
    launch: Optional[dict] = None,
) -> str:
    """新建一个实例目录并写好 .server.json，返回实例目录。

    kind 为 generic 时是一台「通用实例」：不碰 Java 也不碰 jar，启动时按
    launch 里的配置去跑一个可执行文件或一段命令。

    实例名已被占用时抛 CoreError，不自动改名——用户是明确指定了名字的，
    悄悄变成 name_1 只会让人找不到自己的实例。
    """
    cleaned = validate_instance_name(name)
    server_dir = os.path.join(servers_dir, cleaned)
    if os.path.exists(server_dir):
        raise CoreError("web.error.instance_exists", name=cleaned)

    kind = str(kind or KIND_MINECRAFT).strip().lower()
    if kind not in (KIND_MINECRAFT, KIND_GENERIC):
        raise CoreError("web.error.bad_instance_kind", kind=kind)

    try:
        os.makedirs(server_dir)
    except OSError as e:
        raise CoreError("web.error.mkdir_failed", msg=str(e)) from e

    save_server_config(
        {
            "name": cleaned,
            "kind": kind,
            "mc_version": mc_version or "",
            "jar": jar_name or "server.jar",
            "java_path": None,
            "min_mem": min_mem or "1G",
            "max_mem": max_mem or "4G",
            "extra_jvm_args": [],
            "extra_server_args": [],
            "process_priority": "normal",
            "launch": normalize_launch(launch),
        },
        server_dir,
    )
    return server_dir


def install_core(server_dir: str, core_type: str, core_version: str,
                 *, reporter: Optional[Reporter] = None,
                 on_progress=None) -> str:
    """从 MSL 镜像下载指定核心到实例目录，返回落地后的 jar 文件名。

    on_progress(ratio, downloaded, total) 会透传给下载引擎，供界面层画进度。
    """
    reporter = as_reporter(reporter)
    if not core_type or not core_version:
        raise CoreError("web.error.core_selection_required")

    reporter.log("download.fetching_url")
    info = msl_get_download_url(core_type, core_version)
    if not isinstance(info, dict) or not info.get("url"):
        raise CoreError("web.error.core_url_failed", name=core_type, ver=core_version)

    url = str(info["url"])
    # 从下载地址里取文件名；取不到就退回「核心类型.jar」
    jar_name = os.path.basename(url.split("?")[0])
    if not jar_name.lower().endswith(".jar"):
        jar_name = f"{core_type}.jar"
    jar_name = safe_filename(jar_name, fallback=f"{core_type}.jar")

    os.makedirs(server_dir, exist_ok=True)
    target = os.path.join(server_dir, jar_name)
    reporter.progress(label=_server_display_name(core_type))
    if not multithreaded_download(url, target, desc=_server_display_name(core_type),
                                  quiet=True, on_progress=on_progress,
                                  expected_sha256=info.get("sha256")):
        raise CoreError("web.error.core_download_failed", name=core_type)
    if not os.path.isfile(target) or os.path.getsize(target) == 0:
        raise CoreError("web.error.core_download_failed", name=core_type)
    return jar_name


def set_instance_core(server_dir: str, jar_name: str,
                      *, mc_version: Optional[str] = None) -> None:
    """把 .server.json 里的 jar / mc_version 更新成刚装好的核心。"""
    cfg = load_server_config(server_dir)
    cfg["jar"] = jar_name
    if mc_version:
        cfg["mc_version"] = mc_version
    save_server_config(cfg, server_dir)


def list_core_types(project_dir: str) -> list[dict[str, Any]]:
    """列出可用服务端核心，按 MSL 的分类分组顺序展开成一维列表。"""
    categorized = msl_get_server_types(project_dir)
    if not isinstance(categorized, dict):
        raise CoreError("web.error.core_list_failed")

    out: list[dict[str, Any]] = []
    for category, ids in categorized.items():
        if not isinstance(ids, list):
            continue
        label_key = _CATEGORY_LABELS.get(category, category)
        for core_id in ids:
            if not isinstance(core_id, str):
                continue
            out.append({
                "id": core_id,
                "name": _server_display_name(core_id),
                "category": category,
                "category_key": label_key,
            })
    if not out:
        raise CoreError("web.error.core_list_failed")
    return out


def list_core_versions(core_type: str, project_dir: str) -> tuple[list[str], str]:
    """列出某个核心的稳定版本，返回 (版本列表, 描述)。"""
    if not core_type:
        raise CoreError("web.error.core_selection_required")
    data = msl_get_versions(core_type, project_dir)
    if not isinstance(data, dict):
        raise CoreError("web.error.core_list_failed")

    raw = data.get("versions") or []
    versions = [str(v) for v in raw if isinstance(v, (str, int, float))]
    if not versions:
        raise CoreError("web.error.core_list_failed")

    stable = [v for v in versions
              if not any(m in v.lower() for m in _PRERELEASE_MARKERS)]
    return (stable or versions), str(data.get("description") or "")


# 面板上列出来的版本个数上限；再多也没人往下翻
MAX_VERSION_CHOICES = 80


def scan_archive(project_dir: str, zip_path: str,
                 *, reporter: Optional[Reporter] = None) -> ImportPlan:
    """解压存档并列出候选核心。对应 core.transfer.plan_import。"""
    return plan_import(zip_path, project_dir, reporter=reporter)


def import_archive(plan: ImportPlan, jar_rel: str, servers_dir: str,
                   *, name: Optional[str] = None,
                   reporter: Optional[Reporter] = None) -> str:
    """用选定的核心把存档落地成实例。失败时清掉临时目录。"""
    try:
        return commit_import(plan, jar_rel, servers_dir, name=name, reporter=reporter)
    except CoreError:
        abort_import(plan)
        raise


def install_local_jar(server_dir: str, src_path: str,
                      *, reporter: Optional[Reporter] = None) -> str:
    """把磁盘上已有的 jar 复制进实例目录，返回文件名。"""
    from .instances import import_jar

    name = import_jar(server_dir, src_path)
    return name


def timestamp_name(prefix: str = "server") -> str:
    """给「不想起名字」的场景生成一个稳定的默认名。"""
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}"


__all__ = [
    "MAX_NAME_LENGTH",
    "MAX_VERSION_CHOICES",
    "validate_instance_name",
    "normalize_launch",
    "update_instance",
    "instance_path",
    "create_instance",
    "install_core",
    "set_instance_core",
    "install_local_jar",
    "list_core_types",
    "list_core_versions",
    "scan_archive",
    "import_archive",
    "timestamp_name",
]
