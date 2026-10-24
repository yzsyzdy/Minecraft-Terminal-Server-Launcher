"""
Java auto-detection, version compatibility, and JDK download module
"""

import subprocess
import sys
import os
import re
import json
import glob
import platform
import zipfile
import tarfile
import shutil
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, Any

from download_msl import _download_with_progress, _verify_sha256
from http_utils import fetch_json
from i18n import t


def _remove_quietly(path: str) -> None:
    """删文件，失败不抛——清理动作不该把主流程带崩。"""
    try:
        os.remove(path)
    except OSError:
        pass


# =============================================================================
# Java detection
# =============================================================================

@dataclass
class JavaInfo:
    """Describes a detected Java installation."""
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


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_macos() -> bool:
    return sys.platform == "darwin"


# POSIX 上找 JDK 的根目录。{home} 会在运行时替换成 $HOME。
_POSIX_JDK_ROOTS = (
    "/usr/lib/jvm",
    "/usr/java",
    "/opt/java",
    "/opt/jdk",
    "{home}/.jdks",
    "{home}/.sdkman/candidates/java",
    "{home}/.local/share/JetBrains/Toolbox/apps",
)

# macOS 专属的根目录。Homebrew 的两条分别是 Apple 芯片与 Intel 的安装前缀。
_MACOS_JDK_ROOTS = (
    "/Library/Java/JavaVirtualMachines",
    "{home}/Library/Java/JavaVirtualMachines",
    "/opt/homebrew/opt",
    "/usr/local/opt",
)

# 一个 JDK 目录里 java 可能落在这几种位置（相对该目录的路径分段）：
# 普通解压包、JetBrains 自带运行时、macOS 的 .jdk 包、Homebrew 的 openjdk 包。
_POSIX_JDK_SUFFIX_PARTS = (
    ("bin",),
    ("jbr", "bin"),
    ("Contents", "Home", "bin"),
    ("libexec", "openjdk.jdk", "Contents", "Home", "bin"),
)


def _java_exe_name() -> str:
    return "java.exe" if _is_windows() else "java"


def _search_java_from_env() -> list[JavaInfo]:
    found: list[JavaInfo] = []
    seen = set()
    now = datetime.now(timezone.utc).isoformat()
    candidates = []
    java_bin = _java_exe_name()
    for var in ("JAVA_HOME", "JDK_HOME", "JRE_HOME"):
        val = os.environ.get(var, "").strip()
        if val:
            candidates.append(os.path.join(val, "bin", java_bin))
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        d = d.strip()
        if not d:
            continue
        c = os.path.join(d, java_bin)
        if os.path.isfile(c):
            candidates.append(c)
        if not _is_windows() and os.path.islink(c):
            try:
                real = os.path.realpath(c)
                if real not in seen:
                    candidates.append(real)
            except OSError:
                pass
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
    java_bin = _java_exe_name()

    if _is_windows():
        roots = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Common"),
            os.path.join(os.environ.get("USERPROFILE", ""), ".jdks"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "JetBrains"),
        ]
        patterns = [
            f"Java/jdk-*/bin/{java_bin}", f"Java/jdk*/bin/{java_bin}",
            f"Java/jre*/bin/{java_bin}",
            f"Eclipse Adoptium/jdk-*/bin/{java_bin}",
            f"Eclipse Adoptium/jre-*/bin/{java_bin}",
            f"Microsoft/jdk-*/bin/{java_bin}",
            f"AdoptOpenJDK/jdk-*/bin/{java_bin}",
            f"Amazon Corretto/jdk*/bin/{java_bin}",
            f"Amazon Corretto/jre*/bin/{java_bin}",
            f"Zulu/zulu*/bin/{java_bin}",
            f"Liberica JDK/jdk-*/bin/{java_bin}",
            f"Common/Oracle/Java/javapath/{java_bin}",
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
    else:
        home = os.environ.get("HOME", "")
        roots = [p.format(home=home) for p in _POSIX_JDK_ROOTS]
        if _is_macos():
            # macOS 的 JDK 装成 .jdk 包，真正的 java 在 <包名>.jdk/Contents/Home/bin/ 下。
            # 系统自带的 /usr/bin/java 只是个 shim：图形界面里启动 MSTL 时 PATH 里
            # 不一定有它，只靠 PATH 扫描会漏掉所有已安装的 JDK。
            roots += [p.format(home=home) for p in _MACOS_JDK_ROOTS]
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for entry in entries:
                jdk_path = os.path.join(root, entry)
                if not os.path.isdir(jdk_path):
                    continue
                for parts in _POSIX_JDK_SUFFIX_PARTS:
                    exe = os.path.join(jdk_path, *parts, java_bin)
                    if not os.path.isfile(exe):
                        continue
                    norm = os.path.normpath(exe)
                    if norm in seen:
                        continue
                    ver = _run_java_version(norm)
                    if ver:
                        seen.add(norm)
                        found.append(JavaInfo(path=norm, version=ver, detected_at=now))
    return found


def _search_java_in_roots(roots: list[str]) -> list[JavaInfo]:
    """扫一批自定义目录里的便携 JDK。

    给两个场合用：MSTL 自己的数据目录（自动下载的 JDK 就装在这里，以前
    扫描器对它视而不见，下过的 JDK 重启后就「丢」了）和实例目录。便携 JDK
    是开服机器上很常见的摆法：没有安装权限、或想给不同实例钉不同的 Java，
    用户把解压好的 JDK 摆在旁边就完事，注册表与环境变量一概不知情。
    """
    found: list[JavaInfo] = []
    seen: set[str] = set()
    now = datetime.now(timezone.utc).isoformat()
    java_bin = _java_exe_name()
    patterns = [
        f"jdk*/bin/{java_bin}", f"jre*/bin/{java_bin}",
        f"java*/bin/{java_bin}", f"*/bin/{java_bin}",
        f"*/jbr/bin/{java_bin}", f"bin/{java_bin}",
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for pat in patterns:
            for exe in glob.glob(os.path.join(root, pat)):
                norm = os.path.normpath(exe)
                if norm in seen or not os.path.isfile(norm):
                    continue
                ver = _run_java_version(norm)
                if ver:
                    seen.add(norm)
                    found.append(JavaInfo(path=norm, version=ver, detected_at=now))
    return found


def detect_java_versions(extra_roots: Optional[list[str]] = None) -> list[JavaInfo]:
    found = (_search_java_from_env() + _search_java_from_default_paths()
             + _search_java_in_roots(extra_roots or []))
    seen = set()
    merged: list[JavaInfo] = []
    for item in found:
        norm = os.path.normpath(item.path)
        if norm not in seen:
            seen.add(norm)
            merged.append(item)
    merged.sort(key=lambda j: _version_sort_key(j.version), reverse=True)
    return merged


_VERSION_SEG_RE = re.compile(r"\d+|[A-Za-z]+")
_MAJOR_RE = re.compile(r"^\s*(\d+)(?:\.(\d+))?")


def _version_sort_key(version: str) -> tuple:
    """把版本串拆成「永远可比较」的元组，供 Java 列表降序排列。

    每个元素是 (0, 数字, "") 或 (1, 0, 文本)——类型标签放在最前面，所以
    任何两个元素之间都不会出现 int 与 str 直接比较。旧实现是数字段放 int、
    其余原样放 str，于是 "21-ea" 得到 ('21-ea',)，和 (21, 0, 2) 一比就抛
    TypeError: '<' not supported between instances of 'str' and 'int'，
    整个 Java 扫描崩掉。机器上任何一处 EA / 厂商后缀构建就够触发。
    """
    key: list[tuple] = []
    for seg in _VERSION_SEG_RE.findall(str(version or "")):
        if seg.isdigit():
            key.append((0, int(seg), ""))
        else:
            key.append((1, 0, seg.lower()))
    if not key:
        # 完全取不出任何字母数字的串排到最后，但不能返回空元组——空元组和
        # 非空元组比较时永远「更小」，会让这类条目静默沉底且彼此相等。
        key.append((2, 0, ""))
    return tuple(key)


def _java_major_version(version_str: str) -> int:
    """从 `java -version` 给出的版本串里取出主版本号，取不到返回 0。

    实际会遇到的形态：21.0.2、17.0.9+11、21-ea、22+36、1.8.0_392。
    旧实现是 int(s.split(".")[0])，带任何后缀的串（21-ea、22+36）都落进
    except 返回 0；0 >= 21 为假，于是一个 Java 21 被判定成「对 MC 1.20.5+
    太旧」，用户被劝去下载本机已经装好的版本。
    """
    s = str(version_str or "").strip()
    m = _MAJOR_RE.match(s)
    if not m:
        return 0
    if s.startswith("1.") and m.group(2) is not None:
        return int(m.group(2))
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Java list persistence
# ---------------------------------------------------------------------------

def _java_list_path(storage_dir: str) -> str:
    return os.path.join(storage_dir, "java_list.json")


def _last_selected_path(storage_dir: str) -> str:
    """Read the last_selected field from java_list.json."""
    path = _java_list_path(storage_dir)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data.get("last_selected", "")
    except (json.JSONDecodeError, OSError):
        pass
    return ""


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


def _interactive_select(javas: list[JavaInfo], mc_version: str = "",
                        project_dir: str = "") -> JavaInfo:
    """让人从列表里挑一个 Java，同时给出「下载」与「手动指定」两个出口。

    以前这里只有数字可选。机器上装的全是旧 Java、而新服要新版本时，用户只能
    先随便选一个、再在后面的兼容警告里绕一圈才能到下载——更多的人是看到
    列表里没有想要的就直接退出了。把出口摆到选择器里，一步到位。
    """
    req = get_java_requirement(mc_version) if mc_version else None
    download_ver = (req or {}).get("recommended", "") if req else ""
    if download_ver and not can_auto_download(download_ver):
        download_ver = ""

    print()
    print(t("java.select_prompt"))
    print()
    for i, j in enumerate(javas, 1):
        print(t("java.select_line", i=i, path=j.path, ver=j.version))
    print()
    if download_ver:
        print(t("java.select.download", ver=download_ver))
    print(t("java.select.manual"))
    print(t("java.select.cancel"))
    print()
    while True:
        choice = input(t("java.select.prompt_num", max=len(javas))).strip().lower()
        if choice == "0":
            raise FileNotFoundError(t("java.compat.cancelled"))
        if choice == "d" and download_ver:
            exe = _download_jdk(download_ver, project_dir)
            if exe:
                ver = _run_java_version(exe) or download_ver
                return JavaInfo(path=exe, version=ver,
                                detected_at=datetime.now(timezone.utc).isoformat())
            print(t("java.compat.download_failed"))
            continue
        if choice == "m":
            raw = input(t("java.select.manual_prompt")).strip().strip('"')
            if not raw:
                continue
            ap = os.path.abspath(raw)
            if os.path.isfile(ap):
                ver = _run_java_version(ap)
                if ver:
                    return JavaInfo(path=ap, version=ver,
                                    detected_at=datetime.now(timezone.utc).isoformat())
            print(t("java.manual_bad", path=raw))
            continue
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(javas):
                return javas[idx]
        except ValueError:
            pass
        print(t("app.invalid_choice_short"))


# =============================================================================
# Java version compatibility
# =============================================================================

MC_JAVA_COMPAT: list[tuple[str, str, str, str, str]] = [
    ("0",    "1.12.2", "8",  "8",  "Many older mods require Java 8"),
    ("1.13", "1.16.5", "8",  "11", "Java 11 offers better performance"),
    ("1.17", "1.17.1", "16", "17", "Java 8 no longer works"),
    ("1.18", "1.20.4", "17", "17", ""),
    ("1.20.5", "1.24", "21", "21", "Officially required version"),
    ("1.25", "999",    "25", "25", "Latest snapshot"),
]

# 各版本 JDK 的下载源。键是「系统-架构」，值是归档地址。
#
# 分两层：
#   * GraalVM（经 fastmirror 镜像）——国内快，但镜像上只有 windows-x64 与
#     linux-x64 两个包；实测 macos-x64 / macos-aarch64 / linux-aarch64 一律
#     返回 500（文件不存在），所以不能拿它当唯一来源。
#   * Eclipse Temurin（Adoptium 官方 API）——覆盖 win/linux/mac × x64/aarch64，
#     而且 Java 8 / 11 / 16 只有它有（GraalVM 从 17 起）。它在境外、国内偏慢，
#     因此只在镜像没有对应组合时兜底。
JDK_MIRROR_URLS: dict[str, dict[str, str]] = {
    "17": {
        "linux-x64": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-17.0.9_linux-x64_bin.tar.gz",
        "windows-x64": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-17.0.9_windows-x64_bin.zip",
    },
    "21": {
        "linux-x64": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-21.0.2_linux-x64_bin.tar.gz",
        "windows-x64": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-21.0.2_windows-x64_bin.zip",
    },
    "25": {
        "linux-x64": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-25.0.2_linux-x64_bin.tar.gz",
        "windows-x64": "https://res.fastmirror.net/directlink/1/Java%20%E7%8E%AF%E5%A2%83/OpenJDK/Oracle%20GraalVM/graalvm-community-jdk-25.0.2_windows-x64_bin.zip",
    },
}

# 能自动装的 Java 大版本。8 / 11 / 16 只走 Adoptium——老版本服（≤1.16.5）
# 正好需要它们，而 GraalVM 没有。
JDK_DOWNLOAD_VERSIONS: tuple[str, ...] = ("8", "11", "16", "17", "21", "25")

# Adoptium 的 os / architecture 取值与本地叫法不一致，这里做映射。
_ADOPTIUM_OS = {"windows": "windows", "linux": "linux", "darwin": "mac"}
_ADOPTIUM_ARCH = {
    "x86_64": "x64", "amd64": "x64", "x64": "x64",
    "aarch64": "aarch64", "arm64": "aarch64",
}

# 镜像源的归档 sha256，来源是 GitHub 上 graalvm/graalvm-ce-builds 对应
# release 里的 .sha256 sidecar（.../releases/download/jdk-<ver>/<归档>.sha256）。
# 镜像站只镜像字节、不提供校验值，而这里下载的是要拿去跑服务器的 java：镜像被
# 换成别的包、被中间人改过、或者只下到一半，都会一直要到 java 启动失败才暴露。
#
# 25.0.2 这两个值同时出现在 GitHub API 的 asset digest 和 sidecar 里，两条独立
# 渠道完全一致；17.0.9 / 21.0.2 是旧 release（API 不返回 digest），只从 sidecar
# 取。键是「版本, 系统-架构」，与 JDK_MIRROR_URLS 一一对应。
#
# 走 Adoptium 的组合不在这里：它的 assets 接口会随包给出官方 checksum，
# _jdk_source() 直接把那个值带回来，所以不用手工钉（也就不会因为补丁版
# 升级而过期）。
JDK_SHA256: dict[tuple[str, str], str] = {
    ("17", "windows-x64"): "285e045bfc0b87d2b61958fea97444c3c6c7e68fba3fdbbe146622328b52ec38",
    ("17", "linux-x64"): "e47ba7229cef02393e19d5b8f46f7f1cab4829dd17bfe84d5431fc8ff0e22a96",
    ("21", "windows-x64"): "e17b7bead097bf372a5c75df17815b0a2f30b777a019d25eff7706b21421f7fa",
    ("21", "linux-x64"): "b048069aaa3a99b84f5b957b162cc181a32a4330cbc35402766363c5be76ae48",
    ("25", "windows-x64"): "72edc27ce2dd3c156332144a339c97995745cae291205ed2f9b5cc26aa9cb422",
    ("25", "linux-x64"): "e0be791c8fda4d03b6b0a0cb824fef3149736170057b3a515252b44419606af0",
}


def _machine_arch() -> str:
    """把 platform.machine() 归一成 x64 / aarch64；认不出来返回空串。

    归一之后才敢去拼下载地址——ARM 机器上拿到 x64 的包，装完是一堆
    「Exec format error」，比直接说「装不了」更难查。
    """
    return _ADOPTIUM_ARCH.get(platform.machine().lower(), "")


def can_auto_download(java_version: str) -> bool:
    """这个 Java 大版本有没有自动安装渠道（能不能装 ≠ 本机装不装得到）。"""
    return str(java_version) in JDK_DOWNLOAD_VERSIONS


def _adoptium_package(ver: str, os_name: str, arch: str) -> Optional[tuple[str, str]]:
    """向 Adoptium 问某个 版本/系统/架构 的 JDK，返回 (下载地址, sha256)。

    assets 接口一次就把「当前最新补丁版 + 包地址 + 官方校验值」都给出来，
    所以代码里不用钉死小版本号，换补丁版时这里什么都不用改。
    """
    os_key = _ADOPTIUM_OS.get(os_name)
    arch_key = _ADOPTIUM_ARCH.get(arch.lower())
    if not os_key or not arch_key:
        return None
    url = (
        f"https://api.adoptium.net/v3/assets/latest/{ver}/hotspot"
        f"?architecture={arch_key}&image_type=jdk&os={os_key}&vendor=eclipse"
    )
    data = fetch_json(url, timeout=20)
    if not isinstance(data, list) or not data:
        return None
    package = (data[0].get("binary") or {}).get("package") or {}
    link = str(package.get("link") or "")
    checksum = str(package.get("checksum") or "")
    return (link, checksum) if link else None


def _jdk_source(ver: str, os_name: str, arch: str) -> Optional[tuple[str, str]]:
    """挑首选下载源：先国内镜像，镜像没有这个组合再问 Adoptium。

    镜像优先是因为它在国内快；也只有镜像缺这个组合时才去问 Adoptium，
    省掉一次境外请求。
    """
    key = f"{os_name}-{arch}"
    mirrored = JDK_MIRROR_URLS.get(ver, {}).get(key)
    if mirrored:
        return mirrored, JDK_SHA256.get((ver, key), "")
    return _adoptium_package(ver, os_name, arch)


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
    """检测当前操作系统，支持 Windows、macOS、Linux。"""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform.startswith("darwin"):
        return "darwin"
    return "linux"


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
    print(t("java.compat.title"))
    print(t("java.compat.current", ver=java_version, major=java_major))
    print(t("java.compat.mc_version", ver=mc_version))
    print(t("java.compat.required", ver=need_min))
    print(t("java.compat.recommended", ver=rec))
    if req["note"]:
        print(t("java.hint", note=req["note"]))
    print()

    if can_auto_download(rec):
        print(t("java.compat.download", n=1, ver=rec))
        print(t("java.compat.ignore", n=2))
        print(t("java.compat.cancel", n=0))
        print()
        while True:
            c = input(t("java.compat.prompt", max=2)).strip()
            if c == "1":
                exe = _download_jdk(rec, project_dir or storage_dir)
                if exe:
                    ver = _run_java_version(exe)
                    now = datetime.now(timezone.utc).isoformat()
                    new_one = JavaInfo(path=exe, version=ver or rec,
                                       detected_at=now)
                    # 别把列表覆盖成只含这一个：机器上可能已经记着好几个 Java，
                    # 覆盖掉的话，下次启动别的实例时选择器里只剩这一条。
                    merged = [j for j in load_java_list(storage_dir)
                              if os.path.isfile(j.path)
                              and os.path.normpath(j.path) != os.path.normpath(exe)]
                    merged.append(new_one)
                    save_java_list(merged, storage_dir, last_selected=exe)
                    print(t("java.downloaded", path=exe, ver=ver or rec))
                    return exe
                print(); print(t("java.compat.download_failed")); continue
            elif c == "2":
                return None
            elif c == "0":
                raise FileNotFoundError(t("java.compat.cancelled"))
            else:
                print(t("app.invalid_choice_short"))
    else:
        print(t("java.compat.no_auto", ver=rec))
        if input(t("java.compat.ignore_ask")).strip().lower() != "y":
            raise FileNotFoundError(t("java.compat.cancelled"))
        return None


def _safe_extract_tar(tf: tarfile.TarFile, dest: str) -> None:
    """把 tar 解到 dest，先拒绝任何会落到 dest 之外的成员。

    `tarfile.extractall()` 默认会老老实实照成员名里的 "../" 或绝对路径往外写
    （Python 3.12 起要显式传 filter 才会拦）。实测一个含 "../TAROUT.txt" 的
    归档能把文件放到目标目录外面。java_tools 是全项目唯一裸调 extractall 的
    地方，core/transfer.py 与 core/archive.py 都刻意做了同类防护。
    """
    dest_abs = os.path.realpath(dest)
    prefix = dest_abs + os.sep

    def _inside(path: str) -> bool:
        return path == dest_abs or path.startswith(prefix)

    for member in tf.getmembers():
        name = member.name
        if os.path.isabs(name) or name.startswith(("/", "\\")):
            raise ValueError(f"unsafe tar member (absolute): {name}")
        if not _inside(os.path.realpath(os.path.join(dest_abs, name))):
            raise ValueError(f"unsafe tar member (escapes target): {name}")
        if member.issym() or member.islnk():
            link = member.linkname
            # 符号链接的 linkname 相对成员所在目录，硬链接相对归档根。
            base = dest_abs if member.islnk() else os.path.dirname(
                os.path.join(dest_abs, name))
            link_target = link if os.path.isabs(link) else os.path.join(base, link)
            if not _inside(os.path.realpath(link_target)):
                raise ValueError(f"unsafe tar link: {name} -> {link}")
    # 成员已经在上面逐个校验过了，这里要的是「按原样解出来」：JDK 的
    # bin/java 必须保住可执行位。Python 3.14 起 extractall 默认改用 data
    # 过滤器，它会把普通文件的权限压成 rw-，解出来的 java 直接跑不起来。
    try:
        tf.extractall(dest, filter="fully_trusted")
    except TypeError:            # Python < 3.11.4 没有 filter 参数
        tf.extractall(dest)


def _extract_and_rename_jdk(archive: str, target_dir: str, os_name: str) -> Optional[str]:
    """Extract JDK archive and move the extracted root to target_dir."""
    tmp = ""
    try:
        # 临时目录必须唯一。以前固定用 ".jdk_extract_tmp"，而且开头先 rmtree：
        # 另一处（另一个终端、面板里另一次安装）正在解压时，这次会把对方解到
        # 一半的目录整个删掉。mkdtemp 顺带省掉「先删再建」这两步。
        tmp = tempfile.mkdtemp(prefix=".jdk_extract_",
                               dir=os.path.dirname(target_dir) or ".")
        # 按归档后缀判，不按系统判：Windows 是 .zip，Linux 与 macOS 都是
        # .tar.gz。以后若某个源换了打包格式，这里不用跟着改。
        if archive.lower().endswith(".zip"):
            with zipfile.ZipFile(archive, "r") as zf:
                roots = set()
                for m in zf.infolist():
                    p = m.filename.split("/")[0]
                    if p:
                        roots.add(p)
                zf.extractall(tmp)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                roots = set()
                for m in tf.getmembers():
                    p = m.name.split("/")[0]
                    if p:
                        roots.add(p)
                _safe_extract_tar(tf, tmp)
        if roots:
            # 按字母序取第一个根目录，避免 set 无序导致随机选择
            root_dir = sorted(roots)[0]
            src = os.path.join(tmp, root_dir)
            if os.path.isdir(src):
                shutil.move(src, target_dir)
    except Exception as e:
        print(t("jdk.extract_failed", msg=str(e)))
        return None
    finally:
        # 成功失败都要清。以前清理只在成功路径上，解压中途报错就把
        # 上百 MB 的半成品永久留在项目目录里。
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    java_bin = "java.exe" if os_name == "windows" else "java"
    exe = os.path.join(target_dir, "bin", java_bin)
    return exe if os.path.isfile(exe) else None


def _fetch_jdk(
    url: str,
    expected: str,
    java_version: str,
    os_name: str,
    arch: str,
    project_dir: str,
) -> Optional[str]:
    """从单个地址下载、校验、解压一个 JDK；失败返回 None（不抛）。"""
    # 归档格式按 URL 判，不按系统猜：Windows 是 .zip，Linux 与 macOS 都是 .tar.gz。
    ext = ".zip" if url.lower().split("?")[0].endswith(".zip") else ".tar.gz"
    dl = os.path.join(project_dir, f"jdk-{java_version}{ext}")
    if not _download_with_progress(url, dl, desc=f"JDK {java_version}"):
        # 下载失败也要清掉残片。以前只有成功路径删归档，而「是否已有 JDK」
        # 只按解压后的目录判断，所以 150–200MB 的半截归档会一直堆在项目目录里。
        _remove_quietly(dl)
        return None

    if expected:
        # 不匹配时 _verify_sha256 会把文件删掉。
        if not _verify_sha256(dl, expected):
            print(t("jdk.sha256_mismatch", ver=java_version))
            return None
    else:
        # 没有权威校验值就别假装校验过了——说清楚，把判断留给用户。
        print(t("jdk.unverified", ver=java_version))

    print(t("jdk.extracting"))
    jdk_dir = os.path.join(project_dir, f"jdk-{java_version}")
    java_exe = _extract_and_rename_jdk(dl, jdk_dir, os_name)
    _remove_quietly(dl)

    if not java_exe or not os.path.isfile(java_exe):
        print(t("jdk.java_not_found")); return None
    print(t("jdk.complete", path=java_exe))
    return java_exe


def _download_jdk(java_version: str, project_dir: str) -> Optional[str]:
    os_name = _os_platform()
    if not can_auto_download(java_version):
        print(t("jdk.unsupported", ver=java_version)); return None
    arch = _machine_arch()
    if not arch:
        # 认不出架构就别猜着下——下错架构的包会在启动时变成一句
        # "Exec format error"，离真正原因很远。
        print(t("jdk.unsupported_arch", ver=java_version, arch=platform.machine()))
        return None

    jdk_dir = os.path.join(project_dir, f"jdk-{java_version}")
    java_exe = os.path.join(jdk_dir, "bin", "java.exe" if os_name == "windows" else "java")
    if os.path.isdir(jdk_dir) and os.path.isfile(java_exe):
        print(t("jdk.existing", dir=jdk_dir)); return java_exe

    source = _jdk_source(java_version, os_name, arch)
    if not source:
        print(t("jdk.unsupported_os", ver=java_version, os=f"{os_name}/{arch}"))
        return None

    print(t("jdk.downloading", ver=java_version, os=f"{os_name}/{arch}"))
    found = _fetch_jdk(source[0], source[1], java_version, os_name, arch, project_dir)
    if found:
        return found

    # 镜像表里钉的是具体小版本（GraalVM 17.0.9 之类），上游撤掉那个文件时
    # HEAD 还在、GET 却 500——这时候换 Adoptium 再试一次，而不是直接告诉
    # 用户「这个版本不支持自动下载」。
    fallback = _adoptium_package(java_version, os_name, arch)
    if not fallback or fallback[0] == source[0]:
        return None
    print(t("jdk.source_fallback", ver=java_version))
    return _fetch_jdk(fallback[0], fallback[1], java_version, os_name, arch, project_dir)


def resolve_java(
    configured_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
    mc_version: str = "",
    project_dir: str = "",
    extra_roots: Optional[list[str]] = None,
) -> str:
    """Resolve Java path with caching, scanning, compat checking, and auto-download.

    extra_roots 是额外要扫的目录（通常是实例目录）：便携 JDK 摆在实例里的
    摆法靠它才能被看到。MSTL 自己的数据目录（project_dir / storage_dir）
    永远会扫——自动下载的 JDK 就落在那里。
    """
    if configured_path:
        ap = os.path.abspath(configured_path)
        if os.path.isfile(ap):
            return ap

    if storage_dir is None:
        # 不能退回 os.getcwd()：打包版从快捷方式/资源管理器启动时，当前目录
        # 是随机的（往往是 C:\Windows\System32），Java 列表会被读到、写到
        # 一个和用户数据毫不相干的地方。项目里其它地方一律用 app_dir()。
        from paths import app_dir
        storage_dir = app_dir()

    # 额外扫描根：实例目录（extra_roots）+ MSTL 自己的数据目录。后者永远在：
    # 自动下载的 JDK 就装在这里，不扫的话重启后它就从列表里消失了。
    scan_roots: list[str] = []
    for root in list(extra_roots or []) + [project_dir, storage_dir]:
        if not root:
            continue
        key = os.path.normcase(os.path.abspath(root))
        if key not in {os.path.normcase(os.path.abspath(r)) for r in scan_roots}:
            scan_roots.append(root)

    saved = load_java_list(storage_dir)
    valid_saved = [j for j in saved if os.path.isfile(j.path)]
    need_scan = False
    selected: Optional[JavaInfo] = None

    if len(valid_saved) == 1:
        selected = valid_saved[0]
        print(t("java.detect_single", path=selected.path, ver=selected.version))
    elif len(valid_saved) > 1:
        last_path = _last_selected_path(storage_dir)
        if last_path:
            matches = [j for j in valid_saved if os.path.normpath(j.path) == os.path.normpath(last_path)]
            if matches:
                selected = matches[0]
                print(t("java.detect_last", path=selected.path, ver=selected.version))
        if selected is None:
            # 本地记录里没有的便携 JDK（摆在实例目录或 MSTL 目录里的）也该
            # 出现在选择器里，否则用户看到的永远是那份过时的清单
            known = {os.path.normpath(j.path) for j in valid_saved}
            fresh = [j for j in _search_java_in_roots(scan_roots)
                     if os.path.normpath(j.path) not in known]
            if fresh:
                valid_saved = valid_saved + fresh
                valid_saved.sort(key=lambda j: _version_sort_key(j.version), reverse=True)
                print(t("java.found_portable", count=len(fresh)))
            print(t("java.detect_multiple", count=len(valid_saved)))
            selected = _interactive_select(valid_saved, mc_version, project_dir or storage_dir)
    else:
        need_scan = True

    if need_scan:
        print(t("java.scanning"))
        found = detect_java_versions(scan_roots)
        if not found:
            req = get_java_requirement(mc_version) if mc_version else None
            print(t("java.none_found"))
            if req:
                print(t("java.need_version", mc=mc_version, ver=req["min"]))
                print(t("java.recommend_version", ver=req["recommended"]))
                if req["note"]:
                    print(t("java.hint", note=req["note"]))
            else:
                print(t("java.recommend_generic"))

            target = req["recommended"] if req else "21"
            if can_auto_download(target):
                print()
                if input(t("java.ask_download", ver=target)).strip().lower() == "y":
                    exe = _download_jdk(target, project_dir or storage_dir)
                    if exe:
                        ver = _run_java_version(exe)
                        now = datetime.now(timezone.utc).isoformat()
                        found = [JavaInfo(path=exe, version=ver or target, detected_at=now)]
                        save_java_list(found, storage_dir, last_selected=exe)
                        print(t("java.downloaded", path=exe, ver=ver or target))

            if not found:
                raise FileNotFoundError("No Java installation found.\n  Install JDK 21 or later: https://adoptium.net/")

        selected = found[0] if len(found) == 1 else _interactive_select(
            found, mc_version, project_dir or storage_dir)
        seen = {os.path.normpath(j.path) for j in found}
        extra = [j for j in saved if os.path.isfile(j.path) and os.path.normpath(j.path) not in seen]
        save_java_list(found + extra, storage_dir, last_selected=selected.path)
    else:
        # 选择器里可能出现两个本地记录里没有的条目：额外目录里新扫到的便携
        # JDK、当场下载的 JDK。把选中的那个并进记录，下次就不必再扫。
        merged = [j for j in saved if os.path.isfile(j.path)]
        known = {os.path.normpath(j.path) for j in merged}
        for cand in valid_saved + [selected]:
            key = os.path.normpath(cand.path)
            if key not in known:
                known.add(key)
                merged.append(cand)
        save_java_list(merged, storage_dir, last_selected=selected.path)

    new_path = _check_java_compatibility(selected.version, mc_version, storage_dir, project_dir)
    return new_path if new_path else selected.path
