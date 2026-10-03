"""路径解析与开机自启命令。

这一组专门盯住「打包版里判断方式不对」这类 bug：
Nuitka 的 --standalone 不设置 sys.frozen，所以只看 sys.frozen 的分支在打包版里
一次都走不到（MSTL_DATA_DIR 失效、自启命令指向不存在的 python.exe）。
测试里用伪造的 __compiled__ 来模拟「正在打包版里跑」，不需要真的去编译。
"""

import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

import paths
import startup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SourceModeTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("MSTL_DATA_DIR", None)

    def test_not_packaged_when_running_from_source(self):
        self.assertFalse(paths.is_packaged())

    def test_exe_path_is_empty_from_source(self):
        self.assertEqual(paths.exe_path(), "")
        self.assertEqual(paths.exe_dir(), "")

    def test_app_dir_defaults_to_the_checkout(self):
        self.assertEqual(os.path.abspath(paths.app_dir()), os.path.abspath(ROOT))

    def test_data_dir_env_wins_even_from_source(self):
        """MSTL_DATA_DIR 与是否打包无关，源码模式下也得认。"""
        target = tempfile.mkdtemp(prefix="mstl-test-paths-")
        try:
            with mock.patch.dict(os.environ, {"MSTL_DATA_DIR": target}):
                self.assertEqual(os.path.abspath(paths.app_dir()),
                                 os.path.abspath(target))
        finally:
            shutil.rmtree(target, ignore_errors=True)

    def test_startup_command_points_at_python_and_start_py(self):
        cmd = startup._startup_command()
        pieces = [p for p in cmd.split('"') if p.strip()]
        self.assertEqual(len(pieces), 2)
        for piece in pieces:
            self.assertTrue(os.path.isfile(piece), piece)
        self.assertTrue(pieces[1].endswith("start.py"))


class FrozenModeTest(unittest.TestCase):
    """伪造 __compiled__，模拟打包版（standalone）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mstl-test-frozen-")
        # 假装这是用户双击的那个 exe
        self.fake_exe = os.path.join(self.tmp, "MSTL.exe")
        with open(self.fake_exe, "w", encoding="utf-8") as f:
            f.write("stub")
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        self._add_compiled(self.fake_exe)

    def tearDown(self):
        if hasattr(paths, "__compiled__"):
            del paths.__compiled__
        os.environ.pop("MSTL_DATA_DIR", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _add_compiled(self, argv0):
        paths.__compiled__ = types.SimpleNamespace(
            standalone=True, onefile=False, original_argv0=argv0)

    # -- 识别 --------------------------------------------------------------

    def test_standalone_is_detected_without_sys_frozen(self):
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertTrue(paths.is_packaged())

    def test_sys_frozen_also_counts(self):
        del paths.__compiled__
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertTrue(paths.is_packaged())

    # -- 数据目录（就是那个「打包版不认 MSTL_DATA_DIR」的 bug）------------

    def test_app_dir_uses_the_exe_folder_by_default(self):
        self.assertEqual(os.path.abspath(paths.app_dir()),
                         os.path.abspath(self.tmp))

    def test_data_dir_env_still_wins_when_packaged(self):
        with mock.patch.dict(os.environ, {"MSTL_DATA_DIR": self.data_dir}):
            self.assertEqual(os.path.abspath(paths.app_dir()),
                             os.path.abspath(self.data_dir))

    # -- exe 路径 ----------------------------------------------------------

    def test_exe_path_is_the_real_exe_not_python(self):
        self.assertEqual(os.path.abspath(paths.exe_path()),
                         os.path.abspath(self.fake_exe))
        self.assertEqual(os.path.abspath(paths.exe_dir()),
                         os.path.abspath(self.tmp))
        self.assertFalse(paths.exe_path().lower().endswith("python.exe"))

    def test_exe_path_is_empty_when_nothing_can_be_trusted(self):
        """取不到可靠路径时宁可返回空串，也不要拼一条开机跑不起来的命令。"""
        self._add_compiled(os.path.join(self.tmp, "does-not-exist.exe"))
        with mock.patch.object(sys, "argv", ["nope.exe"]), \
                mock.patch.object(sys, "executable",
                                  os.path.join(self.tmp, "python.exe")):
            for var in ("NUITKA_ORIGINAL_ARGV0", "NUITKA_ONEFILE_ARGV0",
                        "NUITKA_ONEFILE_PARENT"):
                os.environ.pop(var, None)
            self.assertEqual(paths.exe_path(), "")

    # -- 开机自启命令 ------------------------------------------------------

    def test_startup_command_is_the_real_exe(self):
        cmd = startup._startup_command()
        pieces = [p for p in cmd.split('"') if p.strip()]
        self.assertEqual(pieces, [os.path.abspath(self.fake_exe)])
        self.assertTrue(os.path.isfile(pieces[0]))

    def test_startup_command_never_uses_the_data_dir(self):
        with mock.patch.dict(os.environ, {"MSTL_DATA_DIR": self.data_dir}):
            cmd = startup._startup_command()
        self.assertNotIn(self.data_dir, cmd)
        self.assertIn("MSTL.exe", cmd)

    def test_startup_command_is_empty_when_no_exe_can_be_resolved(self):
        self._add_compiled(os.path.join(self.tmp, "gone.exe"))
        with mock.patch.object(sys, "argv", ["nope.exe"]), \
                mock.patch.object(sys, "executable",
                                  os.path.join(self.tmp, "python.exe")):
            for var in ("NUITKA_ORIGINAL_ARGV0", "NUITKA_ONEFILE_ARGV0",
                        "NUITKA_ONEFILE_PARENT"):
                os.environ.pop(var, None)
            self.assertEqual(startup._startup_command(), "")


class TempOnefileDirTest(unittest.TestCase):
    def test_temp_dirs_are_recognised(self):
        for name in ("onefile_abc123", "onefile-xyz"):
            self.assertTrue(paths._is_temp_onefile_dir(os.path.join("/tmp", name)))
        self.assertFalse(paths._is_temp_onefile_dir("/tmp/mstl"))
        self.assertFalse(paths._is_temp_onefile_dir("/tmp"))


if __name__ == "__main__":
    unittest.main()
