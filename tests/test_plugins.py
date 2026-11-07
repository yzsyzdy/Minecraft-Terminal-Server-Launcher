"""插件机制的边界。

核心承诺零依赖，所以插件目录是唯一的「可以引第三方库」的地方。这里钉住：
缺依赖时优雅降级、坏插件不拖垮面板、路径不能穿出插件目录。
"""

import json
import os
import tempfile
import unittest

from core.plugins import (ENTRY_NAME, MANIFEST_NAME, PluginManager,
                          plugins_dir)


def make_plugin(root: str, pid: str, manifest: dict, source: str = "",
                assets: dict[str, str] | None = None) -> str:
    path = os.path.join(root, pid)
    os.makedirs(path, exist_ok=True)
    data = {"id": pid, "name": pid, "version": "1.0.0", ENTRY_NAME: "plugin.py"}
    data.update(manifest)
    with open(os.path.join(path, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    if source:
        with open(os.path.join(path, ENTRY_NAME), "w", encoding="utf-8") as f:
            f.write(source)
    for name, text in (assets or {}).items():
        target = os.path.join(path, "web", name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
    return path


GOOD = '''
def register(ctx):
    ctx.route("ping", lambda q, b: (200, "application/json", b'{"ok":true}'))
'''


class ScanTest(unittest.TestCase):
    def test_disabled_by_default(self):
        """enabled 为空时只登记，不加载入口——插件代码不该被自动执行。"""
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "demo", {}, GOOD)
            manager = PluginManager(tmp)
            found = manager.scan(enabled=[])
            self.assertEqual(len(found), 1)
            plugin = manager.get("demo")
            self.assertEqual(plugin.handlers, {}, "没启用就不该注册路由")

    def test_enabled_plugin_registers_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "demo", {}, GOOD)
            manager = PluginManager(tmp)
            manager.scan(enabled=["demo"])
            plugin = manager.get("demo")
            self.assertTrue(plugin.available)
            self.assertIn("ping", plugin.handlers)
            result = manager.dispatch("demo", "ping", {}, None)
            self.assertEqual(result[0], 200)

    def test_missing_dependency_marks_unavailable(self):
        """声明了 requires 但装不上，要如实标成不可用，而不是假装能跑。"""
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "needs",
                        {"requires": ["definitely_not_a_module_xyz"]}, GOOD)
            manager = PluginManager(tmp)
            manager.scan(enabled=["needs"])
            plugin = manager.get("needs")
            self.assertFalse(plugin.available)
            self.assertIn("definitely_not_a_module_xyz", plugin.missing)
            self.assertNotIn("needs", [p.id for p in manager.available()])

    def test_plugin_without_manifest_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(plugins_dir(tmp), "junk"))
            manager = PluginManager(tmp)
            self.assertEqual(manager.scan(enabled=[]), [])

    def test_missing_directory_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginManager(tmp)
            self.assertEqual(manager.scan(enabled=["x"]), [])


class FaultIsolationTest(unittest.TestCase):
    """插件是第三方代码，炸了不能拖垮面板。"""

    def test_import_error_is_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "bad", {},
                        "raise RuntimeError('boom')")
            manager = PluginManager(tmp)
            manager.scan(enabled=["bad"])
            plugin = manager.get("bad")
            self.assertFalse(plugin.available)
            self.assertIn("boom", plugin.error)

    def test_missing_entry_file_is_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "noentry", {})
            manager = PluginManager(tmp)
            manager.scan(enabled=["noentry"])
            self.assertFalse(manager.get("noentry").available)

    def test_no_register_function_is_captured(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "noreg", {}, "x = 1")
            manager = PluginManager(tmp)
            manager.scan(enabled=["noreg"])
            self.assertIn("register", manager.get("noreg").error)

    def test_handler_exception_becomes_500(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "throw", '', '''
def register(ctx):
    def boom(q, b):
        raise ValueError("nope")
    ctx.route("boom", boom)
''')
            manager = PluginManager(tmp)
            manager.scan(enabled=["throw"])
            status, _ctype, body = manager.dispatch("throw", "boom", {}, None)
            self.assertEqual(status, 500)
            self.assertIn(b"nope", body)


class AssetTest(unittest.TestCase):
    def test_asset_served_from_plugin_web_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "demo", {}, GOOD,
                        assets={"xterm.js": "// xterm"})
            manager = PluginManager(tmp)
            manager.scan(enabled=[])
            path = manager.asset_file("demo", "xterm.js")
            self.assertTrue(path and os.path.isfile(path))

    def test_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "demo", {}, GOOD,
                        assets={"a.js": "// a"})
            manager = PluginManager(tmp)
            manager.scan(enabled=[])
            for bad in ("../plugin.json", "..\\x", "web/../plugin.json", ""):
                self.assertIsNone(manager.asset_file("demo", bad), bad)

    def test_unknown_plugin_asset_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = PluginManager(tmp)
            manager.scan(enabled=[])
            self.assertIsNone(manager.asset_file("nope", "a.js"))


class DispatchTest(unittest.TestCase):
    def test_unknown_route_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "demo", {}, GOOD)
            manager = PluginManager(tmp)
            manager.scan(enabled=["demo"])
            self.assertIsNone(manager.dispatch("demo", "nope", {}, None))

    def test_unavailable_plugin_does_not_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin(plugins_dir(tmp), "needs",
                        {"requires": ["definitely_not_a_module_xyz"]}, GOOD)
            manager = PluginManager(tmp)
            manager.scan(enabled=["needs"])
            self.assertIsNone(manager.dispatch("needs", "ping", {}, None))


if __name__ == "__main__":
    unittest.main()
