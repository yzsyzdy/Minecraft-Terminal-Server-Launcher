"""
终端清屏工具
"""
import os


def clear_screen() -> None:
    """清空终端屏幕。"""
    os.system("cls" if os.name == "nt" else "clear")
