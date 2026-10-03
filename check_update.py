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


def _candidate_paths() -> list[str]:
    """version.json 可能在哪几个位置。

    1. bundled_dir()：standalone 下就是 dist 目录，CI 就是把文件写在这
    2. exe 旁边：onefile 的 bundled_dir() 是 %TEMP% 里的解压目录，而
       version.json 并没有被 --include-data-dir 打进去（只有 locales/
       与 webui/），它一直躺在用户那个 exe 旁边
    """
    paths = [os.path.join(_bundled_dir(), VERSION_FILENAME)]
    try:
        from paths import exe_dir
        exe_d = exe_dir()
    except Exception:  # noqa: BLE001
        exe_d = ""
    if exe_d:
        paths.append(os.path.join(exe_d, VERSION_FILENAME))
    return paths


def _read_local_version() -> str:
    """读取 version.json，返回版本号字符串。都找不到返回空字符串。"""
    for path in _candidate_paths():
        try:
            # utf-8-sig 而不是 utf-8：CI 用的 Windows PowerShell 5.1 里
            # `Set-Content -Encoding UTF8` 写出来的文件**带 BOM**，用 utf-8
            # 打开会得到 U+FEFF 开头，json.load 直接抛 JSONDecodeError——
            # 原来这一抛就被下面的 except 吞掉，于是每个正式发布版的更新
            # 检查都是哑的：永远显示「已是最新版」，没有任何提示。
            # utf-8-sig 对带 BOM 和不带 BOM 都能读。
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        version = (data.get("version") or "").strip()
        if version:
            return version
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
