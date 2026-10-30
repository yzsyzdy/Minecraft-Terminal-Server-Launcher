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
from typing import Optional

from .errors import CoreError

COMMENT_PREFIXES = ("#", "!")


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
                out.append(chr(int(hex_part, 16)))
                index += 6
                continue
            except ValueError:
                pass
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
    for char in str(value):
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
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return parse(f.read())
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise CoreError("web.error.no_such_file", path=name) from e


def exists(root: str, name: str = "server.properties") -> bool:
    return os.path.isfile(os.path.join(root, name))


def _key_line_index(lines: list[str], key: str) -> int:
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped or stripped.startswith(COMMENT_PREFIXES):
            continue
        parsed_key, _ = _split_entry(stripped)
        if parsed_key == key:
            return index
    return -1


def update(root: str, changes: dict[str, str],
           name: str = "server.properties") -> dict[str, str]:
    """按需改若干键，保留注释与顺序；返回写完之后的完整键值。"""
    path = os.path.join(root, name)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()
    except FileNotFoundError:
        original = ""
    except OSError as e:
        raise CoreError("web.error.no_such_file", path=name) from e

    lines = original.splitlines()
    appended: list[str] = []
    for key, value in (changes or {}).items():
        key = str(key or "").strip()
        if not key or "\n" in key or "\r" in key:
            continue
        entry = f"{key}={_escape(value)}"
        index = _key_line_index(lines, key)
        if index >= 0:
            lines[index] = entry
        else:
            appended.append(entry)

    if appended:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# 由 MSTL 面板补充")
        lines.extend(appended)

    text = "\n".join(lines).rstrip("\n") + "\n"
    tmp = path + ".mstl-tmp"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise CoreError("web.error.write_failed", msg=str(e)) from e
    return parse(text)


__all__ = ["parse", "load", "update", "exists"]
