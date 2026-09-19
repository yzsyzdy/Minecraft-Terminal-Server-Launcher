"""
core 与界面之间的输出通道。

core 只负责产出事件，不负责把它们画出来。这里定的是一个最小协议：

  log(key, **kwargs)        一条普通信息，界面负责翻译并显示
  progress(...)             一个进度点，只有数字和标签，界面自己排版
  warn(key, **kwargs)       一条警告，流程还能继续

传 key 而不是成品字符串，是为了让界面层持有翻译权。CLI 用 locales
里的中文/英文；将来 Web 面板可以把同一条 key 渲染成 JSON 再交给前端。

core 里的函数一律把 reporter 设成可选参数，默认 NULL，这样测试和
非交互调用（比如 CI 里跑一遍导入导出）不需要准备任何输出设施。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class Reporter(Protocol):
    """core 的输出接收器。实现方只需要下面三个方法。"""

    def log(self, key: str, **kwargs) -> None:
        """一条普通信息。key 是 i18n 键，kwargs 是格式化参数。"""
        ...

    def progress(
        self,
        *,
        label: str = "",
        current: Optional[int] = None,
        total: Optional[int] = None,
        ratio: Optional[float] = None,
        done: bool = False,
    ) -> None:
        """一个进度点。

        current/total 是条目数口径，ratio 是 0~1 的完成度。
        两者都给的时候以 ratio 为准，界面不必自己算。
        """
        ...

    def warn(self, key: str, **kwargs) -> None:
        """一条警告，流程可以继续。"""
        ...


class NullReporter:
    """什么都不做的接收器，core 的默认值。"""

    def log(self, key: str, **kwargs) -> None:
        return None

    def progress(self, **kwargs) -> None:
        return None

    def warn(self, key: str, **kwargs) -> None:
        return None


NULL = NullReporter()


def as_reporter(reporter: Optional[Reporter]) -> Reporter:
    """把 None 收敛成 NULL，省掉 core 里每处都写一遍判空。"""
    return reporter if reporter is not None else NULL
