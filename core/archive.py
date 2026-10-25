"""
压缩包解压：把实例目录里的一个压缩包就地（或指定目录里）解开。

只认标准库能处理的格式——zip / tar / tar.gz / tgz——而且按**内容**嗅探，
不看后缀：浏览器存下来的文件名常被改过，用户手工改名更是家常便饭。

安全口径和 core/files.py 一致，另外多两道闸：

  * 每个成员解析后的落点必须仍在实例目录内（绝对路径、盘符、.. 一律拒绝），
    符号链接与硬链接直接跳过不建；
  * 成员数量与解压后总字节数都有上限，挡住「42.zip」那类压缩炸弹。

默认**不覆盖**已存在的文件：解压往往是往一个已有实例里补文件，悄悄盖掉
server.properties 之类的东西比解压失败糟糕得多。跳过了多少会如实回报。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from typing import NamedTuple, Optional

from . import files as fileops
from .errors import CoreError

# 压缩炸弹闸门。正常整合包远在这条线以下。
MAX_MEMBERS = 200_000
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024      # 解压后总大小 4 GiB

# 面板上给「解压」按钮用的后缀白名单（真正解压时仍按内容判断）
ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".tar.gz", ".tgz",
                    ".tar.bz2", ".tbz2", ".tar.xz", ".txz")

# 7z 与 RAR 的文件头（RAR 只认出来报错用——解 rar 需要 unrar，不在依赖里）
SEVENZ_SIGNATURE = b"7z\xbc\xaf\x27\x1c"
RAR_SIGNATURE = b"Rar!\x1a\x07"


class ExtractResult(NamedTuple):
    """一次解压的结果。dest 是相对实例目录的落地目录。"""

    dest: str
    extracted: int
    skipped: int
    refused: int
    total_bytes: int


def archive_kind(path: str) -> str:
    """按内容判断压缩包类型，返回 'zip' / '7z' / 'rar' / 'tar' / 'gzip' / ''。

    gzip 与 tar 分开：'.tar.gz' 要先过 gzip 再当 tar 读，而裸 '.gz'
    （单个文件被 gzip 压过）我们不处理。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if head[:4] == b"PK\x03\x04":
                return "zip"
            if head[:6] == SEVENZ_SIGNATURE:
                return "7z"
            if head[:7] == RAR_SIGNATURE:
                return "rar"
            if head[:2] == b"\x1f\x8b":
                return "gzip"
            # tar 的签名在偏移 257 处（POSIX 与 GNU 都是 "ustar"）
            f.seek(257)
            if f.read(5) == b"ustar":
                return "tar"
    except OSError:
        return ""
    return ""


def sevenzip_backend() -> str:
    """能用的 7z 后端：'py7zr' / '7z' / ''。

    py7zr 是可选依赖（装了才能读写 7z，也才支持密码）；没装时退到系统的
    7z / 7za / 7zr 可执行文件。两者都没有就返回空串，由调用方报错而不是
    假装解压成功。
    """
    try:
        import py7zr  # noqa: F401
        return "py7zr"
    except Exception:  # noqa: BLE001 装了但导入失败也算没有
        pass
    for name in ("7z", "7za", "7zr"):
        if shutil.which(name):
            return name
    return ""


def is_archive_name(name: str) -> bool:
    """只是给界面层提示用的后缀判断，不参与真正的解压决策。"""
    return str(name or "").lower().endswith(ARCHIVE_SUFFIXES)


def _member_target(root: str, dest_rel: str, member_name: str) -> str:
    """把成员名拼成落点的绝对路径；越界抛 CoreError。"""
    name = str(member_name or "").replace("\\", "/")
    if not name or name.endswith("/"):
        name = name.rstrip("/")
    if not name:
        raise CoreError("web.error.bad_path", path=member_name)
    if name.startswith("/") or (len(name) >= 2 and name[1] == ":"):
        raise CoreError("web.error.path_outside", path=member_name)
    joined = "/".join(part for part in (dest_rel, name) if part)
    return fileops.resolve(root, joined)


def _zip_is_symlink(info: zipfile.ZipInfo) -> bool:
    # 高 16 位是 Unix 的 st_mode；S_IFLNK == 0o120000
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _zip_is_dir(info: zipfile.ZipInfo) -> bool:
    """这个 zip 成员是不是目录。

    ZipInfo.is_dir() 只看文件名结尾是不是 "/"，而 Windows 的资源管理器打包
    时把目录项写成 "sub\\"。只认 "/" 的话 "sub\\" 会被当作零字节普通文件，
    随后 "sub\\file.txt" 就建不出父目录，整个解压失败。所以两种分隔符都认。
    """
    name = str(info.filename or "")
    return info.is_dir() or name.endswith("\\")


def _dest_rel_for(root: str, src_rel: str, dest_rel: Optional[str]) -> str:
    if dest_rel is None:
        return os.path.dirname(src_rel).replace("\\", "/")
    return fileops.normalize_rel(dest_rel)


def extract(root: str, src_rel: str, dest_rel: Optional[str] = None,
            *, overwrite: bool = False, password: str = "",
            max_members: int = MAX_MEMBERS,
            max_bytes: int = MAX_TOTAL_BYTES) -> ExtractResult:
    """把实例目录里的压缩包解开。失败抛 CoreError。

    dest_rel 为空时解到压缩包自己所在的目录（就地解压）。
    password 只用于加密的 zip / 7z；传空串表示没有密码。
    """
    src = fileops.resolve(root, src_rel)
    if not os.path.isfile(src):
        raise CoreError("web.error.no_such_file", path=src_rel)

    kind = archive_kind(src)
    if not kind:
        raise CoreError("web.error.not_an_archive", name=os.path.basename(src))
    if kind == "rar":
        raise CoreError("web.error.rar_unsupported",
                        name=os.path.basename(src))
    if kind == "gzip":
        # 先确认它其实是 tar.gz：裸 .gz 只有单个成员，不是我们要的东西
        try:
            with tarfile.open(src, "r:gz"):
                pass
        except (tarfile.TarError, OSError) as e:
            raise CoreError("web.error.not_an_archive",
                            name=os.path.basename(src)) from e

    dest = _dest_rel_for(root, src_rel, dest_rel)
    dest_abs = fileops.resolve(root, dest)
    try:
        os.makedirs(dest_abs, exist_ok=True)
    except OSError as e:
        raise CoreError("web.error.mkdir_failed", msg=str(e)) from e

    extracted = skipped = refused = 0
    written = 0

    def _write(target: str, stream, declared: int) -> None:
        """把成员写到 target。非本地变量用可变容器带出来。"""
        nonlocal extracted, skipped, refused, written
        if written + declared > max_bytes:
            raise CoreError("web.error.extract_too_large",
                            max=round(max_bytes / 1024 / 1024 / 1024))
        if os.path.isdir(target):
            skipped += 1
            return
        if os.path.exists(target) and not overwrite:
            skipped += 1
            return
        parent = os.path.dirname(target)
        tmp = ""
        try:
            # 建父目录必须留在 try 里面。放在外面时，FileExistsError 会一路
            # 穿出 _write，被调用方那个 except OSError 接住并翻译成
            # 「压缩包损坏」——可压缩包明明好好的：Windows 做出来的 zip 会把
            # 目录项写成 "sub\"，而 ZipInfo.is_dir() 只认结尾的 "/"，于是它被
            # 当成零字节普通文件建了出来，紧接着 "sub\file.txt" 就发现自己的
            # 父目录是个文件，建不动。放进 try 之后这类失败统一报
            # web.error.write_failed，和本函数其它 OSError 的说法一致。
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            # 临时名必须唯一：以前固定拼 target + ".mstl-extract"，两个解压
            # 任务同时往同一个目标写时，会互相把对方写到一半的文件
            # os.replace 上去。mkstemp 还必须在建好父目录之后——父目录不存在
            # 时它直接 FileNotFoundError。
            fd, tmp = tempfile.mkstemp(prefix=".mstl-", suffix=".mstl-extract",
                                       dir=parent or ".")
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise CoreError(
                            "web.error.extract_too_large",
                            max=round(max_bytes / 1024 / 1024 / 1024))
                    out.write(chunk)
            os.replace(tmp, target)
        except CoreError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise CoreError("web.error.write_failed", msg=str(e)) from e
        extracted += 1

    if kind == "zip":
        pwd = password.encode("utf-8") if password else None
        try:
            with zipfile.ZipFile(src) as zf:
                members = zf.infolist()
                if len(members) > max_members:
                    raise CoreError("web.error.extract_too_many",
                                    max=max_members)
                for info in members:
                    name = info.filename
                    try:
                        target = _member_target(root, dest, name)
                    except CoreError:
                        refused += 1
                        continue
                    if _zip_is_symlink(info):
                        refused += 1
                        continue
                    if _zip_is_dir(info):
                        try:
                            os.makedirs(target, exist_ok=True)
                        except OSError:
                            refused += 1
                        continue
                    try:
                        with zf.open(info, pwd=pwd) as stream:
                            _write(target, stream, info.file_size)
                    except RuntimeError as e:
                        # 加密条目没给密码、或密码不对，zipfile 都抛 RuntimeError
                        text = str(e).lower()
                        if "password" in text or "encrypted" in text:
                            raise CoreError("web.error.archive_password",
                                            name=os.path.basename(src)) from e
                        raise
        except (zipfile.BadZipFile, OSError) as e:
            raise CoreError("web.error.bad_archive",
                            name=os.path.basename(src), msg=str(e)) from e
    elif kind == "7z":
        result = _extract_7z(root, src, dest, dest_abs, password,
                             overwrite=overwrite, max_members=max_members,
                             max_bytes=max_bytes)
        extracted, skipped, refused, written = (
            result.extracted, result.skipped, result.refused, result.total_bytes)
    else:
        try:
            with tarfile.open(src, "r:*") as tf:
                members = tf.getmembers()
                if len(members) > max_members:
                    raise CoreError("web.error.extract_too_many",
                                    max=max_members)
                for member in members:
                    try:
                        target = _member_target(root, dest, member.name)
                    except CoreError:
                        refused += 1
                        continue
                    # 链接一律不建：解压出来的软链指到实例外面就等于绕过了沙箱
                    if member.issym() or member.islnk():
                        refused += 1
                        continue
                    if member.isdir():
                        try:
                            os.makedirs(target, exist_ok=True)
                        except OSError:
                            refused += 1
                        continue
                    if not member.isfile():
                        refused += 1
                        continue
                    stream = tf.extractfile(member)
                    if stream is None:
                        refused += 1
                        continue
                    with stream:
                        _write(target, stream, member.size)
        except (tarfile.TarError, OSError) as e:
            raise CoreError("web.error.bad_archive",
                            name=os.path.basename(src), msg=str(e)) from e

    return ExtractResult(dest=dest, extracted=extracted, skipped=skipped,
                         refused=refused, total_bytes=written)


def _move_tree(root_dest: str, tmp_dir: str, dest_rel: str, *,
               overwrite: bool, max_bytes: int) -> ExtractResult:
    """把 7z 先解到临时目录的树搬进实例目录，沿途做同一套边界检查。"""
    extracted = skipped = refused = 0
    written = 0
    for current, _dirs, names in os.walk(tmp_dir):
        for name in names:
            source = os.path.join(current, name)
            rel = os.path.relpath(source, tmp_dir).replace(os.sep, "/")
            try:
                target = fileops.resolve(root_dest, rel)
            except CoreError:
                refused += 1
                continue
            try:
                size = os.path.getsize(source)
            except OSError:
                refused += 1
                continue
            if written + size > max_bytes:
                raise CoreError("web.error.extract_too_large",
                                max=round(max_bytes / 1024 / 1024 / 1024))
            if os.path.exists(target) and not overwrite:
                skipped += 1
                continue
            parent = os.path.dirname(target)
            try:
                if parent:
                    os.makedirs(parent, exist_ok=True)
                shutil.move(source, target)
            except OSError as e:
                raise CoreError("web.error.write_failed", msg=str(e)) from e
            written += size
            extracted += 1
    return ExtractResult(dest=dest_rel, extracted=extracted, skipped=skipped,
                         refused=refused, total_bytes=written)


def _extract_7z(root: str, src: str, dest_rel: str, dest_abs: str,
                password: str, *, overwrite: bool, max_members: int,
                max_bytes: int) -> ExtractResult:
    """7z：优先 py7zr（支持密码），否则调系统的 7z 命令行。"""
    backend = sevenzip_backend()
    if not backend:
        raise CoreError("web.error.sevenzip_unavailable")

    name = os.path.basename(src)
    tmp_dir = tempfile.mkdtemp(prefix=".mstl-7z-", dir=dest_abs)
    try:
        if backend == "py7zr":
            import py7zr
            try:
                with py7zr.SevenZipFile(src, mode="r",
                                        password=password or None) as zf:
                    names = zf.getnames()
                    if len(names) > max_members:
                        raise CoreError("web.error.extract_too_many",
                                        max=max_members)
                    zf.extractall(path=tmp_dir)
            except CoreError:
                raise
            except py7zr.exceptions.PasswordRequired as e:
                raise CoreError("web.error.archive_password", name=name) from e
            except py7zr.exceptions.Bad7zFile as e:
                raise CoreError("web.error.bad_archive", name=name,
                                msg=str(e)) from e
            except Exception as e:  # noqa: BLE001 py7zr 的密码错误有时是底层异常
                text = str(e).lower()
                # 密码错时 py7zr 抛的是 lzma.LZMAError("Corrupt input data")，
                # 不是 PasswordRequired；只有带过密码才敢判定「密码不对」，
                # 没带密码时仍按坏包处理。
                if "password" in text or (password and "corrupt" in text):
                    raise CoreError("web.error.archive_password",
                                    name=name) from e
                raise CoreError("web.error.bad_archive", name=name,
                                msg=str(e)) from e
        else:
            cmd = [backend, "x", "-y", f"-o{tmp_dir}", src]
            if password:
                cmd.append(f"-p{password}")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=600,
                                      creationflags=flags)
            except (OSError, subprocess.TimeoutExpired) as e:
                raise CoreError("web.error.bad_archive", name=name,
                                msg=str(e)) from e
            if proc.returncode != 0:
                message = (proc.stdout + proc.stderr).decode("utf-8", "replace")
                if "password" in message.lower():
                    raise CoreError("web.error.archive_password",
                                    name=name)
                raise CoreError("web.error.bad_archive", name=name,
                                msg=message.strip()[:200])

        return _move_tree(dest_abs, tmp_dir, dest_rel, overwrite=overwrite,
                          max_bytes=max_bytes)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def create_archive(root: str, src_rels: list[str], dest_rel: str,
                   *, fmt: str = "zip", password: str = "",
                   overwrite: bool = False) -> str:
    """把若干文件/目录打成一个压缩包，返回相对路径。

    fmt 支持 zip / 7z / tar.gz。zip 用标准库（写不了密码）；7z 需要 py7zr
    才能写，并且只有它支持密码。
    """
    if not src_rels:
        raise CoreError("web.error.nothing_to_compress")
    fmt = (fmt or "zip").lower()
    if fmt not in ("zip", "7z", "tar.gz"):
        raise CoreError("web.error.bad_archive_format", name=fmt)

    dest = fileops.normalize_rel(dest_rel)
    if not dest:
        raise CoreError("web.error.bad_path", path=dest_rel)
    target = fileops.resolve(root, dest)
    if os.path.isdir(target):
        raise CoreError("web.error.is_a_directory", path=dest)
    if os.path.exists(target) and not overwrite:
        raise CoreError("web.error.already_exists", path=dest)
    parent = os.path.dirname(target)
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
    except OSError as e:
        raise CoreError("web.error.mkdir_failed", msg=str(e)) from e

    sources = []
    for rel in src_rels:
        src = fileops.resolve(root, fileops.normalize_rel(rel))
        if not os.path.exists(src):
            raise CoreError("web.error.no_such_file", path=rel)
        sources.append((os.path.basename(src.rstrip("\\/")) or "item", src))

    try:
        if fmt == "zip":
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
                for arc_base, src in sources:
                    if os.path.isdir(src):
                        for current, _dirs, names in os.walk(src):
                            for name in names:
                                full = os.path.join(current, name)
                                inner = os.path.relpath(full, src).replace(os.sep, "/")
                                zf.write(full, f"{arc_base}/{inner}")
                    else:
                        zf.write(src, arc_base)
        elif fmt == "tar.gz":
            with tarfile.open(target, "w:gz") as tf:
                for arc_base, src in sources:
                    tf.add(src, arcname=arc_base)
        else:
            backend = sevenzip_backend()
            if backend != "py7zr":
                raise CoreError("web.error.sevenzip_write_unavailable")
            import py7zr
            with py7zr.SevenZipFile(target, "w",
                                    password=password or None) as zf:
                for arc_base, src in sources:
                    if os.path.isdir(src):
                        # py7zr 的 write() 对目录只记一条目录项，不递归；
                        # 这里自己走一遍，和 zip 分支保持同样的结构。
                        for current, _dirs, names in os.walk(src):
                            for name in names:
                                full = os.path.join(current, name)
                                inner = os.path.relpath(full, src).replace(os.sep, "/")
                                zf.write(full, f"{arc_base}/{inner}")
                    else:
                        zf.write(src, arc_base)
    except CoreError:
        try:
            os.remove(target)
        except OSError:
            pass
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, tarfile.TarError) as e:
        try:
            os.remove(target)
        except OSError:
            pass
        raise CoreError("web.error.compress_failed", msg=str(e)) from e
    return dest


__all__ = [
    "ARCHIVE_SUFFIXES",
    "MAX_MEMBERS",
    "MAX_TOTAL_BYTES",
    "ExtractResult",
    "archive_kind",
    "is_archive_name",
    "sevenzip_backend",
    "extract",
    "create_archive",
]
