"""
core —— MSTL 的业务核心层。

这一层不认识终端，也不认识浏览器：不 print、不 input，
只做数据与文件的事，把「要说的话」和「要问的事」交给界面层。

界面层目前有三个，将来还会有 TUI 与 Web 面板，它们都只依赖 core。
"""

from .errors import CoreError
from .report import NULL, NullReporter, Reporter

__all__ = ["CoreError", "Reporter", "NullReporter", "NULL"]
