"""定时任务：每天固定时刻触发一次（重启、备份）。

为什么不用 cron / 系统计划任务：
  * 跨平台：Windows 要 schtasks、macOS 要 launchd、Linux 要 crontab，
    三套写法、三套权限模型，为了一个"每天四点重启"不值得；
  * 面板关掉了就不该再重启服务器——调度跟着 MSTL 进程走最符合直觉。

实现是一个 30 秒粒度的轮询线程：每次看"今天这个时刻过了没"，
过了且今天还没跑过，就跑一次。用日期记录去重，所以进程中途重启也不会漏。
"""

from __future__ import annotations

import datetime
import threading
from typing import Callable, Optional

# 轮询间隔。30 秒足够准（误差不超过半分钟），又不至于空转。
TICK_SECONDS = 30.0
# 错过多久之内还算数。MSTL 卡了一下（比如正在打包备份）导致跳过了整分钟，
# 只要还在这个窗口内就补跑；超过就等明天，免得半夜补一堆重启。
CATCH_UP_SECONDS = 300


def parse_hhmm(text: str) -> Optional[tuple[int, int]]:
    """'04:30' -> (4, 30)。不合法返回 None。"""
    text = str(text or "").strip()
    if not text or ":" not in text:
        return None
    hour_text, _, minute_text = text.partition(":")
    try:
        hour, minute = int(hour_text), int(minute_text)
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def format_hhmm(hour: int, minute: int) -> str:
    return "%02d:%02d" % (hour, minute)


class DailyScheduler:
    """每天某个时刻跑一次回调。

    用法：
        s = DailyScheduler(lambda: do_restart(), "04:30")
        s.start()
        ...
        s.stop()
    """

    def __init__(self, callback: Callable[[], None],
                 when: str = "04:30",
                 label: str = "") -> None:
        self.callback = callback
        self.label = label
        self._hour, self._minute = parse_hhmm(when) or (4, 30)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_run_date: Optional[datetime.date] = None
        # 下一次触发的时间，给面板显示用
        self._next_run: Optional[datetime.datetime] = None
        self._compute_next()

    # -- 配置 ------------------------------------------------------------

    @property
    def when(self) -> str:
        return format_hhmm(self._hour, self._minute)

    def set_when(self, when: str) -> bool:
        parsed = parse_hhmm(when)
        if parsed is None:
            return False
        with self._lock:
            self._hour, self._minute = parsed
            self._compute_next()
        return True

    def _compute_next(self) -> None:
        now = datetime.datetime.now()
        target = now.replace(hour=self._hour, minute=self._minute,
                             second=0, microsecond=0)
        if target <= now:
            target += datetime.timedelta(days=1)
        self._next_run = target

    def next_run_text(self) -> str:
        with self._lock:
            if self._next_run is None:
                return ""
            delta = self._next_run - datetime.datetime.now()
            seconds = max(0, int(delta.total_seconds()))
            hours, rest = divmod(seconds, 3600)
            minutes = rest // 60
            if hours:
                return "%d 小时 %d 分后" % (hours, minutes)
            return "%d 分后" % minutes

    # -- 生命周期 --------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True,
                                            name="mstl-daily-%s" % (self.label or "job"))
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # -- 轮询 ------------------------------------------------------------

    def _loop(self) -> None:
        # 先等一个 tick：进程刚起来时不该立刻触发（那时服务器还在启动）
        while not self._stop.wait(TICK_SECONDS):
            try:
                self._tick()
            except Exception:       # noqa: BLE001 - 调度线程不能因为一次异常就死掉
                continue

    def _tick(self) -> None:
        now = datetime.datetime.now()
        with self._lock:
            if self._last_run_date == now.date():
                return
            target = now.replace(hour=self._hour, minute=self._minute,
                                 second=0, microsecond=0)
            late = (now - target).total_seconds()
            if late < 0 or late > CATCH_UP_SECONDS:
                return
            self._last_run_date = now.date()
            self._compute_next()
        try:
            self.callback()
        except Exception:           # noqa: BLE001
            pass


__all__ = ["DailyScheduler", "parse_hhmm", "format_hhmm", "TICK_SECONDS"]
