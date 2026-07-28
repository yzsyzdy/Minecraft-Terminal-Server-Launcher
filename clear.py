"""
终端清屏工具
"""
import sys


def clear_screen() -> None:
    """清空终端屏幕（使用 ANSI 转义码，跨平台兼容）。"""
    # \033[H 将光标移动到左上角，\033[J 清除从光标到屏幕末尾的内容
    # 比 os.system("cls"/"clear") 更快、更安全，不创建子进程
    print("\033[H\033[J", end="")
