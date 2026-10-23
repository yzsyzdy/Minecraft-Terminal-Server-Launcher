"""
开机自启动管理 (Windows)

通过写入注册表 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
实现当前用户的开机自启动。非 Windows 平台返回 False 不做任何操作。

使用零依赖方案: 标准库 winreg (Windows 专有模块)。
"""

import os
import sys

from paths import exe_path, is_packaged

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "MSTL"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def is_supported() -> bool:
    """当前平台能不能管开机自启。面板拿它决定这个开关能不能点。"""
    return _is_windows()


def _startup_command() -> str:
    """构建注册表中存储的启动命令。取不到可靠路径时返回空串。

    打包模式 (Nuitka/PyInstaller): 指向用户启动的那个 exe。exe 路径一律从
    paths.exe_path() 取——它认得 Nuitka 记下的原始 exe，而 sys.executable
    在 standalone 下指向 dist 里并不存在的 python.exe。
    源码模式: python.exe + start.py 的绝对路径。

    判断打包与否必须走 is_packaged()：Nuitka 的 standalone 不设置
    sys.frozen，只看它的话打包版会掉进下面的源码分支，写出
    "<dist>\\python.exe" "<dist>\\start.py" —— 这两个文件在 dist 里都不存在，
    注册表却照样写入成功、界面还显示「已启用」。
    """
    if is_packaged():
        real = exe_path()
        if real:
            return f'"{real}"'
        # 取不到就交白卷，让 set_startup_enabled 返回失败：写一条指向
        # 不存在文件的命令，比明确失败更糟——用户会以为已经生效了
        return ""
    python = sys.executable
    start_py = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "start.py")
    return f'"{python}" "{start_py}"'


def set_startup_enabled(enabled: bool) -> bool:
    """启用/禁用开机自启动。成功返回 True，失败返回 False。"""
    if not _is_windows():
        return False
    command = _startup_command()
    if enabled and not command:
        # 拼不出可靠命令就明确失败，别往注册表里塞空值
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, APP_NAME, 0, winreg.REG_SZ, command)
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
