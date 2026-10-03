"""更新检查读 version.json。

背景：CI 用 Windows PowerShell 5.1 的 `Set-Content -Encoding UTF8` 写这个文件，
它会带 BOM；而 Python 用 utf-8 打开时 json.load 会抛 JSONDecodeError。原来那个
异常被 except 吞掉，于是每个正式发布版的更新检查都是哑的（永远显示「已是最新
版」），界面上一点报错都没有。
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import check_update
import paths


class ReadLocalVersionTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-ver-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, payload: bytes, name="version.json"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(payload)
        return path

    def test_plain_utf8(self):
        self._write(json.dumps({"version": "1.2.3"}).encode("utf-8"))
        with mock.patch.object(check_update, "_bundled_dir", lambda: self.dir):
            self.assertEqual(check_update._read_local_version(), "1.2.3")

    def test_utf8_with_bom(self):
        """带 BOM 的文件必须能读出来——这正是漏过一版的那个 bug。"""
        payload = b"\xef\xbb\xbf" + json.dumps({"version": "1.2.3"}).encode("utf-8")
        self._write(payload)
        with mock.patch.object(check_update, "_bundled_dir", lambda: self.dir):
            self.assertEqual(check_update._read_local_version(), "1.2.3")

    def test_missing_file(self):
        with mock.patch.object(check_update, "_bundled_dir", lambda: self.dir):
            self.assertEqual(check_update._read_local_version(), "")

    def test_corrupt_file(self):
        self._write(b"{ not json")
        with mock.patch.object(check_update, "_bundled_dir", lambda: self.dir):
            self.assertEqual(check_update._read_local_version(), "")

    def test_missing_version_field(self):
        self._write(json.dumps({"tag_name": "v1.0.0"}).encode("utf-8"))
        with mock.patch.object(check_update, "_bundled_dir", lambda: self.dir):
            self.assertEqual(check_update._read_local_version(), "")

    def test_falls_back_to_the_exe_folder(self):
        """onefile 下 bundled_dir() 是 %TEMP% 解压目录，文件其实在 exe 旁边。"""
        empty = os.path.join(self.dir, "empty")
        os.makedirs(empty)
        self._write(json.dumps({"version": "9.9.9"}).encode("utf-8"))
        with mock.patch.object(check_update, "_bundled_dir", lambda: empty), \
                mock.patch.object(paths, "exe_dir", lambda: self.dir):
            self.assertEqual(check_update._read_local_version(), "9.9.9")

    def test_version_is_stripped(self):
        self._write(json.dumps({"version": "  1.2.3  "}).encode("utf-8"))
        with mock.patch.object(check_update, "_bundled_dir", lambda: self.dir):
            self.assertEqual(check_update._read_local_version(), "1.2.3")


class VersionCompareTest(unittest.TestCase):
    def test_padding_avoids_false_updates(self):
        """1.0 不能被判成比 1.0.0 旧，否则每次都提示有新版本。"""
        self.assertEqual(check_update._parse_version("1.0"),
                         check_update._parse_version("1.0.0"))

    def test_v_prefix_is_ignored(self):
        self.assertEqual(check_update._parse_version("v1.2.3"),
                         check_update._parse_version("1.2.3"))

    def test_suffix_uses_the_numeric_prefix(self):
        self.assertEqual(check_update._parse_version("1.2.3-beta"),
                         check_update._parse_version("1.2.3"))

    def test_ordering(self):
        self.assertLess(check_update._parse_version("1.0.0"),
                        check_update._parse_version("1.0.1"))
        self.assertLess(check_update._parse_version("1.9.0"),
                        check_update._parse_version("1.10.0"))

    def test_ci_writes_version_json_without_a_bom(self):
        """CI 里那条写入不能退回带 BOM 的写法。"""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".github", "workflows", "build.yml"),
                  encoding="utf-8") as f:
            workflow = f.read()
        self.assertIn("UTF8Encoding $false", workflow)
        self.assertNotIn("Set-Content -Path $versionFile", workflow)


if __name__ == "__main__":
    unittest.main()
