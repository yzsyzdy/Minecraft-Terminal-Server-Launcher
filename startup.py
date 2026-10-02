"""
开机自启动管理 (Windows)

通过写入注册表 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
实现当前用户的开机自启动。非 Windows 平台返回 False 不做任何操作。

使用零依赖方案: 标准库 winreg (Windows 专有模块)。
"""

import os
import sys

from paths import exe_dir, is_temp_onefile_dir

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "MSTL"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _startup_command() -> str:
    """构建注册表中存储的启动命令。

    打包模式 (Nuitka/PyInstaller): 指向 exe。
    注意 onefile 模式下 sys.executable/sys.argv[0] 指向临时解压目录
    （退出即删除），必须用原始 exe 路径。
    源码模式: python.exe + start.py 的绝对路径。
    """
    if getattr(sys, "frozen", False):
        # 这里必须用 exe_dir() 而不是 original_exe_dir()：后者会把
        # MSTL_DATA_DIR（用户指定的数据目录）当成 exe 目录返回，拼出来的
        # 路径下并没有 exe。注册表照样写入成功、设置页也显示「已启用」，
        # 但 Windows 到开机时只会静默跳过一条指向不存在文件的命令。
        real_dir = exe_dir()
        if real_dir:
            candidate = os.path.join(real_dir, os.path.basename(sys.executable))
            if os.path.isfile(candidate):
                return f'"{candidate}"'

        argv0 = os.path.abspath(sys.argv[0])
        argv0_dir = os.path.dirname(argv0)
        if (os.path.isfile(argv0) and os.path.isdir(argv0_dir)
                and os.access(argv0_dir, os.W_OK)
                and not is_temp_onefile_dir(argv0_dir)):
            return f'"{argv0}"'
        return f'"{sys.executable}"'
    python = sys.executable
    start_py = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "start.py")
    return f'"{python}" "{start_py}"'


def set_startup_enabled(enabled: bool) -> bool:
    """启用/禁用开机自启动。成功返回 True，失败返回 False。"""
    if not _is_windows():
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, APP_NAME, 0, winreg.REG_SZ, _startup_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass  # 键不存在也视为成功
        return True
    except OSError:
        return False


def is_startup_enabled() -> bool:
    """检查当前是否已配置开机自启动。

    要和当前应该写入的命令比对，而不是只看这个值存不存在：exe 挪过位置、
    或者数据目录换过之后，注册表里会留着一条指向旧路径的命令，只看存在性
    会一直显示「已启用」，而开机时什么都不会发生。
    """
    if not _is_windows():
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ,
        ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
    except FileNotFoundError:
        return False
    except OSError:
        return False
    return str(value).strip() == _startup_command().strip()
