"""
路径工具模块

提供跨模式（源码 / Nuitka standalone / Nuitka onefile / PyInstaller）的
路径定位函数，集中处理打包环境下的路径差异。

背景:
  Nuitka onefile 模式运行时会把自身解压到 %TEMP%/onefile_xxx 临时目录，
  且 child 进程的 sys.argv[0]、sys.executable 都被改写为临时二进制路径，
  原始 exe 路径不可从 sys 直接获得。

  可靠来源（Nuitka 4.x）:
    - MSTL_DATA_DIR 环境变量: 用户显式指定的数据目录（最高优先级）
    - NUITKA_ORIGINAL_ARGV0 环境变量: bootstrap 设置，但 Python 启动时
      已被 unset（不可靠，仅作兜底）
    - NUITKA_ONEFILE_PARENT: bootstrap 进程 PID，bootstrap 在 child 运行期间
      一直存活（WaitForSingleObject），可通过系统 API 查询其 exe 路径，
      该路径即用户启动的原始 exe 路径（跨平台方案）。
"""

import os
import sys


def _is_temp_onefile_dir(path: str) -> bool:
    """判断路径是否为 Nuitka onefile 临时解压目录。"""
    base = os.path.basename(os.path.normpath(path) or ".")
    return base.startswith("onefile_") or base.startswith("onefile-")


def _parent_exe_path(pid: int) -> str:
    """通过进程 PID 查询其可执行文件路径（Windows + Linux）。失败返回 ''。"""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(4096)
                size = wintypes.DWORD(4096)
                if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                        handle, 0, buf, ctypes.byref(size)):
                    return buf.value
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
            return ""
        else:
            # Linux / macOS: /proc/<pid>/exe 符号链接
            link = f"/proc/{pid}/exe"
            if os.path.exists(link):
                return os.readlink(link)
            return ""
    except (OSError, AttributeError, ImportError):
        return ""


def original_exe_dir() -> str:
    """获取用户启动 exe 所在的原始目录（onefile 模式专用）。

    返回空串表示不可用。优先级:
      1. MSTL_DATA_DIR 环境变量（用户显式指定的数据目录）
      2. NUITKA_ORIGINAL_ARGV0（bootstrap 设置，若尚未被 unset）
      3. NUITKA_ONEFILE_PARENT 反查 bootstrap 进程 exe 路径
    """
    # 1. 用户显式指定（最高优先级，不受 Nuitka unset 影响）
    explicit = os.environ.get("MSTL_DATA_DIR", "") or ""
    if explicit:
        return os.path.abspath(explicit)

    # 2. bootstrap 设置的环境变量（若尚未被 unset）
    for var in ("NUITKA_ORIGINAL_ARGV0", "NUITKA_ONEFILE_ARGV0"):
        env_path = os.environ.get(var, "") or ""
        if env_path and os.path.isfile(env_path):
            return os.path.dirname(os.path.abspath(env_path))

    # 3. 通过 bootstrap PID 反查原始 exe 路径
    parent_pid = os.environ.get("NUITKA_ONEFILE_PARENT", "") or ""
    if parent_pid.isdigit():
        parent_exe = _parent_exe_path(int(parent_pid))
        if parent_exe and os.path.isfile(parent_exe):
            return os.path.dirname(os.path.abspath(parent_exe))

    return ""


def app_dir() -> str:
    """获取项目数据目录（config.json、servers/ 等用户数据的存放位置）。

    优先级:
      1. MSTL_DATA_DIR 环境变量（用户显式指定）
      2. onefile 模式: 原始 exe 所在目录（bootstrap PID 反查）
      3. frozen 模式: argv[0] 目录（standalone 下正确）
      4. 当前工作目录（cmd 中运行时通常就是 exe 所在目录）
      5. sys.executable 目录（最后兜底）
      6. 源码模式: 本文件所在目录
    """
    if getattr(sys, "frozen", False):
        orig_dir = original_exe_dir()
        if orig_dir:
            return orig_dir
        argv0_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        if (os.path.isdir(argv0_dir) and os.access(argv0_dir, os.W_OK)
                and not _is_temp_onefile_dir(argv0_dir)):
            return argv0_dir
        cwd = os.getcwd()
        if (os.path.isdir(cwd) and os.access(cwd, os.W_OK)
                and not _is_temp_onefile_dir(cwd)):
            return cwd
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundled_dir() -> str:
    """获取嵌入资源目录（locales/、version.json 等只读资源）。

    嵌入资源解压后与 __file__ 同目录：onefile 模式在临时解压目录，
    standalone 在 .dist 目录，源码模式在项目根。
    """
    return os.path.dirname(os.path.abspath(__file__))
