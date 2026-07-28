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


def _read_local_version() -> str:
    """读取本地 version.json，返回版本号字符串。不存在返回空字符串。"""
    # frozen 模式下 version.json 在 exe 同级目录
    base = os.path.dirname(
        os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    path = os.path.join(base, VERSION_FILENAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (data.get("version") or "").strip()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""


def _parse_version(v: str) -> tuple[int, ...]:
    """将 "1.2.3" 转为 (1, 2, 3) 用于比较。"""
    try:
        parts = v.replace("v", "").replace("V", "").split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts)
    except (ValueError, AttributeError):
        return (0,)


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
