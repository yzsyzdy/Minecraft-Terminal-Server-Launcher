"""
Java auto-detection, version compatibility, and JDK download module
"""

import subprocess
import sys
import os
import re
import json
import glob
import zipfile
import tarfile
import shutil
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Optional, Any

from download_msl import _download_with_progress, _verify_sha256
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
        roots = [
            "/usr/lib/jvm",
            "/usr/java",
            "/opt/java",
            "/opt/jdk",
            os.path.join(os.environ.get("HOME", ""), ".jdks"),
            os.path.join(os.environ.get("HOME", ""), ".sdkman", "candidates", "java"),
            os.path.join(os.environ.get("HOME", ""), ".local", "share", "JetBrains", "Toolbox", "apps"),
        ]
        for root in roots:
            if not root or not os.path.isdir(root):
                continue
            for entry in os.listdir(root):
                jdk_path = os.path.join(root, entry)
                if not os.path.isdir(jdk_path):
                    continue
                exe = os.path.join(jdk_path, "bin", java_bin)
                if os.path.isfile(exe):
                    norm = os.path.normpath(exe)
                    if norm in seen:
                        continue
                    ver = _run_java_version(norm)
                    if ver:
                        seen.add(norm)
                        found.append(JavaInfo(path=norm, version=ver, detected_at=now))
                jbr = os.path.join(jdk_path, "jbr", "bin", java_bin)
                if os.path.isfile(jbr):
                    norm = os.path.normpath(jbr)
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


def _interactive_select(javas: list[JavaInfo]) -> JavaInfo:
    print()
    print(t("java.select_prompt"))
    print()
    for i, j in enumerate(javas, 1):
        print(t("java.select_line", i=i, path=j.path, ver=j.version))
    print()
    while True:
        try:
            choice = input(t("java.select_prompt_num", max=len(javas))).strip()
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

# 各版本 JDK 归档的 sha256，来源是 GitHub 上 graalvm/graalvm-ce-builds 对应
# release 的 asset digest（https://api.github.com/repos/graalvm/graalvm-ce-builds
# /releases/tags/jdk-<ver>）。镜像站只镜像字节、不提供校验值，而这里下载的是
# 要拿去跑服务器的 java：镜像被换成别的包、被中间人改过、或者只下到一半，都会
# 一直要到 java 启动失败才暴露。
#
# 17.0.9 / 21.0.2 的官方值是空的：这两个 release 不返回 digest，而 .sha256
# 资产在部分网络下取不到。缺项时退化成「必须能解压出 bin/java」——挡得住
# 错误页和截断包，挡不住蓄意换包，所以下载时会明确告警而不是假装校验过了。
JDK_SHA256: dict[tuple[str, str], str] = {
    ("25", "windows"): "72edc27ce2dd3c156332144a339c97995745cae291205ed2f9b5cc26aa9cb422",
    ("25", "linux"): "e0be791c8fda4d03b6b0a0cb824fef3149736170057b3a515252b44419606af0",
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

    if rec in JDK_MIRROR_URLS:
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
                    save_java_list([JavaInfo(path=exe, version=ver or rec, detected_at=now)], storage_dir, last_selected=exe)
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
        if os_name == "windows":
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


def _download_jdk(java_version: str, project_dir: str) -> Optional[str]:
    os_name = _os_platform()
    urls = JDK_MIRROR_URLS.get(java_version)
    if not urls:
        print(t("jdk.unsupported", ver=java_version)); return None
    url = urls.get(os_name)
    if not url:
        print(t("jdk.unsupported_os", ver=java_version, os=os_name)); return None

    jdk_dir = os.path.join(project_dir, f"jdk-{java_version}")
    java_exe = os.path.join(jdk_dir, "bin", "java.exe" if os_name == "windows" else "java")
    if os.path.isdir(jdk_dir) and os.path.isfile(java_exe):
        print(t("jdk.existing", dir=jdk_dir)); return java_exe

    print(t("jdk.downloading", ver=java_version, os=os_name))
    ext = ".zip" if os_name == "windows" else ".tar.gz"
    dl = os.path.join(project_dir, f"jdk-{java_version}{ext}")
    if not _download_with_progress(url, dl, desc=f"JDK {java_version}"):
        # 下载失败也要清掉残片。以前只有成功路径删归档，而「是否已有 JDK」
        # 只按解压后的目录判断，所以 150–200MB 的半截归档会一直堆在项目目录里。
        _remove_quietly(dl)
        return None

    expected = JDK_SHA256.get((java_version, os_name))
    if expected:
        # 不匹配时 _verify_sha256 会把文件删掉。
        if not _verify_sha256(dl, expected):
            print(t("jdk.sha256_mismatch", ver=java_version))
            return None
    else:
        # 没有权威校验值就别假装校验过了——说清楚，把判断留给用户。
        print(t("jdk.unverified", ver=java_version))

    print(t("jdk.extracting"))
    java_exe = _extract_and_rename_jdk(dl, jdk_dir, os_name)
    _remove_quietly(dl)

    if not java_exe or not os.path.isfile(java_exe):
        print(t("jdk.java_not_found")); return None
    print(t("jdk.complete", path=java_exe))
    return java_exe


def resolve_java(
    configured_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
    mc_version: str = "",
    project_dir: str = "",
) -> str:
    """Resolve Java path with caching, scanning, compat checking, and auto-download."""
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
            print(t("java.detect_multiple", count=len(valid_saved)))
            selected = _interactive_select(valid_saved)
    else:
        need_scan = True

    if need_scan:
        print(t("java.scanning"))
        found = detect_java_versions()
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
            if target in JDK_MIRROR_URLS:
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

        selected = found[0] if len(found) == 1 else _interactive_select(found)
        seen = {os.path.normpath(j.path) for j in found}
        extra = [j for j in saved if os.path.isfile(j.path) and os.path.normpath(j.path) not in seen]
        save_java_list(found + extra, storage_dir, last_selected=selected.path)
    else:
        save_java_list(saved, storage_dir, last_selected=selected.path)

    new_path = _check_java_compatibility(selected.version, mc_version, storage_dir, project_dir)
    return new_path if new_path else selected.path
