"""玩家名单：白名单、OP、封禁。

这三个文件都是 Minecraft 自己写的 JSON，格式固定但有几个坑：

  * `whitelist.json` / `ops.json` / `banned-players.json` **在服务器运行时会
    被它自己重写**——直接改文件，内存里的状态不会跟着变，服务器下次写盘时
    可能把我们写的内容覆盖回去。所以改完之后必须再往控制台发一次对应命令
    （`whitelist add X` / `op X` / `ban X`），由服务器自己去改。
  * 文件不存在是正常状态（服务器还没写过），当成空名单处理。
  * `ops.json` 的 `level` 是权限等级 1–4，`bypassesPlayerLimit` 决定 OP 满员时
    能不能进服。删 OP 时这两个字段跟着一起没。
  * `banned-players.json` 的 `expires` 是 `"forever"` 或 ISO 时间串。

本模块只做"读出来给人看"和"写文件"两件事；"让服务器也认"由调用方
（面板后端）在写完后再发一次命令完成。
"""

from __future__ import annotations

import json
import os
import time
from typing import NamedTuple, Optional

from .errors import CoreError

WHITELIST_FILE = "whitelist.json"
OPS_FILE = "ops.json"
BANNED_FILE = "banned-players.json"
# 旧版本（Beta 1.7.3 及更早）用 banned-ips.txt / white-list.txt 这类文本格式。
# 现代服务端不用，这里只在找不到 JSON 时顺带认一下，避免显示成"空名单"。
LEGACY_WHITELIST = "white-list.txt"
LEGACY_OPS = "ops.txt"
LEGACY_BANNED = "banned-players.txt"

MAX_ENTRIES = 10_000


class PlayerEntry(NamedTuple):
    """一个玩家。不同名单用到的字段不同，用不到的留空。"""

    name: str
    uuid: str = ""
    level: int = 0
    bypasses: bool = False
    reason: str = ""
    expires: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        data = {"name": self.name, "uuid": self.uuid}
        if self.level:
            data["level"] = self.level
            data["bypasses"] = self.bypasses
        if self.reason:
            data["reason"] = self.reason
        if self.expires:
            data["expires"] = self.expires
        if self.source:
            data["source"] = self.source
        return data


def _load_list(path: str) -> list:
    """读一个 JSON 名单。文件不存在 / 内容坏了都当空名单。"""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _load_text_list(path: str) -> list[str]:
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return [line.strip() for line in f
                    if line.strip() and not line.strip().startswith("#")]
    except OSError:
        return []


def _write_list(path: str, data: list) -> None:
    """原子写：先写临时文件再改名，避免服务器正好在读时读到半个 JSON。"""
    tmp = "%s.mstl-tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise CoreError("web.error.write_failed", msg=str(e)) from e


def read_whitelist(instance_dir: str) -> list[PlayerEntry]:
    entries = []
    for item in _load_list(os.path.join(instance_dir, WHITELIST_FILE))[:MAX_ENTRIES]:
        if isinstance(item, dict) and item.get("name"):
            entries.append(PlayerEntry(name=str(item["name"]),
                                       uuid=str(item.get("uuid") or "")))
    if not entries:
        # 退回旧版文本格式
        for name in _load_text_list(os.path.join(instance_dir, LEGACY_WHITELIST)):
            entries.append(PlayerEntry(name=name))
    return entries


def read_ops(instance_dir: str) -> list[PlayerEntry]:
    entries = []
    for item in _load_list(os.path.join(instance_dir, OPS_FILE))[:MAX_ENTRIES]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        try:
            level = int(item.get("level") or 0)
        except (TypeError, ValueError):
            level = 0
        entries.append(PlayerEntry(
            name=str(item["name"]),
            uuid=str(item.get("uuid") or ""),
            level=max(0, min(4, level)),
            bypasses=bool(item.get("bypassesPlayerLimit")),
        ))
    if not entries:
        for name in _load_text_list(os.path.join(instance_dir, LEGACY_OPS)):
            entries.append(PlayerEntry(name=name, level=4))
    return entries


def read_banned(instance_dir: str) -> list[PlayerEntry]:
    entries = []
    for item in _load_list(os.path.join(instance_dir, BANNED_FILE))[:MAX_ENTRIES]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        expires = item.get("expires") or ""
        # 已经过期的封禁对玩家不再生效，标出来，面板上可以提示"可清理"
        if expires and expires != "forever":
            try:
                stamp = time.mktime(time.strptime(expires[:19],
                                                  "%Y-%m-%d %H:%M:%S"))
                if stamp < time.time():
                    expires = "expired"
            except (ValueError, OverflowError):
                pass
        entries.append(PlayerEntry(
            name=str(item["name"]),
            uuid=str(item.get("uuid") or ""),
            reason=str(item.get("reason") or ""),
            expires=str(expires),
            source=str(item.get("source") or ""),
        ))
    if not entries:
        for name in _load_text_list(os.path.join(instance_dir, LEGACY_BANNED)):
            entries.append(PlayerEntry(name=name))
    return entries


def _norm_name(name: str) -> str:
    """玩家名：只允许字母数字下划线，长度 1-16（Minecraft 的规则）。

    这不是为了"严格符合规范"，是因为这个值会被拼进发往控制台的命令里，
    带空格或换行的名字可能被解释成两条命令。

    只认 ASCII：`str.isalnum()` 对中文和重音字母都返回 True，
    用它放行等于允许「玩家」这种名字进控制台命令。Minecraft 正版 ID
    本来就只能是 `[A-Za-z0-9_]`，收紧到 ASCII 才是对的。
    """
    name = str(name or "").strip()
    if not name or len(name) > 16:
        raise CoreError("web.error.bad_player_name")
    for ch in name:
        if not (("a" <= ch <= "z") or ("A" <= ch <= "Z")
                or ("0" <= ch <= "9") or ch == "_"):
            raise CoreError("web.error.bad_player_name")
    return name


def _drop(path: str, name: str) -> bool:
    data = _load_list(path)
    kept = [item for item in data
            if not (isinstance(item, dict)
                    and str(item.get("name") or "").lower() == name.lower())]
    if len(kept) == len(data):
        return False
    _write_list(path, kept)
    return True


def remove_whitelist(instance_dir: str, name: str) -> bool:
    return _drop(os.path.join(instance_dir, WHITELIST_FILE),
                 _norm_name(name))


def remove_op(instance_dir: str, name: str) -> bool:
    return _drop(os.path.join(instance_dir, OPS_FILE), _norm_name(name))


def remove_ban(instance_dir: str, name: str) -> bool:
    return _drop(os.path.join(instance_dir, BANNED_FILE), _norm_name(name))


def player_commands(kind: str, name: str, action: str,
                    level: int = 4, reason: str = "") -> list[str]:
    """返回"让运行中的服务器同步这个改动"需要的控制台命令。

    为什么还要发命令：服务器内存里有一份名单，直接改文件它不认。
    发命令让它自己改，它写盘时会和我们改的合并。

    action = "add" / "remove"。
    """
    name = _norm_name(name)
    kind = (kind or "").lower()
    if kind == "whitelist":
        return ["whitelist add %s" % name] if action == "add" \
            else ["whitelist remove %s" % name]
    if kind == "op":
        if action == "add":
            level = max(1, min(4, int(level or 4)))
            return ["op %s" % name] if level == 4 else ["op %s" % name]
        return ["deop %s" % name]
    if kind == "ban":
        if action == "add":
            safe = str(reason or "").replace("\n", " ").replace("\r", " ")[:120]
            return ["ban %s %s" % (name, safe) if safe else "ban %s" % name]
        return ["pardon %s" % name]
    raise CoreError("web.error.bad_player_list", name=kind)


def summary(instance_dir: str) -> dict:
    """三个名单一起读，给面板一次拿全。"""
    return {
        "whitelist": [item.to_dict() for item in read_whitelist(instance_dir)],
        "ops": [item.to_dict() for item in read_ops(instance_dir)],
        "banned": [item.to_dict() for item in read_banned(instance_dir)],
    }


__all__ = ["PlayerEntry", "read_whitelist", "read_ops", "read_banned",
           "remove_whitelist", "remove_op", "remove_ban", "player_commands",
           "summary", "WHITELIST_FILE", "OPS_FILE", "BANNED_FILE"]
