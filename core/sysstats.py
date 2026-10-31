"""本机资源占用采样：CPU / 内存 / 网络 / 运行时长。

不引 psutil——项目承诺零外部依赖，为此加一个编译型依赖不划算。三个平台各走
各的系统接口：

  * Windows：ctypes 调 GetSystemTimes / GlobalMemoryStatusEx / GetTickCount64，
    网络走 `netstat -e`（输出会本地化，所以只认「一行里有两个纯整数」的形状，
    不认标签文字）；
  * Linux：读 /proc/stat、/proc/meminfo、/proc/net/dev、/proc/uptime；
  * macOS：`sysctl`（含 kern.cp_time）/ `vm_stat` / `netstat -ib`。

任何一项取不到就返回 None，界面显示「—」。宁可空着，也不要编一个好看的假数字。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from typing import Optional


def _run(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=3,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ---------------------------------------------------------------- CPU


def _win_filetime_value(ft) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def _cpu_times() -> Optional[tuple[int, int]]:
    """返回 (idle, total) 两个递增计数器；取不到返回 None。"""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD),
                        ("dwHighDateTime", wintypes.DWORD)]

        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        if not ok:
            return None
        # kernel 时间里已经含 idle，所以 total = kernel + user
        return (_win_filetime_value(idle),
                _win_filetime_value(kernel) + _win_filetime_value(user))

    if sys.platform == "darwin":
        # kern.cp_time 是开机以来的累计 tick（user nice sys idle），
        # 和 /proc/stat 一样能取两次差分。以前这里用 loadavg 折算，凑出来的
        # total 恒定不变，差分永远为 0，占用率就一直是 None。
        parts = _run(["sysctl", "-n", "kern.cp_time"]).split()
        if len(parts) < 4:
            return None
        try:
            user, nice, system, idle = (int(float(p)) for p in parts[:4])
        except ValueError:
            return None
        busy = user + nice + system
        return (idle, busy + idle)

    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            fields = f.readline().split()[1:]
    except OSError:
        return None
    values = [int(v) for v in fields if v.isdigit()]
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


# --------------------------------------------------------------- 内存


def _memory() -> tuple[Optional[int], Optional[int]]:
    """返回 (used, total) 字节数；取不到返回 (None, None)。"""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return None, None
        total = int(stat.ullTotalPhys)
        return total - int(stat.ullAvailPhys), total

    if sys.platform == "darwin":
        total = _run(["sysctl", "-n", "hw.memsize"]).strip()
        vm = _run(["vm_stat"])
        if not total.isdigit() or not vm:
            return None, None
        page = 4096
        match = re.search(r"page size of (\d+)", vm)
        if match:
            page = int(match.group(1))
        used_pages = 0
        for line in vm.splitlines():
            key, _, rest = line.partition(":")
            if key.strip() in ("Pages active", "Pages wired down",
                               "Pages occupied by compressor"):
                num = re.match(r"\s*(\d+)", rest)
                if num:
                    used_pages += int(num.group(1))
        return used_pages * page, int(total)

    used = total = None
    try:
        info = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                num = re.match(r"\s*(\d+)", rest)
                if num:
                    info[key] = int(num.group(1)) * 1024
        total = info.get("MemTotal")
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        if total:
            used = total - avail
    except OSError:
        return None, None
    return used, total


# --------------------------------------------------------------- 网络


def _net_bytes() -> Optional[tuple[int, int]]:
    """累计收发字节 (rx, tx)。"""
    if sys.platform == "win32":
        text = _run(["netstat", "-e"])
        # 标签会随系统语言变（"Bytes" / "字节"），只认「两个纯整数」的行，
        # 而且第一行这种数据就是总计。
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
                return int(parts[-2]), int(parts[-1])
        return None

    if sys.platform == "darwin":
        text = _run(["netstat", "-ib"])
        seen: set[str] = set()
        rx = tx = 0
        for line in text.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 10 or parts[0] == "lo0":
                continue
            name, ibytes, obytes = parts[0], parts[6], parts[9]
            if name in seen or not ibytes.isdigit() or not obytes.isdigit():
                continue
            seen.add(name)
            rx += int(ibytes)
            tx += int(obytes)
        return (rx, tx) if seen else None

    rx = tx = 0
    try:
        with open("/proc/net/dev", "r", encoding="utf-8") as f:
            for line in f.readlines()[2:]:
                name, _, rest = line.partition(":")
                if name.strip() == "lo":
                    continue
                cols = rest.split()
                if len(cols) >= 9:
                    rx += int(cols[0])
                    tx += int(cols[8])
    except (OSError, ValueError):
        return None
    return rx, tx


# ------------------------------------------------------------- 运行时长


def _uptime() -> Optional[float]:
    if sys.platform == "win32":
        import ctypes
        return ctypes.windll.kernel32.GetTickCount64() / 1000.0
    if sys.platform == "darwin":
        text = _run(["sysctl", "-n", "kern.boottime"])
        match = re.search(r"sec = (\d+)", text)
        return (time.time() - int(match.group(1))) if match else None
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            return float(f.read().split()[0])
    except (OSError, ValueError):
        return None


class SystemSampler:
    """两次采样之间算差值。第一次调用没有参照，CPU 与网络给 None。"""

    def __init__(self) -> None:
        self._prev_cpu: Optional[tuple[int, int]] = None
        self._prev_net: Optional[tuple[int, int]] = None
        self._prev_at: Optional[float] = None

    def sample(self) -> dict:
        now = time.monotonic()
        cpu_times = _cpu_times()
        net = _net_bytes()

        cpu_percent = None
        if cpu_times and self._prev_cpu and self._prev_at is not None:
            idle_delta = cpu_times[0] - self._prev_cpu[0]
            total_delta = cpu_times[1] - self._prev_cpu[1]
            if total_delta > 0:
                cpu_percent = max(0.0, min(100.0, 100.0 * (1 - idle_delta / total_delta)))

        net_rate = None
        if net and self._prev_net and self._prev_at is not None:
            elapsed = max(0.05, now - self._prev_at)
            net_rate = (
                max(0, net[0] - self._prev_net[0]) / elapsed,
                max(0, net[1] - self._prev_net[1]) / elapsed,
            )

        used, total = _memory()
        result = {
            "cpu": {"percent": None if cpu_percent is None else round(cpu_percent, 1),
                    "cores": os.cpu_count() or 1},
            "memory": {
                "used": used,
                "total": total,
                "percent": None if not (used and total) else round(used / total * 100, 1),
            },
            "network": {
                "rx_bps": None if net_rate is None else round(net_rate[0], 1),
                "tx_bps": None if net_rate is None else round(net_rate[1], 1),
            },
            "uptime": _uptime(),
            "platform": sys.platform,
        }

        if cpu_times:
            self._prev_cpu = cpu_times
        if net:
            self._prev_net = net
        self._prev_at = now
        return result


__all__ = ["SystemSampler"]
