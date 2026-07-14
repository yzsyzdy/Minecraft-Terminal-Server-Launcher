"""
MSTL — Server launch module

Provides core functions for starting Minecraft servers (JVM args, process management).
"""

import subprocess
import sys
import os
import signal
import platform
from typing import Optional

from i18n import t


def console_title(title: str) -> None:
    """Set console window title (Windows only)."""
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
    """Validate Java and jar files, return working directory."""
    java_path = os.path.abspath(java_path)
    jar_path = os.path.abspath(jar_path)
    if not os.path.isfile(java_path):
        raise FileNotFoundError(t("app.error_java_not_found", path=java_path))
    if not os.path.isfile(jar_path):
        raise FileNotFoundError(t("app.error_jar_not_found", path=jar_path))
    return os.path.dirname(jar_path)


def _build_jvm_cmd(
    java_path: str, jar_path: str, min_mem: str, max_mem: str,
    extra_jvm_args: Optional[list[str]] = None,
    extra_server_args: Optional[list[str]] = None,
    nogui: bool = True,
) -> list[str]:
    """Build the full JVM command list."""
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
    """Start server (non-interactive, log output only). Returns exit code."""
    console_title(t("console.title_noinput"))
    workdir = _validate_java_jar(java_path, jar_path) if workdir is None else os.path.abspath(workdir)
    cmd = _build_jvm_cmd(java_path, jar_path, min_mem, max_mem,
                         extra_jvm_args, extra_server_args, nogui)

    print(t("launch.workdir", dir=workdir))
    print(t("launch.java", path=os.path.abspath(java_path)))
    print(t("launch.jar", path=os.path.abspath(jar_path)))
    print(t("launch.memory", min=min_mem, max=max_mem))
    print(t("launch.command", cmd=" ".join(cmd)))
    print()

    process = subprocess.Popen(
        cmd, cwd=workdir, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, encoding="utf-8", bufsize=1,
    )

    def _handle_sigint(sig, frame):
        print(t("launch.sigint"))
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        os._exit(0)

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
    """Start server with interactive console input."""
    console_title(t("console.title"))
    workdir = _validate_java_jar(java_path, jar_path)
    cmd = _build_jvm_cmd(java_path, jar_path, min_mem, max_mem,
                         extra_jvm_args, extra_server_args, nogui=True)

    print(t("launch.workdir", dir=workdir))
    print(t("launch.java", path=os.path.abspath(java_path)))
    print(t("launch.jar", path=os.path.abspath(jar_path)))
    print(t("launch.memory", min=min_mem, max=max_mem))
    print(t("launch.interactive"))
    print()
    return subprocess.call(cmd, cwd=workdir)
