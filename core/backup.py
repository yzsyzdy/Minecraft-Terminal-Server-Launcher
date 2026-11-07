"""实例备份：把整个服务端目录打成 zip，按保留份数清理旧的。

为什么单独一个模块：备份要能脱离"有人盯着面板"独立发生（定时、关服时），
所以它不依赖 HTTP 层，只依赖实例目录与配置。

一致性问题：
Minecraft 的存档一直在往 region 文件里写，直接拷会拿到"半个存档"。
所以备份前要让服务器把数据落盘并暂停写盘：

    save-all  →  save-off  →  （这时文件是静止的）  →  打包  →  save-on

这个顺序由调用方（core/runtime.py，它才拿得到控制台）负责，
本模块只管"文件静止之后怎么打包、打包完怎么清理旧份数"。

目录布局：

    <实例目录>/backups/
        world-20261107-213045.zip
        world-20261106-213045.zip

备份文件放在实例目录**内部**，这样"导出实例"时备份跟着走，
但打包时会跳过 backups/ 自己，不然会递归套娃。
"""

from __future__ import annotations

import os
import re
import time
import zipfile
from typing import NamedTuple, Optional

from .errors import CoreError

BACKUP_DIRNAME = "backups"
# 备份里不该出现的东西：备份自己、日志、缓存、崩溃报告。
# 日志几百 MB 且没有恢复价值，缓存可以直接重生成。
EXCLUDE_DIRS = {
    BACKUP_DIRNAME,          # 不套娃
    "logs",
    "crash-reports",
    "cache",
    ".mstl-tmp",
}
# 单个备份的大小上限：超过就拒绝，免得把一个 50 GB 的整合包打成 zip 卡死面板
MAX_BACKUP_BYTES = 8 * 1024 * 1024 * 1024

# world-20261107-213045.zip
_NAME_RE = re.compile(r"^(?P<base>.+)-(?P<ts>\d{8}-\d{6})(?:-\d+)?\.zip$")


class BackupEntry(NamedTuple):
    """一份备份。"""

    name: str
    path: str
    size: int
    created: float

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "created": self.created,
            "created_text": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(self.created)),
        }


def backup_dir(instance_dir: str) -> str:
    return os.path.join(instance_dir, BACKUP_DIRNAME)


def _stamp(when: Optional[float] = None) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(when or time.time()))


def _unique_path(directory: str, stem: str, when: Optional[float] = None) -> str:
    """同一个秒内备两次也不能互相覆盖。"""
    stamp = _stamp(when)
    candidate = os.path.join(directory, f"{stem}-{stamp}.zip")
    index = 1
    while os.path.exists(candidate):
        index += 1
        candidate = os.path.join(directory, f"{stem}-{stamp}-{index}.zip")
    return candidate


def _walk_for_backup(instance_dir: str) -> tuple[list[tuple[str, str]], int]:
    """列出要打包的 (绝对路径, 包内相对路径)，顺带算总大小。

    跳过 EXCLUDE_DIRS 与符号链接（跟压缩那段的安全口径保持一致：
    不把指向实例目录外面的东西打进包里）。
    """
    pairs: list[tuple[str, str]] = []
    total = 0
    root = os.path.abspath(instance_dir)
    for current, dirs, names in os.walk(root):
        # 就地改 dirs 才能剪掉整棵子树，比后面再过滤快得多
        dirs[:] = [d for d in dirs
                   if d not in EXCLUDE_DIRS
                   and not os.path.islink(os.path.join(current, d))]
        for name in names:
            full = os.path.join(current, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            total += size
            if total > MAX_BACKUP_BYTES:
                raise CoreError("web.error.backup_too_large",
                                mb=int(MAX_BACKUP_BYTES // (1024 * 1024)))
            pairs.append((full, rel))
    return pairs, total


def create(instance_dir: str, name: str = "", *, when: Optional[float] = None,
           keep: int = 0) -> BackupEntry:
    """给实例目录打一份 zip 备份。

    name 为空时用实例目录名做前缀。keep > 0 时打完顺手清理旧份数。
    """
    instance_dir = os.path.abspath(instance_dir)
    if not os.path.isdir(instance_dir):
        raise CoreError("web.error.no_such_instance", name=instance_dir)

    pairs, total = _walk_for_backup(instance_dir)
    if not pairs:
        raise CoreError("web.error.backup_nothing")

    stem = (name or os.path.basename(instance_dir.rstrip("\\/")) or "backup")
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .") or "backup"

    directory = backup_dir(instance_dir)
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as e:
        raise CoreError("web.error.mkdir_failed", msg=str(e)) from e

    target = _unique_path(directory, stem, when)
    tmp = target + ".part"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as zf:
            for full, rel in pairs:
                try:
                    zf.write(full, rel)
                except OSError:
                    # 单个文件在打包途中被删/被占用：跳过它，别让整份备份失败。
                    # Minecraft 运行中会短暂锁住 session.lock 这类文件。
                    continue
        os.replace(tmp, target)     # 打包完整才改名，避免留下半个 zip
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    try:
        size = os.path.getsize(target)
    except OSError:
        size = 0
    entry = BackupEntry(name=os.path.basename(target), path=target,
                        size=size, created=os.path.getmtime(target))
    if keep > 0:
        prune(instance_dir, keep)
    return entry


def list_backups(instance_dir: str) -> list[BackupEntry]:
    """按时间倒序列出备份（最新的在前）。"""
    directory = backup_dir(instance_dir)
    if not os.path.isdir(directory):
        return []
    entries: list[BackupEntry] = []
    for name in os.listdir(directory):
        if not name.lower().endswith(".zip"):
            continue
        path = os.path.join(directory, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        entries.append(BackupEntry(name=name, path=path, size=stat.st_size,
                                   created=stat.st_mtime))
    entries.sort(key=lambda item: item.created, reverse=True)
    return entries


def prune(instance_dir: str, keep: int) -> list[str]:
    """只保留最新的 keep 份，返回被删掉的文件名。"""
    keep = max(0, int(keep))
    if keep <= 0:
        return []
    entries = list_backups(instance_dir)
    removed: list[str] = []
    for entry in entries[keep:]:
        try:
            os.remove(entry.path)
            removed.append(entry.name)
        except OSError:
            continue
    return removed


def delete(instance_dir: str, name: str) -> bool:
    """删掉一份指定备份。只认 backups/ 里的单层文件名。"""
    name = os.path.basename(str(name or ""))
    if not name or not name.lower().endswith(".zip"):
        return False
    path = os.path.join(backup_dir(instance_dir), name)
    if not os.path.isfile(path):
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def total_size(instance_dir: str) -> int:
    """备份占用的总字节数。"""
    return sum(entry.size for entry in list_backups(instance_dir))


__all__ = ["BackupEntry", "BACKUP_DIRNAME", "create", "list_backups",
           "prune", "delete", "total_size", "backup_dir"]
