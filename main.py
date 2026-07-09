"""
Minecraft 服务器启动模块

提供启动 Minecraft 服务器的核心函数，基于 Leaves/Fabric 服务器的 JVM 参数设计。
"""

import subprocess
import sys
import os
import signal
import json
import glob
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, fields
from typing import Optional


# =============================================================================
# Java 自动检测模块
# =============================================================================

@dataclass
class JavaInfo:
    """描述一个检测到的 Java 安装。"""
    path: str
    version: str
    detected_at: str  # ISO 8601 格式

    @staticmethod
    def from_dict(d: dict) -> "JavaInfo":
        return JavaInfo(
            path=d["path"],
            version=d["version"],
            detected_at=d.get("detected_at", ""),
        )


def _run_java_version(java_exe: str) -> Optional[str]:
    """执行 java -version，解析版本号。失败返回 None。"""
    try:
        result = subprocess.run(
            [java_exe, "-version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        # java -version 输出在 stderr
        output = result.stderr or result.stdout
        for line in output.splitlines():
            line = line.strip()
            # 匹配形如：openjdk version "21.0.9" 或 java version "1.8.0_401"
            if '"' in line and "version" in line:
                start = line.index('"') + 1
                end = line.index('"', start)
                return line[start:end]
        return None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def _search_java_from_env() -> list[JavaInfo]:
    """从环境变量 JAVA_HOME / JDK_HOME / PATH 搜索 java。"""
    found: list[JavaInfo] = []
    seen = set()
    now = datetime.now(timezone.utc).isoformat()

    candidates = []

    for var in ("JAVA_HOME", "JDK_HOME", "JRE_HOME"):
        val = os.environ.get(var, "").strip()
        if val:
            candidates.append(os.path.join(val, "bin", "java.exe"))

    # 扫描 PATH 中的 java.exe
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        d = d.strip()
        if not d:
            continue
        java_candidate = os.path.join(d, "java.exe")
        if os.path.isfile(java_candidate):
            candidates.append(java_candidate)

    for cand in candidates:
        norm = os.path.normpath(cand)
        if norm in seen:
            continue
        if not os.path.isfile(norm):
            continue
        version = _run_java_version(norm)
        if version:
            seen.add(norm)
            found.append(JavaInfo(path=norm, version=version, detected_at=now))

    return found


def _search_java_from_default_paths() -> list[JavaInfo]:
    """扫描 Windows 上常见的 JDK/JRE 安装目录。"""
    found: list[JavaInfo] = []
    seen = set()
    now = datetime.now(timezone.utc).isoformat()

    search_roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Common"),
        os.path.join(os.environ.get("USERPROFILE", ""), ".jdks"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "JetBrains"),
    ]

    # 在 Program Files 下搜索 Java 目录结构
    patterns = [
        "Java/jdk-*/bin/java.exe",
        "Java/jdk*/bin/java.exe",
        "Java/jre*/bin/java.exe",
        "Eclipse Adoptium/jdk-*/bin/java.exe",
        "Eclipse Adoptium/jre-*/bin/java.exe",
        "Microsoft/jdk-*/bin/java.exe",
        "AdoptOpenJDK/jdk-*/bin/java.exe",
        "Amazon Corretto/jdk*/bin/java.exe",
        "Amazon Corretto/jre*/bin/java.exe",
        "Zulu/zulu*/bin/java.exe",
        "Liberica JDK/jdk-*/bin/java.exe",
        "Common/Oracle/Java/javapath/java.exe",
    ]

    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for pattern in patterns:
            full_glob = os.path.join(root, pattern)
            for exe in glob.glob(full_glob):
                norm = os.path.normpath(exe)
                if norm in seen:
                    continue
                version = _run_java_version(norm)
                if version:
                    seen.add(norm)
                    found.append(JavaInfo(path=norm, version=version, detected_at=now))

    # 在 JetBrains 目录下搜 jbr（捆绑的 JetBrains Runtime）
    jbr_pattern = "*/jbr/bin/java.exe"
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for exe in glob.glob(os.path.join(root, jbr_pattern)):
            norm = os.path.normpath(exe)
            if norm in seen:
                continue
            version = _run_java_version(norm)
            if version:
                seen.add(norm)
                found.append(JavaInfo(path=norm, version=version, detected_at=now))

    return found


def detect_java_versions() -> list[JavaInfo]:
    """执行一次完整的 Java 扫描，返回去重后的结果列表。"""
    found_env = _search_java_from_env()
    found_paths = _search_java_from_default_paths()

    seen = set()
    merged: list[JavaInfo] = []
    for item in found_env + found_paths:
        norm = os.path.normpath(item.path)
        if norm not in seen:
            seen.add(norm)
            merged.append(item)

    # 按版本号（降序）排列
    merged.sort(key=lambda j: _version_sort_key(j.version), reverse=True)
    return merged


def _version_sort_key(version: str) -> tuple:
    """将 '21.0.9' 转为 (21, 0, 9, ...) 用于排序。非数字段放到最后。"""
    parts = []
    for segment in version.split("."):
        try:
            parts.append(int(segment))
        except ValueError:
            parts.append(segment)
    return tuple(parts)


# ---------------------------------------------------------------------------
# 持久化：Java 列表保存到 JSON
# ---------------------------------------------------------------------------

def _java_list_path(storage_dir: str) -> str:
    return os.path.join(storage_dir, "java_list.json")


def load_java_list(storage_dir: str) -> list[JavaInfo]:
    """加载本地缓存的 Java 列表。"""
    path = _java_list_path(storage_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            # 新格式：{ "known_javas": [...], "last_selected": "..." }
            raw = data.get("known_javas", [])
        elif isinstance(data, list):
            raw = data
        else:
            return []
        return [JavaInfo.from_dict(j) for j in raw if isinstance(j, dict)]
    except (json.JSONDecodeError, KeyError, OSError):
        return []


def save_java_list(javas: list[JavaInfo], storage_dir: str, last_selected: str = "") -> None:
    """保存 Java 列表到本地缓存。"""
    path = _java_list_path(storage_dir)
    os.makedirs(storage_dir, exist_ok=True)
    data = {
        "known_javas": [asdict(j) for j in javas],
        "last_selected": last_selected,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _interactive_select(javas: list[JavaInfo]) -> JavaInfo:
    """在终端列出多个 Java 版本让用户选择，返回选中的 JavaInfo。"""
    print()
    print("  检测到多个 Java 安装，请选择其中一个：")
    print()
    for i, j in enumerate(javas, 1):
        print(f"  [{i}] {j.path}")
        print(f"      版本: {j.version}")
    print()
    while True:
        try:
            choice = input("  请输入编号 (1-{}): ".format(len(javas))).strip()
            idx = int(choice) - 1
            if 0 <= idx < len(javas):
                return javas[idx]
        except ValueError:
            pass
        print(f"  无效选择，请输入 1-{len(javas)} 之间的数字。")


def resolve_java(
    configured_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
) -> str:
    """
    自动解析可用的 Java 路径。

    流程
    -----
    1. 如果 configured_path 存在且有效，直接返回。
    2. 加载本地缓存的 Java 列表，过滤出仍有效的项。
    3. 如果有效缓存中只有一项 → 直接使用。
    4. 如果有多项 → 询问用户选择。
    5. 如果没有有效缓存 → 执行完整扫描。
    6. 扫描后：找到多项则询问，找到一项直接使用，没找到报错。
    7. 将最终选中的路径保存到缓存。

    返回
    -----
    str
        可用的 java.exe 绝对路径。
    """
    if configured_path:
        abs_path = os.path.abspath(configured_path)
        if os.path.isfile(abs_path):
            return abs_path

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
        print(f"[Java] 在本地记录中找到 {len(valid_saved)} 个可用的 Java")
        selected = _interactive_select(valid_saved)
    else:
        need_scan = True

    if need_scan:
        print("[Java] 未找到本地可用 Java 记录，正在扫描系统...")
        found = detect_java_versions()
        if not found:
            raise FileNotFoundError(
                "未在系统中找到任何 Java 安装。\n"
                "  请先安装 JDK 21 或更高版本：https://adoptium.net/"
            )

        if len(found) == 1:
            selected = found[0]
            print(f"[Java] 自动发现: {selected.path} (版本 {selected.version})")
        else:
            selected = _interactive_select(found)

        # 合并扫描结果到缓存（保留之前记录中仍然有效但本次未扫到的路径）
        seen_paths = {os.path.normpath(j.path) for j in found}
        extra = [j for j in saved if os.path.isfile(j.path)
                 and os.path.normpath(j.path) not in seen_paths]
        merged = found + extra
        save_java_list(merged, storage_dir, last_selected=selected.path)
    else:
        # 更新 last_selected
        save_java_list(saved, storage_dir, last_selected=selected.path)

    return selected.path


# =============================================================================
# 服务器启动模块
# =============================================================================


def start_minecraft_server(
    java_path: str,
    jar_path: str,
    min_mem: str = "1G",
    max_mem: str = "16G",
    nogui: bool = True,
    workdir: Optional[str] = None,
    extra_jvm_args: Optional[list[str]] = None,
    extra_server_args: Optional[list[str]] = None,
) -> int:
    """
    启动 Minecraft 服务器（非交互模式，仅输出日志）。

    参数
    ----------
    java_path : str
        Java 可执行文件的路径（如 jdk-21.0.9/bin/java.exe）。
    jar_path : str
        服务端核心 jar 文件的路径（如 leaves.jar）。
    min_mem : str
        最小堆内存，默认 "1G"。
    max_mem : str
        最大堆内存，默认 "16G"。
    nogui : bool
        是否使用 nogui 模式，默认 True。
    workdir : str, optional
        服务器工作目录，默认使用 jar 所在目录。
    extra_jvm_args : list[str], optional
        额外的 JVM 参数。
    extra_server_args : list[str], optional
        额外的服务器参数。

    返回
    -------
    int
        服务器进程的退出码。

    异常
    ------
    FileNotFoundError
        java_path 或 jar_path 不存在。
    """
    java_path = os.path.abspath(java_path)
    jar_path = os.path.abspath(jar_path)

    if not os.path.isfile(java_path):
        raise FileNotFoundError(f"Java 可执行文件未找到: {java_path}")
    if not os.path.isfile(jar_path):
        raise FileNotFoundError(f"服务端核心文件未找到: {jar_path}")

    if workdir is None:
        workdir = os.path.dirname(jar_path)

    jvm_opens = [
        "--add-opens", "java.base/java.lang=ALL-UNNAMED",
        "--add-opens", "java.base/java.lang.reflect=ALL-UNNAMED",
        "--add-opens", "java.base/java.util=ALL-UNNAMED",
        "--add-opens", "java.base/java.io=ALL-UNNAMED",
        "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
    ]

    cmd = [java_path]
    cmd.append(f"-Xmx{max_mem}")
    cmd.append(f"-Xms{min_mem}")
    cmd.extend(jvm_opens)
    if extra_jvm_args:
        cmd.extend(extra_jvm_args)
    cmd.append("-jar")
    cmd.append(jar_path)
    if nogui:
        cmd.append("nogui")
    if extra_server_args:
        cmd.extend(extra_server_args)

    print(f"[启动] 工作目录: {workdir}")
    print(f"[启动] Java:    {java_path}")
    print(f"[启动] 核心:    {jar_path}")
    print(f"[启动] 内存:    最小 {min_mem} / 最大 {max_mem}")
    print(f"[启动] 命令:    {' '.join(cmd)}")
    print()

    process = subprocess.Popen(
        cmd,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )

    def _handle_sigint(sig, frame):
        print("\n[关闭] 收到 Ctrl+C，正在停止服务器...")
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        sys.exit(0)

    original_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _handle_sigint)

    try:
        for line in process.stdout:
            print(line, end="")
    except KeyboardInterrupt:
        _handle_sigint(None, None)

    process.wait()
    signal.signal(signal.SIGINT, original_sigint)

    return process.returncode


def start_server_interactive(
    java_path: str,
    jar_path: str,
    min_mem: str = "1G",
    max_mem: str = "16G",
    extra_jvm_args: Optional[list[str]] = None,
    extra_server_args: Optional[list[str]] = None,
) -> int:
    """
    启动 Minecraft 服务器并支持控制台交互输入（如 stop、say 等命令）。

    参数与 start_minecraft_server 一致，区别在于将 stdin 传给子进程，
    使得用户可以在终端输入服务器指令。
    """
    java_path = os.path.abspath(java_path)
    jar_path = os.path.abspath(jar_path)

    if not os.path.isfile(java_path):
        raise FileNotFoundError(f"Java 可执行文件未找到: {java_path}")
    if not os.path.isfile(jar_path):
        raise FileNotFoundError(f"服务端核心文件未找到: {jar_path}")

    workdir = os.path.dirname(jar_path)

    jvm_opens = [
        "--add-opens", "java.base/java.lang=ALL-UNNAMED",
        "--add-opens", "java.base/java.lang.reflect=ALL-UNNAMED",
        "--add-opens", "java.base/java.util=ALL-UNNAMED",
        "--add-opens", "java.base/java.io=ALL-UNNAMED",
        "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
    ]

    cmd = [java_path]
    cmd.append(f"-Xmx{max_mem}")
    cmd.append(f"-Xms{min_mem}")
    cmd.extend(jvm_opens)
    if extra_jvm_args:
        cmd.extend(extra_jvm_args)
    cmd.append("-jar")
    cmd.append(jar_path)
    cmd.append("nogui")
    if extra_server_args:
        cmd.extend(extra_server_args)

    print(f"[启动] 工作目录: {workdir}")
    print(f"[启动] Java:    {java_path}")
    print(f"[启动] 核心:    {jar_path}")
    print(f"[启动] 内存:    最小 {min_mem} / 最大 {max_mem}")
    print("[启动] 控制台交互已启用（输入 stop 关闭服务器）")
    print()

    return subprocess.call(cmd, cwd=workdir)
