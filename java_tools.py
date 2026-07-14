"""
Java auto-detection, version compatibility, and JDK download module
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
from i18n import t


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


def _version_sort_key(version: str) -> tuple:
    parts = []
    for seg in version.split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(seg)
    return tuple(parts)


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
    ("1.20.5", "1.25", "21", "21", "Officially required version"),
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


def _extract_and_rename_jdk(archive: str, target_dir: str, os_name: str) -> Optional[str]:
    """Extract JDK archive and move the extracted root to target_dir."""
    try:
        if os_name == "windows":
            with zipfile.ZipFile(archive, "r") as zf:
                roots = set()
                for m in zf.infolist():
                    p = m.filename.split("/")[0]
                    if p:
                        roots.add(p)
                tmp = os.path.join(os.path.dirname(target_dir), ".jdk_extract_tmp")
                if os.path.isdir(tmp):
                    shutil.rmtree(tmp)
                zf.extractall(tmp)
                if roots:
                    src = os.path.join(tmp, list(roots)[0])
                    if os.path.isdir(src):
                        shutil.move(src, target_dir)
                shutil.rmtree(tmp, ignore_errors=True)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                roots = set()
                for m in tf.getmembers():
                    p = m.name.split("/")[0]
                    if p:
                        roots.add(p)
                tmp = os.path.join(os.path.dirname(target_dir), ".jdk_extract_tmp")
                if os.path.isdir(tmp):
                    shutil.rmtree(tmp)
                tf.extractall(tmp)
                if roots:
                    src = os.path.join(tmp, list(roots)[0])
                    if os.path.isdir(src):
                        shutil.move(src, target_dir)
                shutil.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        print(t("jdk.extract_failed", msg=str(e)))
        return None

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
        return None

    print(t("jdk.extracting"))
    java_exe = _extract_and_rename_jdk(dl, jdk_dir, os_name)
    if os.path.isfile(dl):
        os.remove(dl)

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
        storage_dir = os.getcwd()

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
