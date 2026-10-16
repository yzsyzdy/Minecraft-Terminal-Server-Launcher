"""JDK 归档解压的安全边界，以及 Java 版本串的解析。

两处都是「平时看不见、出事就是数据或体验灾难」的地方：

* `_safe_extract_tar` 是全项目唯一解 tar 的地方，而 JDK 压缩包是从网上
  下回来的。`tarfile.extractall()` 默认会照成员名里的 `../` 往外写。
* `_java_major_version` 决定「这个 Java 够不够新」。它返回 0 时，任何
  `>= 21` 的判断都为假，于是一台装着 Java 21 的机器被劝去重新下载。
"""

import io
import os
import shutil
import tarfile
import tempfile
import unittest
import warnings

import java_tools as jt


# ---------------------------------------------------------------------------
# 版本串解析
# ---------------------------------------------------------------------------

class JavaMajorVersionTest(unittest.TestCase):
    def test_plain_release(self):
        self.assertEqual(jt._java_major_version("21.0.2"), 21)

    def test_build_suffix(self):
        self.assertEqual(jt._java_major_version("17.0.9+11"), 17)

    def test_ea_suffix(self):
        """旧实现 int("21-ea".split(".")[0]) 抛 ValueError → 返回 0。"""
        self.assertEqual(jt._java_major_version("21-ea"), 21)

    def test_build_only_suffix(self):
        self.assertEqual(jt._java_major_version("22+36"), 22)

    def test_legacy_one_dot_eight(self):
        """1.8.0_392 的主版本号是 8，不是 1。"""
        self.assertEqual(jt._java_major_version("1.8.0_392"), 8)

    def test_legacy_one_dot_eight_short(self):
        self.assertEqual(jt._java_major_version("1.8"), 8)

    def test_ten_and_above_are_not_treated_as_legacy(self):
        self.assertEqual(jt._java_major_version("10.0.2"), 10)
        self.assertEqual(jt._java_major_version("100"), 100)

    def test_whitespace_is_stripped(self):
        self.assertEqual(jt._java_major_version("  21.0.2\n"), 21)

    def test_unparsable_returns_zero(self):
        for bad in ("", "   ", "ea", "unknown", "openjdk version"):
            with self.subTest(bad=bad):
                self.assertEqual(jt._java_major_version(bad), 0)

    def test_none_is_tolerated(self):
        self.assertEqual(jt._java_major_version(None), 0)


class VersionSortKeyTest(unittest.TestCase):
    def test_mixed_suffixes_do_not_raise(self):
        """旧实现把数字段放 int、其余放 str，一排序就 TypeError。"""
        versions = ["21-ea", "21.0.2", "8u392", "1.8.0_392", "17.0.9+11", "22+36"]
        ordered = sorted(versions, key=jt._version_sort_key, reverse=True)
        self.assertEqual(ordered, ["22+36", "21-ea", "21.0.2",
                                   "17.0.9+11", "8u392", "1.8.0_392"])

    def test_junk_sorts_after_real_versions(self):
        ordered = sorted(["21.0.2", "!!!", "17.0.9"], key=jt._version_sort_key)
        self.assertEqual(ordered, ["17.0.9", "21.0.2", "!!!"])

    def test_junk_entries_compare_equal_to_each_other(self):
        """不能返回空元组：空元组和任何非空元组比都「更小」，会让这类
        条目静默沉底且彼此相等。"""
        self.assertEqual(jt._version_sort_key("!!!"),
                         jt._version_sort_key(""))
        self.assertNotEqual(jt._version_sort_key("!!!"), ())

    def test_key_never_mixes_types(self):
        """每个元素的形状都是 (int, int, str)，这样才永远可比。"""
        for version in ("21-ea", "1.8.0_392", "", "22+36", "!!!"):
            with self.subTest(version=version):
                for part in jt._version_sort_key(version):
                    self.assertEqual(len(part), 3)
                    self.assertIsInstance(part[0], int)
                    self.assertIsInstance(part[1], int)
                    self.assertIsInstance(part[2], str)


# ---------------------------------------------------------------------------
# tar 解压
# ---------------------------------------------------------------------------

class SafeExtractTarTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mstl-test-jt-")
        self.dest = os.path.join(self.root, "out")
        os.makedirs(self.dest, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # -- 构造 --------------------------------------------------------------

    def _tar(self, name="a.tar"):
        return os.path.join(self.root, name)

    def _write(self, path, entries):
        """entries: [(TarInfo, bytes | None)]"""
        with tarfile.open(path, "w") as t:
            for info, data in entries:
                t.addfile(info, io.BytesIO(data) if data is not None else None)

    @staticmethod
    def _file(name, data=b"x"):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        return info, data

    @staticmethod
    def _symlink(name, target):
        info = tarfile.TarInfo(name)
        info.type = tarfile.SYMTYPE
        info.linkname = target
        return info, None

    @staticmethod
    def _hardlink(name, target):
        info = tarfile.TarInfo(name)
        info.type = tarfile.LNKTYPE
        info.linkname = target
        return info, None

    def _extract(self, path):
        with tarfile.open(path, "r") as tf:
            jt._safe_extract_tar(tf, self.dest)

    def _outside(self, name):
        return os.path.join(self.root, name)

    # -- 正常路径 ----------------------------------------------------------

    def test_extracts_nested_members(self):
        p = self._tar()
        self._write(p, [self._file("jdk-21/bin/java", b"bin"),
                        self._file("jdk-21/lib/a.txt", b"lib")])
        self._extract(p)
        with open(os.path.join(self.dest, "jdk-21", "lib", "a.txt"), "rb") as f:
            self.assertEqual(f.read(), b"lib")

    def test_internal_symlink_is_not_rejected(self):
        """归档内部的相对链接是合法的，不能一起拒掉。"""
        p = self._tar()
        self._write(p, [self._file("jdk-21/a.txt", b"a"),
                        self._symlink("jdk-21/link", "a.txt")])
        try:
            self._extract(p)
        except ValueError as e:
            self.fail(f"归档内部的相对符号链接被误拒: {e}")
        except OSError:
            # Windows 上创建符号链接需要开发者模式或管理员权限
            self.skipTest("当前环境不允许创建符号链接")

    def test_extraction_does_not_rely_on_the_legacy_default_filter(self):
        """Python 3.14 起 extractall 默认换成 data 过滤器，它会把普通文件的
        权限压成 rw-，解出来的 bin/java 直接跑不起来。这里确认我们显式
        要了 fully_trusted，而不是吃默认值。"""
        p = self._tar()
        self._write(p, [self._file("jdk-21/bin/java", b"bin")])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self._extract(p)
        messages = [str(w.message) for w in caught
                    if issubclass(w.category, DeprecationWarning)]
        self.assertEqual(messages, [])

    @unittest.skipIf(os.name == "nt", "Windows 不看权限位")
    def test_executable_bit_survives(self):
        info = tarfile.TarInfo("jdk-21/bin/java")
        info.size = 3
        info.mode = 0o755
        p = self._tar()
        self._write(p, [(info, b"bin")])
        self._extract(p)
        java = os.path.join(self.dest, "jdk-21", "bin", "java")
        self.assertTrue(os.access(java, os.X_OK))

    # -- 越界一律拒绝 ------------------------------------------------------

    def test_refuses_parent_traversal(self):
        p = self._tar()
        self._write(p, [self._file("../TAROUT.txt", b"pwned")])
        with self.assertRaises(ValueError):
            self._extract(p)
        self.assertFalse(os.path.exists(self._outside("TAROUT.txt")))

    def test_refuses_absolute_path(self):
        p = self._tar()
        self._write(p, [self._file("/TAROUT.txt", b"pwned")])
        with self.assertRaises(ValueError):
            self._extract(p)

    def test_refuses_traversal_below_a_subdirectory(self):
        p = self._tar()
        self._write(p, [self._file("jdk-21/../../TAROUT.txt", b"pwned")])
        with self.assertRaises(ValueError):
            self._extract(p)
        self.assertFalse(os.path.exists(self._outside("TAROUT.txt")))

    def test_refuses_symlink_escape(self):
        p = self._tar()
        self._write(p, [self._symlink("jdk-21/link", "../../../pwned.txt")])
        with self.assertRaises(ValueError):
            self._extract(p)

    def test_refuses_absolute_symlink(self):
        p = self._tar()
        self._write(p, [self._symlink("jdk-21/link", "/etc/passwd")])
        with self.assertRaises(ValueError):
            self._extract(p)

    def test_refuses_hardlink_escape(self):
        """硬链接的 linkname 相对归档根，不是相对成员目录。"""
        p = self._tar()
        self._write(p, [self._hardlink("jdk-21/hard", "../pwned.txt")])
        with self.assertRaises(ValueError):
            self._extract(p)

    def test_nothing_is_written_when_any_member_is_bad(self):
        """校验全部成员在前、落地在后：坏包不能留下解了一半的目录。"""
        p = self._tar()
        self._write(p, [self._file("jdk-21/good.txt", b"good"),
                        self._file("../TAROUT.txt", b"pwned")])
        with self.assertRaises(ValueError):
            self._extract(p)
        self.assertFalse(os.path.exists(os.path.join(self.dest, "jdk-21", "good.txt")))
        self.assertFalse(os.path.exists(self._outside("TAROUT.txt")))


if __name__ == "__main__":
    unittest.main()
