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
import re
import shutil
import tempfile
import time
import zipfile
from typing import NamedTuple, Optional

from .archive import MAX_MEMBERS, MAX_TOTAL_BYTES, _zip_is_dir, _zip_is_symlink
from .errors import CoreError
from .report import NULL, Reporter, as_reporter
from server_manager import EXPORT_CATEGORIES, collect_export_files, save_server_config

TEMP_DIR_NAME = ".import_temp"

# 和 provision._INVALID_NAME_CHARS 保持同一套规则。这里不 import 它是因为
# provision 在模块顶部就 import 了本模块，反过来引用会形成循环。
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_suggested_name(zip_basename: str) -> str:
    """从压缩包文件名推一个能用的默认实例名。

    包名什么字符都可能有（"我的服务器 v1.2 (副本).zip"），直接当目录名会在
    Windows 上炸掉。这里只做清洗，保证默认值起步就合法；用户显式给的名字
    仍然走 commit_import 里的严格校验。
    """
    stem = os.path.splitext(zip_basename)[0]
    cleaned = _INVALID_NAME_CHARS.sub("_", stem).strip().rstrip(" .")
    return cleaned or "instance"

# 导入临时目录的保留时长。每次导入用独立目录，进程被杀时会留下垃圾，
# 所以超过这个岁数的就顺手清掉（正在进行的导入不可能有这么老）。
_STALE_TEMP_AGE = 24 * 3600


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


def _sweep_stale_temp_dirs(project_dir: str) -> None:
    """清掉以前崩溃留下的导入临时目录。"""
    try:
        entries = os.listdir(project_dir)
    except OSError:
        return
    now = time.time()
    for entry in entries:
        if not entry.startswith(TEMP_DIR_NAME):
            continue
        full = os.path.join(project_dir, entry)
        try:
            if now - os.path.getmtime(full) > _STALE_TEMP_AGE:
                shutil.rmtree(full, ignore_errors=True)
        except OSError:
            continue


def _safe_extract(zip_path: str, target_dir: str, reporter: Reporter) -> None:
    """解压并挡住 zip slip 与压缩炸弹，同时按字节数汇报进度。

    闸门与 core/archive.py 用同一套常量。导入这条路以前一道闸都没有，可它
    处理的正是用户从网上下回来的整合包——最需要防的就是这类输入。
    """
    target_abs = os.path.abspath(target_dir)
    prefix = target_abs + os.sep
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        if len(members) > MAX_MEMBERS:
            raise CoreError("web.error.extract_too_many", max=MAX_MEMBERS)
        declared = sum(m.file_size for m in members if not m.is_dir())
        if declared > MAX_TOTAL_BYTES:
            raise CoreError("web.error.extract_too_large",
                            max=round(MAX_TOTAL_BYTES / 1024 / 1024 / 1024))
        total = declared
        extracted = 0
        for member in members:
            member_path = os.path.abspath(os.path.join(target_abs, member.filename))
            if not member_path.startswith(prefix):
                reporter.warn("import.path_traversal", name=member.filename)
                continue
            # 符号链接一律不建。zipfile 在 POSIX 上会照着成员的 unix 权限位
            # 建出软链，而越界检查只看成员名：先建一个指向 /etc 的链，再用
            # 后面的 "link/passwd" 成员写穿它，成员名本身完全合法。archive.py
            # 早就这么防了，这里同样处理。
            if _zip_is_symlink(member):
                reporter.warn("import.path_traversal", name=member.filename)
                continue
            if _zip_is_dir(member):
                os.makedirs(member_path, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(member_path), exist_ok=True)
            # 自己流式读，不用 zf.extract：成员头里声明的 file_size 可以撒谎，
            # 只有真正写下去的字节数才算数。
            with zf.open(member) as src, open(member_path, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    extracted += len(chunk)
                    if extracted > MAX_TOTAL_BYTES:
                        raise CoreError(
                            "web.error.extract_too_large",
                            max=round(MAX_TOTAL_BYTES / 1024 / 1024 / 1024))
                    dst.write(chunk)
            if total > 0:
                reporter.progress(ratio=min(1.0, extracted / total),
                                  current=extracted, total=total)
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

    _sweep_stale_temp_dirs(project_dir)

    # 每次导入用独立的临时目录。以前是一个固定的 .import_temp，开头先
    # rmtree 再 makedirs——两个并发的导入会互相把对方正在解压的东西删掉，
    # 而 web 那边的 _IMPORT_PLANS_LOCK 只保护 plan 字典，管不到目录。
    try:
        temp_dir = tempfile.mkdtemp(prefix=TEMP_DIR_NAME + "-", dir=project_dir)
    except OSError as e:
        raise CoreError("web.error.mkdir_failed", msg=str(e)) from e

    reporter.log("import.extracting", name=os.path.basename(zip_path))
    try:
        _safe_extract(zip_path, temp_dir, reporter)
    except zipfile.BadZipFile as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise CoreError("import.bad_zip", name=os.path.basename(zip_path)) from e
    except OSError as e:
        # 落盘失败（磁盘满、没权限、父路径被占用）和「压缩包坏了」是两回事。
        # 以前两者都报 import.bad_zip，用户拿着一个完好的整合包去反复重下，
        # 而真正的问题是磁盘满了。
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise CoreError("web.error.write_failed", msg=str(e)) from e
    except CoreError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    candidates = find_core_jars(temp_dir)
    if not candidates:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise CoreError("import.no_jar")

    return ImportPlan(
        zip_path=zip_path,
        temp_dir=temp_dir,
        project_dir=project_dir,
        candidates=candidates,
        suggested_name=_safe_suggested_name(os.path.basename(zip_path)),
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

    # 这是全流程里唯一把外部字符串直接拼成 servers/ 下路径的地方。
    # 以前 name 原样落盘：传 "../../x" 或 "C:\\x" 就能在 servers/ 之外建目录
    # 并把整包内容搬进去。校验放在这里而不是 Web 层，CLI 导入也一并治了。
    from .provision import validate_instance_name

    base_name = validate_instance_name(name or plan.suggested_name)
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
    # 先写临时文件，成功再换名。就地写的话，中途抛异常时 ZipFile.__exit__
    # 仍会把中央目录写出去，留下一个「打得开、内容却缺了一半」的备份——
    # 用户看到文件在那儿，会以为备份是好的。
    tmp = output_path + ".mstl-export"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, (arcname, abs_path) in enumerate(files, 1):
                zf.write(abs_path, arcname)
                reporter.progress(current=i, total=total, ratio=i / total, done=i == total)
        os.replace(tmp, output_path)
    except (OSError, zipfile.BadZipFile) as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
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
