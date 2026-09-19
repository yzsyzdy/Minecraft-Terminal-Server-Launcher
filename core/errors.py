"""
core 的业务异常。

core 层不打印任何东西。遇到可预期的失败（文件不存在、格式不对、
下载失败……）就抛 CoreError，把 i18n 的 key 与参数原样带出来，
由界面层决定怎么呈现：CLI 翻成一行字，Web 面板翻成一条 JSON。
"""

from __future__ import annotations


class CoreError(Exception):
    """带 i18n key 的业务异常。

    key 与 locales/*.json 里的键一致，kwargs 是该条文案的格式化参数。
    界面层拿到之后通常就是 t(key, **kwargs)，不需要再判断错误种类。
    """

    def __init__(self, key: str, **kwargs):
        super().__init__(key)
        self.key = key
        self.kwargs = kwargs

    def __str__(self) -> str:
        # 只给日志和调试看，用户可见的文案由界面层生成
        if self.kwargs:
            return f"{self.key} {self.kwargs}"
        return self.key
