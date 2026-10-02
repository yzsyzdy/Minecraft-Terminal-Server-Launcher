"""
实例内的文件读写。

网页面板的文件管理器需要「列目录 / 读文本 / 写文本 / 新建 / 改名 / 删除」
这六件事。它们是纯数据操作，不 print 也不提问，所以放在 core 里；HTTP 与
渲染交给界面层。

安全边界：所有路径都必须落在调用方给定的根目录（也就是某个实例目录）之内。
请求里带来的是相对路径，这里做三件事——

  1. 拒绝盘符、绝对路径和任何 .. 片段；
  2. 用 realpath 解开符号链接后再确认结果仍在根目录内，挡住「链到外面的
     软链接」这种绕法；
  3. 删除与改名另外拒绝根目录本身。

一句话：路径检查不通过就抛 CoreError，绝不去碰文件系统。
"""

from __future__ import annotations

import os
import shutil
from typing import NamedTuple

from .errors import CoreError

# 单个文件可编辑的上限。再大就不往浏览器里塞了，让用户走下载。
MAX_EDIT_BYTES = 2 * 1024 * 1024

# 一次列目录最多返回多少条，避免超大树把浏览器拖死
MAX_LIST_ENTRIES = 2000

# 这些后缀当作可编辑文本
TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".json5", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".conf", ".properties", ".log", ".sh", ".bat", ".cmd", ".ps1", ".csv",
    ".html", ".css", ".js", ".ts", ".xml", ".env", ".list", ".snbt", ".mcmeta",
    ".lang", ".sql", ".py", ".java", ".gradle", ".gitignore", ".editorconfig",
}

# 这些文件名（不分大小写）当作可编辑文本，即使没有后缀
TEXT_NAMES = {
    "eula.txt", "server.properties", "banned-ips.json", "banned-players.json",
    "ops.json", "whitelist.json", "usercache.json", "permissions.yml",
    "bukkit.yml", "spigot.yml", "paper.yml", "purpur.yml", "pufferfish.yml",
    ".server.json", "config.yml", "settings.yml", "README", "LICENSE",
}


class FileEntry(NamedTuple):
    """目录里的一项。path 是相对根目录的 posix 风格路径。"""

    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float
    editable: bool


def is_probably_text(name: str) -> bool:
    """按文件名猜它是不是可编辑文本。只是给界面层的提示，不是硬判断。"""
    low = name.lower()
    if low in TEXT_NAMES:
        return True
    ext = os.path.splitext(low)[1]
    return ext in TEXT_EXTENSIONS


def normalize_rel(rel: str) -> str:
    """把请求里的相对路径收敛成 'a/b/c' 形式，非法就抛 CoreError。

    开头的 '/' 会被当作「从根目录算起」直接去掉，不留下来：留着的话
    os.path.join 会把它当成绝对路径，整个根目录就被丢掉了。
    """
    raw = (rel or "").replace("\\", "/").strip()
    # 盘符（C:...）与 UNC 一律不接受：os.path.join 遇到它们会直接丢掉根目录
    if len(raw) >= 2 and raw[1] == ":":
        raise CoreError("web.error.bad_path", path=rel)
    if raw.startswith("//"):
        raise CoreError("web.error.bad_path", path=rel)

    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise CoreError("web.error.bad_path", path=rel)
        parts.append(part)
    return "/".join(parts)


def resolve(root: str, rel: str) -> str:
    """把相对路径解析成根目录内的绝对路径；越界就抛 CoreError。"""
    root_abs = os.path.realpath(root)
    clean = normalize_rel(rel)
    candidate = os.path.realpath(os.path.join(root_abs, clean))
    if candidate != root_abs and not candidate.startswith(root_abs + os.sep):
        # 走到这里说明路径里有软链接指向根目录之外
        raise CoreError("web.error.path_outside", path=rel)
    return candidate


def relative(root: str, absolute: str) -> str:
    """把绝对路径转回相对根目录的 posix 路径。"""
    rel = os.path.relpath(absolute, os.path.realpath(root))
    return "" if rel == "." else rel.replace("\\", "/")


def list_dir(root: str, rel: str = "") -> list[FileEntry]:
    """列出某个目录。目录不存在抛 CoreError。"""
    target = resolve(root, rel)
    if not os.path.isdir(target):
        raise CoreError("web.error.no_such_dir", path=rel or "/")

    try:
        names = os.listdir(target)
    except OSError as e:
        raise CoreError("web.error.list_failed", msg=str(e)) from e

    entries: list[FileEntry] = []
    for name in names:
        full = os.path.join(target, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue  # 读不到的条目直接跳过，不要让整个列表失败
        is_dir = os.path.isdir(full)
        entries.append(FileEntry(
            name=name,
            path=relative(root, full),
            is_dir=is_dir,
            size=0 if is_dir else stat.st_size,
            mtime=stat.st_mtime,
            editable=not is_dir and is_probably_text(name),
        ))

    # 目录在前，然后按名字排；大小写不敏感，免得大小写混着排看起来很乱
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries[:MAX_LIST_ENTRIES]


def _require_file(root: str, rel: str) -> str:
    target = resolve(root, rel)
    if not os.path.isfile(target):
        raise CoreError("web.error.no_such_file", path=rel or "/")
    return target


def _require_entry(root: str, rel: str) -> str:
    if not normalize_rel(rel):
        raise CoreError("web.error.root_locked")
    target = resolve(root, rel)
    if not os.path.exists(target):
        raise CoreError("web.error.no_such_file", path=rel)
    return target


def read_text(root: str, rel: str, max_bytes: int = MAX_EDIT_BYTES) -> str:
    """读一个文本文件。太大或不是 UTF-8 文本都抛 CoreError。"""
    target = _require_file(root, rel)
    try:
        size = os.path.getsize(target)
        if size > max_bytes:
            raise CoreError("web.error.file_too_large",
                            size=round(size / 1024 / 1024, 1),
                            max=round(max_bytes / 1024 / 1024, 1))
        with open(target, "rb") as f:
            data = f.read(max_bytes + 1)
    except CoreError:
        raise
    except OSError as e:
        raise CoreError("web.error.read_failed", msg=str(e)) from e

    if len(data) > max_bytes:
        raise CoreError("web.error.file_too_large",
                        size=round(len(data) / 1024 / 1024, 1),
                        max=round(max_bytes / 1024 / 1024, 1))
    # NUL 字节是二进制文件最可靠的信号，而且 UTF-8 解码不会因为它报错：
    # 只靠 UnicodeDecodeError 判断的话，blob.bin 这种文件会被当成文本塞给编辑器
    if b"\x00" in data:
        raise CoreError("web.error.not_text_file")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CoreError("web.error.not_text_file") from e


def write_text(root: str, rel: str, content: str,
               max_bytes: int = MAX_EDIT_BYTES) -> int:
    """写一个文本文件，返回写入的字节数。

    先写同目录下的临时文件再 os.replace：中途出错也不会把原来的配置截成半截。
    """
    clean = normalize_rel(rel)
    if not clean:
        raise CoreError("web.error.bad_path", path=rel)

    data = content.encode("utf-8")
    if len(data) > max_bytes:
        raise CoreError("web.error.content_too_large",
                        max=round(max_bytes / 1024 / 1024, 1))

    target = resolve(root, clean)
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        raise CoreError("web.error.no_such_dir", path=relative(root, parent))
    if os.path.isdir(target):
        raise CoreError("web.error.is_a_directory", path=clean)

    tmp = target + ".mstl-tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise CoreError("web.error.write_failed", msg=str(e)) from e
    return len(data)


def make_directory(root: str, rel: str) -> str:
    """新建目录（含中间层级）。已存在则抛 CoreError。"""
    clean = normalize_rel(rel)
    if not clean:
        raise CoreError("web.error.bad_path", path=rel)
    target = resolve(root, clean)
    if os.path.exists(target):
        raise CoreError("web.error.already_exists", path=clean)
    try:
        os.makedirs(target)
    except OSError as e:
        raise CoreError("web.error.mkdir_failed", msg=str(e)) from e
    return clean


def rename_entry(root: str, rel: str, new_name: str) -> str:
    """改名（不移动位置）。返回新的相对路径。"""
    target = _require_entry(root, rel)
    clean_name = (new_name or "").strip()
    if (not clean_name or clean_name in (".", "..")
            or "/" in clean_name or "\\" in clean_name
            or (len(clean_name) >= 2 and clean_name[1] == ":")):
        raise CoreError("web.error.bad_name", name=new_name)

    parent = os.path.dirname(target)
    new_target = os.path.join(parent, clean_name)
    if os.path.exists(new_target):
        raise CoreError("web.error.already_exists", path=clean_name)
    try:
        os.rename(target, new_target)
    except OSError as e:
        raise CoreError("web.error.rename_failed", msg=str(e)) from e
    return relative(root, new_target)


def delete_entry(root: str, rel: str) -> None:
    """删除文件或目录（目录递归删）。拒绝删除根目录自身。"""
    target = _require_entry(root, rel)
    try:
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
    except OSError as e:
        raise CoreError("web.error.delete_failed", msg=str(e)) from e


def save_upload(root: str, rel: str, stream, content_length: int,
                max_bytes: int, create_dirs: bool = False) -> int:
    """把上传流写入目标路径，返回字节数。

    必须按 Content-Length 精确读取，不能读到 EOF 为止：这是 HTTP 长连接上
    的请求体，读完 body 之后 socket 并不会关闭，多读一次就会永远阻塞在
    下一个字节上。边读边写，不把整个文件读进内存——上传的可能是几十 MB 的
    服务端 jar。

    create_dirs=True 时把缺失的父目录一并建出来。上传整个文件夹要靠它：
    浏览器给出的顺序不保证，world/level.dat 完全可能先于 world/ 到达。
    """
    clean = normalize_rel(rel)
    if not clean:
        raise CoreError("web.error.bad_path", path=rel)
    if content_length <= 0:
        raise CoreError("web.error.upload_empty")
    if content_length > max_bytes:
        raise CoreError("web.error.upload_too_large",
                        max=round(max_bytes / 1024 / 1024))

    target = resolve(root, clean)
    parent = os.path.dirname(target)
    if not os.path.isdir(parent):
        if not create_dirs:
            raise CoreError("web.error.no_such_dir", path=relative(root, parent))
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            raise CoreError("web.error.mkdir_failed", msg=str(e)) from e
    if os.path.isdir(target):
        raise CoreError("web.error.is_a_directory", path=clean)

    written = 0
    remaining = content_length
    tmp = target + ".mstl-upload"
    try:
        with open(tmp, "wb") as f:
            while remaining > 0:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                written += len(chunk)
                f.write(chunk)
        if remaining != 0:
            raise CoreError("web.error.upload_incomplete")
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
    return written


__all__ = [
    "MAX_EDIT_BYTES",
    "MAX_LIST_ENTRIES",
    "FileEntry",
    "is_probably_text",
    "normalize_rel",
    "resolve",
    "relative",
    "list_dir",
    "read_text",
    "write_text",
    "make_directory",
    "rename_entry",
    "delete_entry",
    "save_upload",
]
