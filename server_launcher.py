"""
MSTL — 服务器启动模块

提供启动服务器的核心函数（JVM 参数、进程管理）。
"""

import subprocess
import sys
import os
import signal
import platform
from typing import Optional


def console_title(title: str) -> None:
    """设置控制台窗口标题（Windows），其他系统忽略。"""
    if platform.system() == "Windows":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass


_JVM_OPENS = [
    "--add-opens", "java.base/java.lang=ALL-UNNAMED",
    "--add-opens", "java.base/java.lang.reflect=ALL-UNNAMED",
    "--add-opens", "java.base/java.util=ALL-UNNAMED",
    "--add-opens", "java.base/java.io=ALL-UNNAMED",
    "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
]


def _validate_java_jar(java_path: str, jar_path: str) -> str:
    """校验 Java 和 jar 文件，返回工作目录。"""
    java_path = os.path.abspath(java_path)
    jar_path = os.path.abspath(jar_path)
    if not os.path.isfile(java_path):
        raise FileNotFoundError(f"Java 可执行文件未找到: {java_path}")
    if not os.path.isfile(jar_path):
        raise FileNotFoundError(f"服务端核心文件未找到: {jar_path}")
    return os.path.dirname(jar_path)


def _build_jvm_cmd(
    java_path: str, jar_path: str, min_mem: str, max_mem: str,
    extra_jvm_args: Optional[list[str]] = None,
    extra_server_args: Optional[list[str]] = None,
    nogui: bool = True,
) -> list[str]:
    """构建完整的 JVM 启动命令列表。"""
    cmd = [java_path, f"-Xmx{max_mem}", f"-Xms{min_mem}"] + _JVM_OPENS
    if extra_jvm_args:
        cmd.extend(extra_jvm_args)
    cmd += ["-jar", jar_path]
    if nogui:
        cmd.append("nogui")
    if extra_server_args:
        cmd.extend(extra_server_args)
    return cmd


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
    """启动服务器（非交互模式，仅输出日志）。返回进程退出码。"""
    console_title("MSTL — 服务器控制台 (非交互)")
    workdir = _validate_java_jar(java_path, jar_path) if workdir is None else os.path.abspath(workdir)
    cmd = _build_jvm_cmd(java_path, jar_path, min_mem, max_mem,
                         extra_jvm_args, extra_server_args, nogui)

    print(f"[启动] 工作目录: {workdir}")
    print(f"[启动] Java:    {os.path.abspath(java_path)}")
    print(f"[启动] 核心:    {os.path.abspath(jar_path)}")
    print(f"[启动] 内存:    最小 {min_mem} / 最大 {max_mem}")
    print(f"[启动] 命令:    {' '.join(cmd)}")
    print()

    process = subprocess.Popen(
        cmd, cwd=workdir, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", bufsize=1,
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
    """启动服务器并支持控制台交互输入。"""
    console_title("MSTL — 服务器控制台")
    workdir = _validate_java_jar(java_path, jar_path)
    cmd = _build_jvm_cmd(java_path, jar_path, min_mem, max_mem,
                         extra_jvm_args, extra_server_args, nogui=True)

    print(f"[启动] 工作目录: {workdir}")
    print(f"[启动] Java:    {os.path.abspath(java_path)}")
    print(f"[启动] 核心:    {os.path.abspath(jar_path)}")
    print(f"[启动] 内存:    最小 {min_mem} / 最大 {max_mem}")
    print("[启动] 控制台交互已启用（输入 stop 关闭服务器）")
    print()
    return subprocess.call(cmd, cwd=workdir)
