"""
MSTL — Server launch module

Provides core functions for starting Minecraft servers
(JVM args, process management, priority control).
"""

import os
import platform
import signal
import subprocess
import sys
from typing import Optional

from i18n import t

# Module-level ctypes import (Windows only)
_WINDOWS_KERNEL32 = None
if platform.system() == "Windows":
    try:
        import ctypes
        _WINDOWS_KERNEL32 = ctypes.windll.kernel32
    except Exception:
        pass

# Priority class constants (Windows)
# https://docs.microsoft.com/en-us/windows/win32/procthread/priority-class
_PRIORITY_CLASS = {
    "normal":       0x0020,   # NORMAL_PRIORITY_CLASS
    "below_normal": 0x4000,   # BELOW_NORMAL_PRIORITY_CLASS
    "low":          0x0040,   # IDLE_PRIORITY_CLASS
}
# PROCESS_MODE_BACKGROUND_BEGIN (Windows 8+, lowers I/O priority)
_BG_BEGIN = 0x00100000


def _set_priority_windows(handle: int, creationflags: int,
                          priority: str) -> int:
    """Apply Windows priority and I/O background mode.

    Returns the effective creationflags.
    """
    base_class = creationflags
    if base_class == 0 and priority in _PRIORITY_CLASS:
        base_class = _PRIORITY_CLASS[priority]

    if _WINDOWS_KERNEL32 is None or priority == "normal":
        return base_class

    # Set the priority class
    _WINDOWS_KERNEL32.SetPriorityClass(handle, base_class)

    # For 'low' also enable background I/O mode so the process
    # is throttled on disk access as well.
    if priority == "low":
        try:
            _WINDOWS_KERNEL32.SetPriorityClass(handle, _BG_BEGIN)
        except Exception:
            pass

    return base_class


def _start_priority_process(
    cmd: list[str], cwd: str, priority: str,
    **popen_kw,
) -> subprocess.Popen:
    """Start a subprocess with the given priority hint.

    On Windows this sets creationflags + PostCreation SetPriorityClass.
    On Unix/macOS it applies os.nice() via preexec_fn.
    """
    creationflags = 0
    preexec_fn = None
    system = platform.system()

    if system == "Windows" and priority in _PRIORITY_CLASS:
        creationflags = _PRIORITY_CLASS[priority]

    if system != "Windows" and priority != "normal":
        nice_val = {"below_normal": 5, "low": 10}.get(priority, 0)
        if nice_val:
            preexec_fn = lambda: os.nice(nice_val)

    proc = subprocess.Popen(
        cmd, cwd=cwd, creationflags=creationflags,
        preexec_fn=preexec_fn, **popen_kw,
    )

    # Post-creation adjustment on Windows
    if system == "Windows" and priority != "normal":
        _set_priority_windows(proc._handle, creationflags, priority)

    return proc


def console_title(title: str) -> None:
    """Set console window title (Windows only)."""
    if _WINDOWS_KERNEL32 is not None:
        try:
            _WINDOWS_KERNEL32.SetConsoleTitleW(title)
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
        raise FileNotFoundError(
            t("app.error_java_not_found", path=java_path))
    if not os.path.isfile(jar_path):
        raise FileNotFoundError(
            t("app.error_jar_not_found", path=jar_path))
    return os.path.dirname(jar_path)


def _build_jvm_cmd(
    java_path: str,
    jar_path: str,
    min_mem: str,
    max_mem: str,
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


def _print_launch_info(
    workdir: str, java_path: str, jar_path: str,
    min_mem: str, max_mem: str, cmd: list[str],
    interactive: bool = False,
) -> None:
    """Print launch info summary."""
    print(t("launch.workdir", dir=workdir))
    print(t("launch.java", path=os.path.abspath(java_path)))
    print(t("launch.jar", path=os.path.abspath(jar_path)))
    print(t("launch.memory", min=min_mem, max=max_mem))
    if interactive:
        print(t("launch.interactive"))
    else:
        print(t("launch.command", cmd=" ".join(cmd)))
    print()


def start_minecraft_server(
    java_path: str,
    jar_path: str,
    min_mem: str = "1G",
    max_mem: str = "16G",
    nogui: bool = True,
    workdir: Optional[str] = None,
    extra_jvm_args: Optional[list[str]] = None,
    extra_server_args: Optional[list[str]] = None,
    process_priority: str = "normal",
) -> int:
    """Start server (non-interactive, log output only). Returns exit code."""
    console_title(t("console.title_noinput"))
    workdir = _validate_java_jar(java_path, jar_path) \
        if workdir is None else os.path.abspath(workdir)
    cmd = _build_jvm_cmd(
        java_path, jar_path, min_mem, max_mem,
        extra_jvm_args, extra_server_args, nogui,
    )
    _print_launch_info(workdir, java_path, jar_path,
                       min_mem, max_mem, cmd)

    process = _start_priority_process(
        cmd, workdir, process_priority,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", bufsize=1,
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
    process_priority: str = "normal",
) -> int:
    """Start server with interactive console input."""
    console_title(t("console.title"))
    workdir = _validate_java_jar(java_path, jar_path)
    cmd = _build_jvm_cmd(
        java_path, jar_path, min_mem, max_mem,
        extra_jvm_args, extra_server_args, nogui=True,
    )
    _print_launch_info(workdir, java_path, jar_path,
                       min_mem, max_mem, cmd, interactive=True)

    process = _start_priority_process(
        cmd, workdir, process_priority)
    process.wait()
    return process.returncode
