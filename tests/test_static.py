"""静态一致性检查。

这一组把我这一路手工跑的检查固化下来：它们在这次审计里各自抓到过真问题
（写错函数名、面板引用了不存在的元素、前端用了后端没下发的文案 key、
中文文档与代码脱节）。放进测试里，CI 就能一直盯着。
"""

import ast
import os
import re
import shutil
import subprocess
import symtable
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"_rpmerge", ".git", "__pycache__", ".piptmp", "build",
             ".docs_deps", "site", "tests"}

# 解释器/插件注入的名字，以及零参 super() 会隐式使用的 __class__
INJECTED = {"__file__", "__name__", "__doc__", "__class__", "__compiled__",
            "__package__", "__spec__", "__loader__", "__builtins__"}


def python_files():
    found = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith(".py"):
                found.append(os.path.join(base, name))
    return found


def rel(path):
    return os.path.relpath(path, ROOT)


class UndefinedNameTest(unittest.TestCase):
    """扫出「函数里用了、但哪儿都没定义」的名字。

    这类错误一旦落在被 except 包住的路径上，就变成「功能静默失效」：
    SpigotMC 插件下载调了一个不存在的函数，异常被吞掉，界面只显示
    「下载失败」，查了很久。
    """

    def test_no_undefined_globals(self):
        import builtins
        problems = []
        for path in python_files():
            with open(path, encoding="utf-8") as f:
                source = f.read()
            try:
                tree = ast.parse(source, path)
                table = symtable.symtable(source, path, "exec")
            except SyntaxError as exc:
                problems.append(f"{rel(path)}: 语法错误 {exc}")
                continue

            # 内建名必须算进来：函数里引用 Exception 之类在 symtable 看来
            # 也是 global。注意别用 __builtins__——它有时是模块、有时是字典。
            globals_ = set(INJECTED) | set(dir(builtins))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        globals_.add((alias.asname or alias.name).split(".")[0])
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        for sub in ast.walk(target):
                            if isinstance(sub, ast.Name):
                                globals_.add(sub.id)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)):
                    globals_.add(node.name)
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    globals_.add(node.target.id)
                elif isinstance(node, ast.Global):
                    globals_.update(node.names)

            def walk(table, bound):
                for child in table.get_children():
                    if child.get_type() != "function":
                        walk(child, bound)
                        continue
                    inner = set(bound) | set(child.get_parameters())
                    inner |= {s.get_name() for s in child.get_symbols()
                              if s.is_assigned() or s.is_parameter()}
                    for symbol in child.get_symbols():
                        name = symbol.get_name()
                        if (symbol.is_referenced() and name not in inner
                                and name not in globals_
                                and (symbol.is_global() or symbol.is_free())):
                            problems.append(
                                f"{rel(path)}: {child.get_name()}() -> {name}")
                    walk(child, inner)

            walk(table, set())
        self.assertEqual(problems, [])


class PanelConsistencyTest(unittest.TestCase):
    """网页面板：元素 id 与文案 key 前后端要对得上。"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(ROOT, "webui", "index.html")
        with open(path, encoding="utf-8") as f:
            cls.html = f.read()
        cls.scripts = "\n;\n".join(
            re.findall(r"<script[^>]*>(.*?)</script>", cls.html, re.S))
        cls.ids = re.findall(r'\bid="([^"]+)"', cls.html)

    def test_no_duplicate_element_ids(self):
        dupes = sorted({i for i in self.ids if self.ids.count(i) > 1})
        self.assertEqual(dupes, [])

    def test_every_referenced_element_exists(self):
        used = set(re.findall(r'\$\("([^"]+)"\)', self.scripts))
        missing = sorted(used - set(self.ids))
        self.assertEqual(missing, [])

    def test_every_frontend_string_is_delivered_by_the_backend(self):
        """前端 strings[...] 用到的 key 必须都在 WEB_STRINGS 里。

        漏一个，用户看到的就是一句英文兜底（`name required`）或者空字符串——
        实例名为空时的提示就这么漏过。
        """
        from web.server import WEB_STRINGS
        used = set(re.findall(r'strings\["([^"]+)"\]', self.scripts))
        missing = sorted(k for k in used if k not in set(WEB_STRINGS))
        self.assertEqual(missing, [])

    def test_javascript_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("没有 node，跳过 JS 语法检查")
        tmp = os.path.join(tempfile.gettempdir(), "mstl-panel-check.js")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self.scripts)
        try:
            result = subprocess.run([node, "--check", tmp],
                                    capture_output=True, text=True)
        finally:
            os.remove(tmp)
        self.assertEqual(result.returncode, 0, result.stderr)


class LocaleCoverageTest(unittest.TestCase):
    """代码里用到的 i18n key 必须真的存在，否则界面显示裸 key。"""

    def test_every_key_used_in_code_exists(self):
        import json
        with open(os.path.join(ROOT, "locales", "zh.json"), encoding="utf-8") as f:
            zh = json.load(f)

        used = set()
        for path in python_files():
            with open(path, encoding="utf-8") as f:
                source = f.read()
            try:
                tree = ast.parse(source, path)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "t" and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    used.add(node.args[0].value)

        missing = sorted(k for k in used if "." in k and k not in zh)
        self.assertEqual(missing, [])

    def test_server_side_messages_exist_in_every_locale(self):
        """服务端直接打印的提示（面板/终端）四种语言都要有。"""
        import json
        keys = ("web.cli.data_dir_failed", "app.data_dir_failed",
                "search.source_failed", "download.source_failed")
        for lang in ("zh", "en", "ru", "neko"):
            with open(os.path.join(ROOT, "locales", f"{lang}.json"),
                      encoding="utf-8") as f:
                table = json.load(f)
            for key in keys:
                self.assertIn(key, table, f"{lang} 缺少 {key}")


class ModuleImportTest(unittest.TestCase):
    """所有顶层模块都要能导入（打包时会顺着 import 图收集，导不进来就是缺模块）。"""

    def test_top_level_modules_import(self):
        import importlib
        modules = ["start", "menu", "config", "paths", "i18n", "startup",
                   "server_manager", "server_launcher", "java_tools",
                   "check_update", "clear", "http_utils", "constants",
                   "download_msl", "mod_sources", "plugin_sources",
                   "core.archive", "core.errors", "core.files", "core.frp",
                   "core.provision", "core.runtime", "core.transfer",
                   "frp.frpc_files", "frp.tunnel_manager",
                   "openfrp.tunnel_manager", "web.server"]
        for name in modules:
            with self.subTest(module=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
