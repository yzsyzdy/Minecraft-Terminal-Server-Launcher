"""「上次在跑哪些实例」的落地记录。

面板托管的实例是子进程。软件重启（或者被关掉、被任务管理器杀掉）之后，那些
子进程要么已经跟着没了，要么成了没人管得到的孤儿——运行时的 stdin 管道在
父进程里，重连不上。所以重启后能做的不是「接回去」，而是「照上次的样子再拉
一遍」。

这里只存两样东西：实例名和一个 pid。

- 不存配置：配置在各自实例目录的 `.server.json` 里，这里再存一份就会过期，
  而且改配置时两边都可能被写。
- 存 pid：重启时如果那个 pid 还活着，说明上一轮的子进程没被带走，这时候再
  拉起一遍只会撞端口，所以宁可跳过并且从记录里抹掉。

写盘走「临时文件 + os.replace」，和 config.py 一样：中途崩了不会留下半截
JSON，读不出来的记录当成空的，最多丢一次恢复。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any

STATE_NAME = ".mstl-running.json"

_lock = threading.Lock()


def state_path(project_dir: str) -> str:
    return os.path.join(project_dir, STATE_NAME)


def load(project_dir: str) -> dict[str, dict[str, Any]]:
    """读记录。文件不在、读不动、内容不对都返回空字典。"""
    path = state_path(project_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    items = data.get("instances") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, info in items.items():
        if isinstance(name, str) and name and isinstance(info, dict):
            out[name] = info
    return out


def _write(project_dir: str, items: dict[str, dict[str, Any]]) -> None:
    """覆盖写。失败就当没这回事——恢复功能不该让启动流程挂掉。"""
    path = state_path(project_dir)
    if not items:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    payload = {"version": 1, "instances": items}
    tmp = path + ".tmp"
    try:
        os.makedirs(project_dir, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def remember(project_dir: str, name: str, pid: Any = None) -> None:
    if not name:
        return
    with _lock:
        items = load(project_dir)
        items[name] = {"pid": int(pid) if pid else 0, "at": _now()}
        _write(project_dir, items)


def forget(project_dir: str, name: str) -> None:
    if not name:
        return
    with _lock:
        items = load(project_dir)
        if items.pop(name, None) is not None:
            _write(project_dir, items)


def clear(project_dir: str) -> None:
    with _lock:
        _write(project_dir, {})


def _now() -> float:
    import time
    return round(time.time(), 3)


def pid_alive(pid: Any) -> bool:
    """pid 对应的进程是否还活着。

    只用来「避免重复拉起」，不问它是不是当初那个进程：pid 复用可能让我们
    误判成活着，代价是少恢复一台；反过来（判死但其实活着）就会去撞端口。
    两害相权，偏向保守。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                # 只看 OpenProcess 成不成功是不够的：父进程（我们自己）还持有着
                # 已退出进程的句柄时，那个 pid 依然能被打开，于是「已经退出的
                # 子进程」会被当成活着，恢复时被白白跳过。GetExitCodeProcess
                # 还在回 STILL_ACTIVE 才算真在跑。
                code = wintypes.DWORD()
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                        handle, ctypes.byref(code)):
                    return False
                return code.value == STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (OSError, AttributeError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        # 没权限说明进程在，只是不归我们管
        return True


__all__ = [
    "STATE_NAME",
    "state_path",
    "load",
    "remember",
    "forget",
    "clear",
    "pid_alive",
]
