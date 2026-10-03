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


def is_temp_onefile_dir(path: str) -> bool:
    """公开版本，供启动项等模块复用同一套判断。"""
    return _is_temp_onefile_dir(path)


def _nuitka_info():
    """Nuitka 会往它编译的每个模块里注入 `__compiled__`（1.9+），没有则返回 None。"""
    return globals().get("__compiled__")


def is_packaged() -> bool:
    """当前是不是打包后的 exe 在跑。

    **不能只看 `sys.frozen`**：Nuitka 的 standalone 根本不设置它（实测在
    `--standalone` 产物里 `getattr(sys, "frozen", False)` 为假）。原来所有
    判断都写成 `if sys.frozen`，于是打包版里这些分支一次都走不到，后果是：

      - `MSTL_DATA_DIR` 被无视，数据一律落在 exe 旁边
      - 开机自启写出 `"<dist>\\python.exe" "<dist>\\start.py"` 这种命令，
        而这两个文件在 dist 里都不存在（standalone 下 `sys.executable`
        指向的就是那个不存在的 python.exe）

    PyInstaller 以及 Nuitka onefile 会设置 `sys.frozen`，这里一并认。
    """
    if getattr(sys, "frozen", False):
        return True
    return _nuitka_info() is not None


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


def exe_path() -> str:
    """获取用户启动的那个 exe 的完整路径。取不到返回空串。

    只认「真实存在、且不在 onefile 临时解压目录里」的候选。宁可返回空串让
    调用方报错，也不要拼出一条开机时必然跑不起来的命令。

    不要直接拿 sys.executable 当 exe 路径：Nuitka standalone 下它指向
    dist 里的 python.exe，而 dist 里并没有这个文件（真正的二进制叫
    MSTL.exe，旁边只有 python313.dll）。
    """
    if not is_packaged():
        return ""

    candidates = []

    # Nuitka 把「用户启动的那个 exe」原样记在 __compiled__.original_argv0，
    # standalone 与 onefile 都可用，这是最直接的一条。
    info = _nuitka_info()
    if info is not None:
        candidates.append(str(getattr(info, "original_argv0", "") or ""))

    # bootstrap 设置的环境变量（若尚未被 unset）
    for var in ("NUITKA_ORIGINAL_ARGV0", "NUITKA_ONEFILE_ARGV0"):
        candidates.append(os.environ.get(var, "") or "")

    # standalone 下 sys.argv[0] 就是 exe 本身
    candidates.append(sys.argv[0] if sys.argv else "")

    for path in candidates:
        if not path:
            continue
        full = os.path.abspath(path)
        if not os.path.isfile(full):
            continue
        if _is_temp_onefile_dir(os.path.dirname(full)):
            continue
        return full

    # 通过 bootstrap PID 反查原始 exe 路径（onefile 下前面几条都会落空）
    parent_pid = os.environ.get("NUITKA_ONEFILE_PARENT", "") or ""
    if parent_pid.isdigit():
        parent_exe = _parent_exe_path(int(parent_pid))
        if parent_exe and os.path.isfile(parent_exe):
            return os.path.abspath(parent_exe)

    # sys.executable 放最后，而且要求文件真的存在（standalone 下它不存在）
    exe = os.path.abspath(sys.executable or "")
    if (exe and os.path.isfile(exe)
            and not _is_temp_onefile_dir(os.path.dirname(exe))):
        return exe

    return ""


def exe_dir() -> str:
    """获取用户启动的那个 exe 所在的真实目录（只看 exe，不含数据目录）。

    与 original_exe_dir() 的区别：MSTL_DATA_DIR 是「数据放哪」的开关，
    它指向的目录里并没有 exe。注册开机自启动这类要拼 exe 路径的场景必须
    用本函数，否则会把数据目录当成 exe 目录，拼出一个根本不存在的路径——
    注册表写进去了，Windows 到开机时也只是静默跳过。

    返回空串表示不可用（非打包模式或查不到）。
    """
    path = exe_path()
    return os.path.dirname(path) if path else ""


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

    return exe_dir()


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
    # MSTL_DATA_DIR 是用户显式指定的数据目录，优先级最高，**且与是否打包无关**。
    # 它原来藏在对 sys.frozen 的判断里面，而 Nuitka standalone 不设置
    # sys.frozen，于是打包版里这个开关完全失效——用户把数据目录指到别处，
    # 数据还是照旧落在 exe 旁边。
    explicit = os.environ.get("MSTL_DATA_DIR", "") or ""
    if explicit:
        return os.path.abspath(explicit)

    if is_packaged():
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
