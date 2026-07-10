"""
Minecraft 服务器启动模块

提供启动服务器的核心函数（JVM 参数、进程管理）。
"""

import subprocess
import sys
import os
import signal
from typing import Optional


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
    """启动 Minecraft 服务器（非交互模式，仅输出日志）。返回进程退出码。"""
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

    cmd = [java_path, f"-Xmx{max_mem}", f"-Xms{min_mem}"] + jvm_opens
    if extra_jvm_args:
        cmd.extend(extra_jvm_args)
    cmd += ["-jar", jar_path]
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

    cmd = [java_path, f"-Xmx{max_mem}", f"-Xms{min_mem}"] + jvm_opens
    if extra_jvm_args:
        cmd.extend(extra_jvm_args)
    cmd += ["-jar", jar_path, "nogui"]
    if extra_server_args:
        cmd.extend(extra_server_args)

    print(f"[启动] 工作目录: {workdir}")
    print(f"[启动] Java:    {java_path}")
    print(f"[启动] 核心:    {jar_path}")
    print(f"[启动] 内存:    最小 {min_mem} / 最大 {max_mem}")
    print("[启动] 控制台交互已启用（输入 stop 关闭服务器）")
    print()
    return subprocess.call(cmd, cwd=workdir)
