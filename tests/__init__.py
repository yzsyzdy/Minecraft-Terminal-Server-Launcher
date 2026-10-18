"""MSTL 的测试。

只用标准库（unittest），和项目本身一样不引入第三方运行时依赖，
所以 CI 里不需要 pip install 任何东西。

跑法（仓库根目录）：
    python -m unittest discover -s tests -t . -v
"""

import sys


# 测试会调用真实的打印路径，而 CI 的 stdout 是管道：Windows 上是 cp1252、
# LANG=C 的 Linux 上是 ascii，任何一句中文都会抛 UnicodeEncodeError。
# 产品侧在 start.main() 里做了同样的重配置（start.py 的
# _ensure_utf8_stdout），但测试不走 main()，所以这里补一份。
# 少这一份的后果是「本地全绿、CI 红」——2026-10 就是这么翻的。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is None:
        continue
    try:
        _reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        continue
