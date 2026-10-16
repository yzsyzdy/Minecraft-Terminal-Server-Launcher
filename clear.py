"""
终端清屏工具
"""
import os
import sys

# Windows 10 以前的控制台不认 ANSI 转义码，会把 "\033[H\033[J" 原样打到屏幕上：
# 不但没清屏，还多出一行乱码。ENABLE_VIRTUAL_TERMINAL_PROCESSING 是让经典
# 控制台开始支持 ANSI 的那个开关；能打开就继续用转义码（不建子进程），
# 打不开再退回 cls / clear。探测结果缓存下来，菜单里每次刷新都要调它。
_VT_SUPPORTED = None


def _supports_ansi() -> bool:
    global _VT_SUPPORTED
    if _VT_SUPPORTED is not None:
        return _VT_SUPPORTED
    if os.name != "nt":
        _VT_SUPPORTED = True
        return _VT_SUPPORTED
    _VT_SUPPORTED = False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            _VT_SUPPORTED = bool(
                kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:
        _VT_SUPPORTED = False
    return _VT_SUPPORTED


def clear_screen() -> None:
    """清空终端屏幕（优先 ANSI 转义码，不支持的终端退回系统命令）。"""
    # 输出被重定向到文件或管道时没有「屏幕」可清，cls/clear 只会打印报错。
    if not sys.stdout.isatty():
        return
    if _supports_ansi():
        # \033[H 将光标移动到左上角，\033[J 清除从光标到屏幕末尾的内容
        # 比 os.system("cls"/"clear") 更快、更安全，不创建子进程
        print("\033[H\033[J", end="")
        # 不 flush 的话菜单重绘前清屏可能还压在缓冲区里，画面会花
        sys.stdout.flush()
        return
    os.system("cls" if os.name == "nt" else "clear")
