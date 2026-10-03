"""MSTL 的测试。

只用标准库（unittest），和项目本身一样不引入第三方运行时依赖，
所以 CI 里不需要 pip install 任何东西。

跑法（仓库根目录）：
    python -m unittest discover -s tests -t . -v
"""
