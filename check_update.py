"""
版本更新检查模块

在 Nuitka 打包后的目录中查找 version.json，读取当前版本号，
然后跟 GitHub Releases 的最新版本比较。如果不是最新，提示用户下载。

version.json 由 CI 构建流程自动生成，格式:
  {"version": "1.0.0", "tag_name": "v1.0.0"}
"""

import json
import os
import sys
import urllib.request
import urllib.error

from constants import REPO_API, USER_AGENT

VERSION_FILENAME = "version.json"


def _bundled_dir() -> str:
    """定位打包时嵌入的资源目录。"""
    from paths import bundled_dir
    return bundled_dir()


def _read_local_version() -> str:
    """读取 version.json，返回版本号字符串。不存在返回空字符串。"""
    path = os.path.join(_bundled_dir(), VERSION_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("version") or "").strip()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""


def _parse_version(v: str) -> tuple[int, ...]:
    """把 "v1.2.3" 这类版本号转成等长的数字元组用于比较。

    补到固定长度是必须的：不然 (1, 0) < (1, 0, 0)，"1.0" 会被判成比
    "1.0.0" 旧，每次检查都提示有新版本。带后缀的段（"3-beta"）取数字前缀，
    否则整段会被当成 0，"1.2.3-beta" 反而显得比 "1.2.3" 旧。
    """
    nums: list[int] = []
    for part in v.strip().lstrip("vV").split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def check_for_update() -> tuple[bool, str, str]:
    """检查更新。

    三种可能结果:
      (False, "", "")          — 无法检查（无本地版本号/网络不通）
      (False, "x.y.z", "")     — 已是最新版
      (True,  "cur", "latest") — 有新版本: 当前 cur，最新 latest
    """
    current = _read_local_version()
    if not current:
        return False, "", ""

    try:
        req = urllib.request.Request(
            REPO_API,
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            OSError, json.JSONDecodeError):
        return False, current, ""

    latest = (data.get("tag_name") or "").strip()
    if not latest:
        return False, current, ""

    if _parse_version(latest) > _parse_version(current):
        return True, current, latest

    return False, current, latest
