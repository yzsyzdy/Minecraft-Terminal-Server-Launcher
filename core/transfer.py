"""
压缩包的进出：导入已有服务器存档、导出实例存档。

导入特意拆成「计划 → 落地」两步。原因只有一个：候选核心 jar 多于
一个时该选哪个，core 没有资格拍板。

    plan = plan_import(zip, project_dir)      # 解压到临时目录，列出候选
    ...界面层去问用户，或者把 plan 直接甩给浏览器...
    commit_import(plan, jar_rel, servers_dir) # 用选定的 jar 落地成实例

这样 CLI 弹一个序号菜单、Web 面板回一次 POST，走的是同一条 core 路径。
中途放弃时调用 abort_import(plan) 清掉临时目录。
"""

from __future__ import annotations

import os
import shutil
import zipfile
from typing import NamedTuple, Optional

from .errors import CoreError
from .report import NULL, Reporter, as_reporter
from server_manager import EXPORT_CATEGORIES, collect_export_files, save_server_config

TEMP_DIR_NAME = ".import_temp"


class ExportResult(NamedTuple):
    """一次导出的结果。界面层拿它来拼成功提示。"""

    path: str
    file_count: int
    size_bytes: int


class ImportPlan(NamedTuple):
    """一次导入的中间状态。commit 或 abort 之后这个对象就作废了。"""

    zip_path: str
    temp_dir: str
    project_dir: str
    candidates: list[str]
    suggested_name: str

    @property
    def auto_choice(self) -> Optional[str]:
        """候选只有一个时可以直接用，多于一个返回 None。"""
        return self.candidates[0] if len(self.candidates) == 1 else None


def _safe_extract(zip_path: str, target_dir: str, reporter: Reporter) -> None:
    """解压并挡住 zip slip，同时按字节数汇报进度。"""
    target_abs = os.path.abspath(target_dir)
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        total = sum(m.file_size for m in members if not m.is_dir())
        extracted = 0
        for member in members:
            member_path = os.path.abspath(os.path.join(target_abs, member.filename))
            if not member_path.startswith(target_abs + os.sep):
                reporter.warn("import.path_traversal", name=member.filename)
                continue
            zf.extract(member, target_abs)
            if not member.is_dir():
                extracted += member.file_size
            if total > 0:
                reporter.progress(ratio=extracted / total, current=extracted, total=total)
        reporter.progress(ratio=1.0, done=True)


def find_core_jars(directory: str) -> list[str]:
    """挑出候选核心 jar。

    只看第一层，压缩包多套了一层目录时再往下钻一层，
    这样 plugins/ 里的 jar 不会被误认成核心。
    """
    jars: list[str] = []
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return jars

    for entry in entries:
        full = os.path.join(directory, entry)
        if os.path.isfile(full) and entry.lower().endswith(".jar"):
            jars.append(entry)
    if jars:
        return jars

    subdirs = [e for e in entries if os.path.isdir(os.path.join(directory, e))]
    if len(subdirs) == 1:
        sub = subdirs[0]
        try:
            for entry in sorted(os.listdir(os.path.join(directory, sub))):
                if entry.lower().endswith(".jar") and os.path.isfile(
                    os.path.join(directory, sub, entry)
                ):
                    jars.append(f"{sub}/{entry}")
        except OSError:
            pass
    return jars


def plan_import(
    zip_path: str,
    project_dir: str,
    *,
    reporter: Optional[Reporter] = None,
) -> ImportPlan:
    """解压存档到临时目录并列出候选核心，失败时抛 CoreError。"""
    reporter = as_reporter(reporter)
    zip_path = os.path.abspath(zip_path)
    if not os.path.isfile(zip_path):
        raise CoreError("import.file_missing", path=zip_path)

    temp_dir = os.path.join(project_dir, TEMP_DIR_NAME)
    if os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir)

    reporter.log("import.extracting", name=os.path.basename(zip_path))
    try:
        _safe_extract(zip_path, temp_dir, reporter)
    except zipfile.BadZipFile as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise CoreError("import.bad_zip", name=os.path.basename(zip_path)) from e
    except OSError as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise CoreError("import.bad_zip", name=os.path.basename(zip_path)) from e

    candidates = find_core_jars(temp_dir)
    if not candidates:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise CoreError("import.no_jar")

    return ImportPlan(
        zip_path=zip_path,
        temp_dir=temp_dir,
        project_dir=project_dir,
        candidates=candidates,
        suggested_name=os.path.splitext(os.path.basename(zip_path))[0],
    )


def commit_import(
    plan: ImportPlan,
    jar_rel: str,
    servers_dir: str,
    *,
    name: Optional[str] = None,
    reporter: Optional[Reporter] = None,
) -> str:
    """把临时目录里的内容落地成一个正式实例，返回实例目录。

    jar_rel 必须是 plan.candidates 里的一项；jar 在子目录里时，
    该子目录会被当成实例根，避免多套一层。
    """
    reporter = as_reporter(reporter)
    if jar_rel not in plan.candidates:
        raise CoreError("import.no_jar")

    base_name = name or plan.suggested_name
    server_dir = os.path.join(servers_dir, base_name)
    counter = 1
    while os.path.exists(server_dir):
        server_dir = os.path.join(servers_dir, f"{base_name}_{counter}")
        counter += 1
    os.makedirs(server_dir)

    jar_rel_dir = os.path.dirname(jar_rel)
    source = os.path.join(plan.temp_dir, jar_rel_dir) if jar_rel_dir else plan.temp_dir
    for item in os.listdir(source):
        shutil.move(os.path.join(source, item), server_dir)

    save_server_config(
        {
            "name": os.path.basename(server_dir),
            "mc_version": "",
            "jar": os.path.basename(jar_rel),
            "java_path": None,
            "min_mem": "1G",
            "max_mem": "4G",
            "extra_jvm_args": [],
            "extra_server_args": [],
        },
        server_dir,
    )

    abort_import(plan)
    reporter.log("import.success", name=os.path.basename(server_dir))
    return server_dir


def abort_import(plan: ImportPlan) -> None:
    """清掉导入用的临时目录。重复调用是安全的。"""
    if os.path.isdir(plan.temp_dir):
        shutil.rmtree(plan.temp_dir, ignore_errors=True)


def export_instance(
    server_dir: str,
    output_path: str,
    categories: set[str],
    *,
    reporter: Optional[Reporter] = None,
) -> ExportResult:
    """按分类把实例打包成 zip，失败时抛 CoreError。"""
    reporter = as_reporter(reporter)
    files = collect_export_files(server_dir, categories)
    if not files:
        raise CoreError("export.no_files")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    total = len(files)
    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, (arcname, abs_path) in enumerate(files, 1):
                zf.write(abs_path, arcname)
                reporter.progress(current=i, total=total, ratio=i / total, done=i == total)
    except (OSError, zipfile.BadZipFile) as e:
        raise CoreError("export.failed", msg=str(e)) from e

    return ExportResult(
        path=output_path,
        file_count=total,
        size_bytes=os.path.getsize(output_path),
    )


__all__ = [
    "TEMP_DIR_NAME",
    "ExportResult",
    "ImportPlan",
    "plan_import",
    "commit_import",
    "abort_import",
    "export_instance",
    "find_core_jars",
    "EXPORT_CATEGORIES",
]
