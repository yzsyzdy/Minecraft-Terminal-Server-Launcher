"""端口探活。

面板要回答一个很小的问题：「这个实例说它监听 6185，现在真的通吗」。不需要
跑 `netstat` 再解析（Windows 的列名会跟语言变），直接连一下最省事：本机连接
几毫秒就返回，连不上也只会等到很短的超时。
"""

from __future__ import annotations

import socket

DEFAULT_TIMEOUT = 0.25


def port_open(port: int, host: str = "127.0.0.1",
              timeout: float = DEFAULT_TIMEOUT) -> bool:
    """TCP 连得上就算在监听；端口非法或连不上都返回 False。"""
    if not isinstance(port, int) or not (0 < port <= 65535):
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


__all__ = ["port_open", "DEFAULT_TIMEOUT"]
