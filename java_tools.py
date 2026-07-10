"""
Java 自动检测、版本兼容与 JDK 下载模块
"""

import subprocess
import sys
import os
import json
import glob
import zipfile
import tarfile
import shutil
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, Any

from download_msl import _download_with_progress


# =============================================================================
# Java 检测
# =============================================================================

@dataclass
class JavaInfo:
    """描述一个检测到的 Java 安装。"""
    path: str
    version: str
    detected_at: str

    @staticmethod
    def from_dict(d: dict) -> "JavaInfo":
        return JavaInfo(
            path=d["path"],
            version=d["version"],
            detected_at=d.get("detected_at", ""),
        )


def _run_java_version(java_exe: str) -> Optional[str]:
    try:
        result = subprocess.run(
            [java_exe, "-version"],
            capture_output=True, text=True, timeout=15,
        )
        output = result.stderr or result.stdout
        for line in output.splitlines():
            line = line.strip()
            if '"' in line and "version" in line:
                start = line.index('"') + 1
                end = line.index('"', start)
                return line[start:end]
        return None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def _search_java_from_env() -> list[JavaInfo]:
    found: list[JavaInfo] = []
    seen = set()
    now = datetime.now(timezone.utc).isoformat()
    candidates = []
    for var in ("JAVA_HOME", "JDK_HOME", "JRE_HOME"):
        val = os.environ.get(var, "").strip()
        if val:
            candidates.append(os.path.join(val, "bin", "java.exe"))
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        d = d.strip()
        if not d:
            continue
        c = os.path.join(d, "java.exe")
        if os.path.isfile(c):
            candidates.append(c)
    for cand in candidates:
        norm = os.path.normpath(cand)
        if norm in seen or not os.path.isfile(norm):
            continue
        ver = _run_java_version(norm)
        if ver:
            seen.add(norm)
            found.append(JavaInfo(path=norm, version=ver, detected_at=now))
    return found


def _search_java_from_default_paths() -> list[JavaInfo]:
    found: list[JavaInfo] = []
    seen = set()
    now = datetime.now(timezone.utc).isoformat()
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Common"),
        os.path.join(os.environ.get("USERPROFILE", ""), ".jdks"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "JetBrains"),
    ]
    patterns = [
        "Java/jdk-*/bin/java.exe", "Java/jdk*/bin/java.exe", "Java/jre*/bin/java.exe",
        "Eclipse Adoptium/jdk-*/bin/java.exe", "Eclipse Adoptium/jre-*/bin/java.exe",
        "Microsoft/jdk-*/bin/java.exe", "AdoptOpenJDK/jdk-*/bin/java.exe",
        "Amazon Corretto/jdk*/bin/java.exe", "Amazon Corretto/jre*/bin/java.exe",
        "Zulu/zulu*/bin/java.exe", "Liberica JDK/jdk-*/bin/java.exe",
        "Common/Oracle/Java/javapath/java.exe",
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for pat in patterns:
            for exe in glob.glob(os.path.join(root, pat)):
                norm = os.path.normpath(exe)
                if norm in seen:
                    continue
                ver = _run_java_version(norm)
                if ver:
                    seen.add(norm)
                    found.append(JavaInfo(path=norm, version=ver, detected_at=now))
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for exe in glob.glob(os.path.join(root, "*/jbr/bin/java.exe")):
            norm = os.path.normpath(exe)
            if norm in seen:
                continue
            ver = _run_java_version(norm)
            if ver:
                seen.add(norm)
                found.append(JavaInfo(path=norm, version=ver, detected_at=now))
    return found


def detect_java_versions() -> list[JavaInfo]:
    found = _search_java_from_env() + _search_java_from_default_paths()
    seen = set()
    merged: list[JavaInfo] = []
    for item in found:
        norm = os.path.normpath(item.path)
        if norm not in seen:
            seen.add(norm)
            merged.append(item)
    merged.sort(key=lambda j: _version_sort_key(j.version), reverse=True)
    return merged


def _version_sort_key(version: str) -> tuple:
    parts = []
    for seg in version.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(seg)
    return tuple(parts)


# ---------------------------------------------------------------------------
# Java 列表持久化
# ---------------------------------------------------------------------------

def _java_list_path(storage_dir: str) -> str:
    return os.path.join(storage_dir, "java_list.json")


def load_java_list(storage_dir: str) -> list[JavaInfo]:
    path = _java_list_path(storage_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("known_javas", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        return [JavaInfo.from_dict(j) for j in raw if isinstance(j, dict)]
    except (json.JSONDecodeError, KeyError, OSError):
        return []


def save_java_list(javas: list[JavaInfo], storage_dir: str, last_selected: str = "") -> None:
    path = _java_list_path(storage_dir)
    os.makedirs(storage_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"known_javas": [asdict(j) for j in javas], "last_selected": last_selected}, f, ensure_ascii=False, indent=2)


def _interactive_select(javas: list[JavaInfo]) -> JavaInfo:
    print()
    print("  检测到多个 Java 安装，请选择其中一个：")
    print()
    for i, j in enumerate(javas, 1):
        print(f"  [{i}] {j.path}")
        print(f"      版本: {j.version}")
    print()
    while True:
        try:
            choice = input(f"  请输入编号 (1-{len(javas)}): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(javas):
                return javas[idx]
        except ValueError:
            pass
        print(f"  无效选择。")


# =============================================================================
# Java 版本兼容性
# =============================================================================

MC_JAVA_COMPAT: list[tuple[str, str, str, str, str]] = [
    ("0",    "1.12.2", "8",  "8",  "许多老牌 Mod 强制要求 Java 8"),
    ("1.13", "1.16.5", "8",  "11", "Java 11 性能更好"),
    ("1.17", "1.17.1", "16", "17", "Java 8 不再兼容"),
    ("1.18", "1.20.4", "17", "17", ""),
    ("1.20.5", "1.25", "21", "21", "官方强制版本"),
    ("1.25", "999",    "25", "25", "最新快照版"),
]

JDK_MIRROR_URLS: dict[str, dict[str, str]] = {
    "17": {
        "linux": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-17.0.9_linux-x64_bin.tar.gz",
        "windows": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-17.0.9_windows-x64_bin.zip",
    },
    "21": {
        "linux": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-21.0.2_linux-x64_bin.tar.gz",
        "windows": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-21.0.2_windows-x64_bin.zip",
    },
    "25": {
        "linux": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-25.0.2_linux-x64_bin.tar.gz",
        "windows": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-25.0.2_windows-x64_bin.zip",
    },
}


def _version_tuple(v: str) -> tuple:
    parts = []
    for seg in v.split("."):
        clean = seg.split("-")[0].split("pre")[0]
        try:
            parts.append(int(clean))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _is_mc_in_range(mc_version: str, lo: str, hi: str) -> bool:
    mv = _version_tuple(mc_version)
    return _version_tuple(lo) <= mv <= _version_tuple(hi)


def get_java_requirement(mc_version: str) -> Optional[dict]:
    for lo, hi, min_j, rec_j, note in MC_JAVA_COMPAT:
        if _is_mc_in_range(mc_version, lo, hi):
            return {"min": min_j, "recommended": rec_j, "note": note}
    return None


def _os_platform() -> str:
    return "windows" if sys.platform.startswith("win") else "linux"


def _java_major_version(version_str: str) -> int:
    s = version_str.strip()
    if s.startswith("1."):
        parts = s.split(".")
        if len(parts) >= 2:
            try:
                return int(parts[1])
            except ValueError:
                pass
    try:
        return int(s.split(".")[0])
    except ValueError:
        return 0


def _check_java_compatibility(
    java_version: str, mc_version: str, storage_dir: str, project_dir: str,
) -> Optional[str]:
    if not mc_version:
        return None
    req = get_java_requirement(mc_version)
    if not req:
        return None
    java_major = _java_major_version(java_version)
    need_min = int(req["min"])
    rec = req["recommended"]
    if java_major >= need_min:
        return None

    print()
    print(f"  [警告] Java 版本不兼容！")
    print(f"         当前 Java:      {java_version} (主版本 {java_major})")
    print(f"         服务器 MC 版本: {mc_version}")
    print(f"         需要 Java {need_min} 或更高版本")
    print(f"         推荐 Java {rec}")
    if req["note"]:
        print(f"         提示: {req['note']}")
    print()

    if rec in JDK_MIRROR_URLS:
        print(f"  [1] 自动下载 JDK {rec}")
        print(f"  [2] 忽略警告，继续使用当前 Java")
        print(f"  [0] 取消启动")
        print()
        while True:
            c = input("  请选择 (0-2): ").strip()
            if c == "1":
                exe = _download_jdk(rec, project_dir or storage_dir)
                if exe:
                    ver = _run_java_version(exe)
                    now = datetime.now(timezone.utc).isoformat()
                    save_java_list([JavaInfo(path=exe, version=ver or rec, detected_at=now)], storage_dir, last_selected=exe)
                    print(f"[Java] 已切换至: {exe} (版本 {ver or rec})")
                    return exe
                print(); print("  [错误] 下载失败"); continue
            elif c == "2":
                return None
            elif c == "0":
                raise FileNotFoundError("用户取消了启动")
            else:
                print("  无效选择。")
    else:
        print(f"  （无法自动下载 JDK {rec}，请手动安装）")
        if input("  是否忽略警告继续？(y/N): ").strip().lower() != "y":
            raise FileNotFoundError("用户取消了启动")
        return None


def _download_jdk(java_version: str, project_dir: str) -> Optional[str]:
    os_name = _os_platform()
    urls = JDK_MIRROR_URLS.get(java_version)
    if not urls:
        print(f"  [错误] 不支持自动下载 JDK {java_version}"); return None
    url = urls.get(os_name)
    if not url:
        print(f"  [错误] JDK {java_version} 不支持 {os_name}"); return None

    jdk_dir = os.path.join(project_dir, f"jdk-{java_version}")
    java_exe = os.path.join(jdk_dir, "bin", "java" if os_name == "linux" else "java.exe")
    if os.path.isdir(jdk_dir) and os.path.isfile(java_exe):
        print(f"  [JDK] 已存在: {jdk_dir}"); return java_exe

    print(f"  [JDK] 正在下载 GraalVM JDK {java_version}（{os_name}）...")
    ext = ".zip" if os_name == "windows" else ".tar.gz"
    dl = os.path.join(project_dir, f"jdk-{java_version}{ext}")
    if not _download_with_progress(url, dl, desc=f"JDK {java_version}"):
        return None

    print("  [JDK] 正在解压...")
    try:
        if os_name == "windows":
            with zipfile.ZipFile(dl, "r") as zf:
                members = zf.infolist()
                roots = set()
                for m in members:
                    p = m.filename.split("/")[0]
                    if p:
                        roots.add(p)
                zf.extractall(project_dir)
                if roots:
                    src = os.path.join(project_dir, list(roots)[0])
                    if os.path.isdir(src) and not os.path.isdir(jdk_dir):
                        shutil.move(src, jdk_dir)
        else:
            with tarfile.open(dl, "r:gz") as tf:
                tf.extractall(project_dir)
            for entry in os.listdir(project_dir):
                ep = os.path.join(project_dir, entry)
                if os.path.isdir(ep) and "jdk" in entry.lower() and ep != jdk_dir:
                    shutil.move(ep, jdk_dir)
                    break
    except Exception as e:
        print(f"  [错误] 解压失败: {e}"); return None
    finally:
        if os.path.isfile(dl):
            os.remove(dl)

    if not os.path.isfile(java_exe):
        print("  [错误] 解压后未找到 java"); return None
    print(f"  [JDK] 下载完成: {java_exe}")
    return java_exe


def resolve_java(
    configured_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
    mc_version: str = "",
    project_dir: str = "",
) -> str:
    """自动解析 Java 路径。支持缓存、扫描、版本兼容检查、自动下载。"""
    if configured_path:
        ap = os.path.abspath(configured_path)
        if os.path.isfile(ap):
            return ap

    if storage_dir is None:
        storage_dir = os.getcwd()

    saved = load_java_list(storage_dir)
    valid_saved = [j for j in saved if os.path.isfile(j.path)]
    need_scan = False
    selected: Optional[JavaInfo] = None

    if len(valid_saved) == 1:
        selected = valid_saved[0]
        print(f"[Java] 使用本地记录的 Java: {selected.path} (版本 {selected.version})")
    elif len(valid_saved) > 1:
        print(f"[Java] 在本地记录中找到 {len(valid_saved)} 个 Java")
        selected = _interactive_select(valid_saved)
    else:
        need_scan = True

    if need_scan:
        print("[Java] 未找到本地可用 Java 记录，正在扫描系统...")
        found = detect_java_versions()
        if not found:
            req = get_java_requirement(mc_version) if mc_version else None
            print("  [Java] 未在系统中找到任何 Java 安装。")
            if req:
                print(f"         当前服务器 MC {mc_version} 需要 Java {req['min']}+")
                print(f"         推荐 Java {req['recommended']}")
                if req["note"]:
                    print(f"         提示: {req['note']}")
            else:
                print("         推荐安装 JDK 21 或更高版本。")

            target = req["recommended"] if req else "21"
            if target in JDK_MIRROR_URLS:
                print()
                if input(f"  是否自动下载 JDK {target}？(y/N): ").strip().lower() == "y":
                    exe = _download_jdk(target, project_dir or storage_dir)
                    if exe:
                        ver = _run_java_version(exe)
                        now = datetime.now(timezone.utc).isoformat()
                        found = [JavaInfo(path=exe, version=ver or target, detected_at=now)]
                        save_java_list(found, storage_dir, last_selected=exe)
                        print(f"[Java] 已下载: {exe} (版本 {ver or target})")

            if not found:
                raise FileNotFoundError("未在系统中找到任何 Java 安装。\n  请先安装 JDK 21 或更高版本：https://adoptium.net/")

        selected = found[0] if len(found) == 1 else _interactive_select(found)
        seen = {os.path.normpath(j.path) for j in found}
        extra = [j for j in saved if os.path.isfile(j.path) and os.path.normpath(j.path) not in seen]
        save_java_list(found + extra, storage_dir, last_selected=selected.path)
    else:
        save_java_list(saved, storage_dir, last_selected=selected.path)

    new_path = _check_java_compatibility(selected.version, mc_version, storage_dir, project_dir)
    return new_path if new_path else selected.path
