r"""`server.properties` 的读写。

Minecraft 用的是 Java 的 `Properties` 格式，和 INI 像但有几处必须照做：

  * `#` 与 `!` 开头是注释；
  * 分隔符可以是 `=`、`:`、或第一个空白；
  * 行尾一个反斜杠表示续行；
  * 值里 `\\` `\:` `\=` `\ ` 是转义，`\uXXXX` 是 Unicode 转义——**Java 按
    ISO-8859-1 读这个文件**，所以中文（比如 motd）只能写成 `\\uXXXX`，直接写
    UTF-8 汉字在游戏里会变成乱码。写回时这里会做对应的转义。

回写只改有变化的行，注释、顺序、其它键都原样保留；缺失的键追加到文件末尾。
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional

from .errors import CoreError

COMMENT_PREFIXES = ("#", "!")

# Java properties 的键：不能有空白、`=`、`:`，也不能以 `#`/`!` 开头
_VALID_KEY = re.compile(r"[^\s=:#!][^\s=:]*$")


def _unescape(text: str) -> str:
    """处理 Java properties 的转义。"""
    out = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char != "\\" or index + 1 >= length:
            out.append(char)
            index += 1
            continue
        nxt = text[index + 1]
        if nxt == "u" and index + 5 < length:
            hex_part = text[index + 2:index + 6]
            try:
                code = int(hex_part, 16)
            except ValueError:
                code = -1
            if code >= 0:
                index += 6
                # 增补平面（emoji 等）写成一对代理项，必须拼回一个码位，
                # 否则读出来是两个孤立的代理字符，写回 UTF-8 时会炸
                if 0xD800 <= code <= 0xDBFF and text[index:index + 2] == "\\u":
                    low_hex = text[index + 2:index + 6]
                    try:
                        low = int(low_hex, 16)
                    except ValueError:
                        low = -1
                    if 0xDC00 <= low <= 0xDFFF:
                        out.append(chr(0x10000 + (code - 0xD800) * 0x400
                                       + (low - 0xDC00)))
                        index += 6
                        continue
                out.append(chr(code))
                continue
        simple = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}
        out.append(simple.get(nxt, nxt))
        index += 2
    return "".join(out)


def _escape(value: str) -> str:
    """把值写成 Java properties 能原样读回来的形式。

    非 ASCII 一律转 `\\uXXXX`：这个文件在游戏侧是按 ISO-8859-1 解码的，
    直接写 UTF-8 只会得到一串问号或 mojibake。
    """
    out = []
    for position, char in enumerate(str(value)):
        if position == 0 and char == " ":
            # Java 会吃掉值前面的空白，前导空格必须写成 `\ `
            out.append("\\ ")
            continue
        code = ord(char)
        if char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        elif code > 0x7e:
            encoded = char.encode("utf-16-be", "surrogatepass")
            for i in range(0, len(encoded), 2):
                out.append(f"\\u{int.from_bytes(encoded[i:i+2], 'big'):04x}")
        else:
            out.append(char)
    return "".join(out)


def _logical_lines(text: str) -> list[str]:
    """按 Java 的规则把续行拼起来；注释与空行保留为单独一项。"""
    lines: list[str] = []
    pending: Optional[str] = None
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if pending is None and (not stripped or stripped.startswith(COMMENT_PREFIXES)):
            lines.append(raw)
            continue
        current = stripped if pending is None else pending + stripped
        if current.endswith("\\") and not current.endswith("\\\\"):
            pending = current[:-1]
            continue
        pending = None
        lines.append(current)
    if pending is not None:
        lines.append(pending)
    return lines


def _split_entry(line: str) -> tuple[str, str]:
    """把 `key=value` / `key: value` / `key value` 拆开。"""
    key_chars = []
    index = 0
    length = len(line)
    while index < length:
        char = line[index]
        if char in "=: \t\f":
            break
        if char == "\\" and index + 1 < length:
            key_chars.append(line[index:index + 2])
            index += 2
            continue
        key_chars.append(char)
        index += 1
    key = _unescape("".join(key_chars))
    while index < length and line[index] in " \t\f":
        index += 1
    if index < length and line[index] in "=:":
        index += 1
    while index < length and line[index] in " \t\f":
        index += 1
    return key, _unescape(line[index:])


def parse(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in _logical_lines(text or ""):
        stripped = line.strip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            continue
        key, value = _split_entry(stripped)
        if key:
            result[key] = value
    return result


def load(root: str, name: str = "server.properties") -> dict[str, str]:
    path = os.path.join(root, name)
    try:
        with open(path, "rb") as f:
            return parse(f.read().decode("latin-1"))
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise CoreError("web.error.no_such_file", path=name) from e


def exists(root: str, name: str = "server.properties") -> bool:
    return os.path.isfile(os.path.join(root, name))


def _key_span(lines: list[str], key: str) -> tuple[int, int]:
    """返回这个键**最后一次出现**所覆盖的物理行区间 [start, end)。

    Java 允许用行尾反斜杠续行，一个键可能横跨多行；按行号直接替换会把
    续行残留成一段坏数据，所以这里要整段换掉。
    """
    start, end = -1, -1
    index = 0
    while index < len(lines):
        stripped = lines[index].lstrip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            index += 1
            continue
        last = index
        while last + 1 < len(lines) and lines[last].endswith("\\") \
                and not lines[last].endswith("\\\\"):
            last += 1
        parsed_key, _ = _split_entry(stripped)
        if parsed_key == key:
            start, end = index, last + 1
        index = last + 1
    return start, end


def update(root: str, changes: dict[str, str],
           name: str = "server.properties") -> dict[str, str]:
    """按需改若干键，保留注释与顺序；返回写完之后的完整键值。"""
    path = os.path.join(root, name)
    try:
        # 这个文件在游戏侧按 ISO-8859-1 读写；用 latin-1 解码再编码能保证
        # 原样保留那些非 ASCII 的旧字节，不会被替换成 U+FFFD 永久破坏。
        with open(path, "rb") as f:
            original = f.read().decode("latin-1")
    except FileNotFoundError:
        original = ""
    except OSError as e:
        raise CoreError("web.error.no_such_file", path=name) from e

    if not changes:
        # 没有改动就不要碰文件：文件不存在时也不该凭空造一个空文件
        return parse(original)

    lines = original.splitlines()
    appended: list[str] = []
    for key, value in (changes or {}).items():
        key = str(key or "").strip()
        if not _VALID_KEY.match(key):
            continue
        entry = f"{key}={_escape(value)}"
        start, end = _key_span(lines, key)
        if start >= 0:
            lines[start:end] = [entry]
        else:
            appended.append(entry)

    if appended:
        if lines and lines[-1].strip():
            lines.append("")
        # 注释必须是纯 ASCII：文件按 latin-1 写回，中文会变成问号
        lines.append("# added by the MSTL panel")
        lines.extend(appended)

    text = "\n".join(lines).rstrip("\n") + "\n"
    # 唯一临时文件名：并发保存不会互相删对方的半成品
    tmp = f"{path}.mstl-tmp-{os.getpid()}-{time.time_ns()}"
    try:
        with open(tmp, "wb") as f:
            f.write(text.encode("latin-1", "replace"))
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise CoreError("web.error.write_failed", msg=str(e)) from e
    return parse(text)


__all__ = ["parse", "load", "update", "exists"]
