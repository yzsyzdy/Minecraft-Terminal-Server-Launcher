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

# ---------------------------------------------------------------------------
# Windows 内核 API 句柄 (模块级一次性加载)
# ctypes 的 windll 只能在 Windows 上导入，放在模块顶部避免每次调用都 try
# ---------------------------------------------------------------------------
_WINDOWS_KERNEL32 = None
if platform.system() == "Windows":
    try:
        import ctypes
        _WINDOWS_KERNEL32 = ctypes.windll.kernel32
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 进程优先级常量 (Windows)
# 参考: https://docs.microsoft.com/en-us/windows/win32/procthread/priority-class
# ---------------------------------------------------------------------------
_PRIORITY_CLASS = {
    "normal":       0x0020,   # NORMAL_PRIORITY_CLASS  — 默认
    "below_normal": 0x4000,   # BELOW_NORMAL_PRIORITY_CLASS — 不影响 MC 帧率
    "low":          0x0040,   # IDLE_PRIORITY_CLASS    — 只在 CPU 空闲时运行
}
# PROCESS_MODE_BACKGROUND_BEGIN (Windows 8+, 同时降低 I/O 优先级)
_BG_BEGIN = 0x00100000


def _set_priority_windows(handle: int, creationflags: int,
                          priority: str) -> int:
    """在 Windows 上设置进程优先级和 I/O 后台模式。

    两种方式配合使用:
    1. creationflags — 进程创建时指定优先级类
    2. SetPriorityClass — 创建后用 ctypes 补充设置

    对 "low" 优先级额外启用后台 I/O 模式（磁盘访问也会被限速）。
    返回最终的 creationflags 值。
    """
    base_class = creationflags
    if base_class == 0 and priority in _PRIORITY_CLASS:
        base_class = _PRIORITY_CLASS[priority]

    if _WINDOWS_KERNEL32 is None or priority == "normal":
        return base_class

    _WINDOWS_KERNEL32.SetPriorityClass(handle, base_class)

    # 只有 "low" 才启用_BG_BEGIN，否则 below_normal 会被意外覆盖为 idle
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
    """按指定优先级启动子进程。

    Windows 通过 creationflags 和 SetPriorityClass 双重保证。
    Unix/macOS 通过 preexec_fn 在 fork 后调用 os.nice()。
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

    # Windows 上 creationflags 只在 CreateProcess 时生效，
    # SetPriorityClass 可以覆盖后续调整
    if system == "Windows" and priority != "normal":
        _set_priority_windows(proc._handle, creationflags, priority)

    return proc


def console_title(title: str) -> None:
    """设置控制台窗口标题 (仅 Windows)。"""
    if _WINDOWS_KERNEL32 is not None:
        try:
            _WINDOWS_KERNEL32.SetConsoleTitleW(title)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# JVM 参数构建
# ---------------------------------------------------------------------------

# 必要模块开放: Mojang 的混淆代码用反射访问平台模块
_JVM_OPENS = [
    "--add-opens", "java.base/java.lang=ALL-UNNAMED",
    "--add-opens", "java.base/java.lang.reflect=ALL-UNNAMED",
    "--add-opens", "java.base/java.util=ALL-UNNAMED",
    "--add-opens", "java.base/java.io=ALL-UNNAMED",
    "--add-opens", "java.base/sun.nio.ch=ALL-UNNAMED",
]


def _validate_java_jar(java_path: str, jar_path: str) -> str:
    """验证 Java 和核心 jar 是否存在，返回工作目录。"""
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
    """构建完整的 JVM 命令行参数列表。

    参数顺序不可随意调换:
      java -Xmx -Xms --add-opens ... -jar jar nogui [extra]
    """
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
    """打印启动摘要信息。"""
    print(t("launch.workdir", dir=workdir))
    print(t("launch.java", path=os.path.abspath(java_path)))
    print(t("launch.jar", path=os.path.abspath(jar_path)))
    print(t("launch.memory", min=min_mem, max=max_mem))
    if interactive:
        print(t("launch.interactive"))
    else:
        print(t("launch.command", cmd=" ".join(cmd)))
    print()


# ---------------------------------------------------------------------------
# SIGINT 处理器（统一实现，避免重复注册）
# ---------------------------------------------------------------------------

from contextlib import contextmanager


@contextmanager
def _sigint_handler(process: subprocess.Popen):
    """上下文管理器：注册 SIGINT 处理器，退出时自动恢复。

    在 with 块内，Ctrl+C 会优雅终止子进程并抛出 KeyboardInterrupt。
    退出 with 块后自动恢复原有的 SIGINT 处理器。
    """
    def _handle_sigint(sig, frame):
        print(t("launch.sigint"))
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        raise KeyboardInterrupt()

    original_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _handle_sigint)
    try:
        yield
    except KeyboardInterrupt:
        pass  # 已终止子进程，只需等待真正的退出
    finally:
        signal.signal(signal.SIGINT, original_sigint)


# ---------------------------------------------------------------------------
# 非交互模式启动
# ---------------------------------------------------------------------------

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
    """以非交互模式启动服务器（仅输出日志，不接受控制台输入）。

    工作原理:
       1. 验证 Java 和核心 jar 路径
       2. 按优先级配置启动子进程
       3. 逐行读取 stdout 并打印到控制台
       4. 注册 SIGINT 处理器实现优雅关闭
       5. 返回 Minecraft 服务端的退出码

    Returns:
        服务端进程的退出码 (0 = 正常退出)
    """
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

    with _sigint_handler(process):
        try:
            for line in process.stdout:
                print(line, end="")
        except KeyboardInterrupt:
            pass
    process.wait()
    return process.returncode


# ---------------------------------------------------------------------------
# 交互模式启动
# ---------------------------------------------------------------------------

def start_server_interactive(
    java_path: str,
    jar_path: str,
    min_mem: str = "1G",
    max_mem: str = "16G",
    extra_jvm_args: Optional[list[str]] = None,
    extra_server_args: Optional[list[str]] = None,
    process_priority: str = "normal",
) -> int:
    """以交互模式启动服务器（stdin/stdout 直通，可输入命令）。

    交互模式下 MC 服务端默认监听控制台输入（help、stop 等命令），
    所以不能像非交互模式那样用 PIPE 接管 stdout。
    这里让子进程直接继承父进程的 stdin/stdout。

    Returns:
        服务端进程的退出码
    """
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

    with _sigint_handler(process):
        try:
            process.wait()
        except KeyboardInterrupt:
            pass
    return process.returncode
