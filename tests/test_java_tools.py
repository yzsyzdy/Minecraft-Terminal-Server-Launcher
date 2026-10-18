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
from unittest import mock

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


class JdkChecksumCoverageTest(unittest.TestCase):
    """每个能下的 JDK 归档都得有 sha256 钉在 JDK_SHA256 里。

    少钉一条不会报错，只会悄悄退回「只检查能不能解压出 bin/java」——
    而那条路的防护力约等于零，所以用测试把它钉住。
    """

    def test_every_mirror_url_has_a_pinned_hash(self):
        missing = sorted(f"{ver}/{os_name}"
                         for ver, oses in jt.JDK_MIRROR_URLS.items()
                         for os_name in oses
                         if (ver, os_name) not in jt.JDK_SHA256)
        self.assertEqual(missing, [])

    def test_no_pin_without_a_url(self):
        extra = sorted(f"{ver}/{os_name}"
                       for ver, os_name in jt.JDK_SHA256
                       if os_name not in jt.JDK_MIRROR_URLS.get(ver, {}))
        self.assertEqual(extra, [])

    def test_pins_are_lowercase_sha256_hex(self):
        for (ver, os_name), value in jt.JDK_SHA256.items():
            self.assertRegex(value, r"^[0-9a-f]{64}$", f"{ver}/{os_name} => {value}")

    def test_url_points_at_the_pinned_version(self):
        """URL 换了版本、哈希没跟着换，校验就会永远失败。"""
        for ver, oses in jt.JDK_MIRROR_URLS.items():
            for os_name, url in oses.items():
                self.assertIn(f"-{ver}.", os.path.basename(url), url)


class MacosJavaScanTest(unittest.TestCase):
    """macOS 的 JDK 装在 .jdk 包深处，扫描必须能走到那里。

    以前 POSIX 分支只扫 `/usr/lib/jvm` 这类 Linux 目录，macOS 上一个都找不到，
    于是「三平台」里只有两个平台能自动接上 Java。
    """

    def _make_bundle(self, home):
        # 固定用 POSIX 的名字：下面把 _is_windows 也一起 patch 掉了，
        # 扫描时 _java_exe_name() 会返回 "java"，不跟随本机真实平台。
        exe = os.path.join(home, "Library", "Java", "JavaVirtualMachines",
                           "temurin-21.jdk", "Contents", "Home", "bin", "java")
        os.makedirs(os.path.dirname(exe))
        with open(exe, "w", encoding="utf-8") as f:
            f.write("")
        return exe

    def _scan(self, home, macos):
        with mock.patch.object(jt, "_is_windows", lambda: False), \
                mock.patch.object(jt, "_is_macos", lambda: macos), \
                mock.patch.object(jt, "_run_java_version", lambda p: "21.0.2"), \
                mock.patch.dict(os.environ, {"HOME": home}):
            return jt._search_java_from_default_paths()

    def test_roots_cover_the_macos_bundle_locations(self):
        self.assertIn("/Library/Java/JavaVirtualMachines", jt._MACOS_JDK_ROOTS)
        self.assertIn("{home}/Library/Java/JavaVirtualMachines", jt._MACOS_JDK_ROOTS)

    def test_roots_cover_both_homebrew_prefixes(self):
        # Apple 芯片在 /opt/homebrew，Intel 在 /usr/local，两个都得扫
        self.assertIn("/opt/homebrew/opt", jt._MACOS_JDK_ROOTS)
        self.assertIn("/usr/local/opt", jt._MACOS_JDK_ROOTS)

    def test_suffixes_cover_bundle_and_homebrew_layouts(self):
        self.assertIn(("Contents", "Home", "bin"), jt._POSIX_JDK_SUFFIX_PARTS)
        self.assertIn(("libexec", "openjdk.jdk", "Contents", "Home", "bin"),
                      jt._POSIX_JDK_SUFFIX_PARTS)

    def test_finds_a_bundle_style_jdk(self):
        with tempfile.TemporaryDirectory() as home:
            exe = self._make_bundle(home)
            found = self._scan(home, macos=True)
            self.assertIn(os.path.normpath(exe),
                          [os.path.normpath(j.path) for j in found])

    def test_the_macos_roots_are_gated_on_darwin(self):
        """同一棵树，不假装是 macOS 就不该被扫到。

        不能断言「结果为空」：CI 的 ubuntu runner 自带 /usr/lib/jvm 里的 JDK，
        扫描本来就会扫到真东西。这里要的是「假 HOME 下那棵树没被当成 macOS
        bundle 认出来」，所以只否掉那一条路径。
        """
        with tempfile.TemporaryDirectory() as home:
            exe = self._make_bundle(home)
            found = self._scan(home, macos=False)
            self.assertNotIn(os.path.normpath(exe),
                             [os.path.normpath(j.path) for j in found])


class JdkSourceResolutionTest(unittest.TestCase):
    """下载源：镜像优先、Adoptium 兜底，键必须是「系统-架构」。"""

    def test_mirror_keys_are_arch_qualified(self):
        """只写 "linux" 的键在 ARM 上会静默下到 x64 包，必须带上架构。"""
        for ver, entries in jt.JDK_MIRROR_URLS.items():
            for key in entries:
                self.assertRegex(key, r"^(windows|linux|darwin)-(x64|aarch64)$",
                                 f"{ver}: {key}")

    def test_mirror_wins_when_it_has_the_combo(self):
        with mock.patch.object(jt, "_adoptium_package",
                               side_effect=AssertionError("不该去问 Adoptium")):
            url, sha = jt._jdk_source("21", "linux", "x64")
        self.assertIn("graalvm", url)
        self.assertEqual(sha, jt.JDK_SHA256[("21", "linux-x64")])

    def test_adoptium_is_used_for_macos(self):
        """镜像站没有 macOS 的包（实测 500），只能走 Adoptium。"""
        with mock.patch.object(jt, "_adoptium_package",
                               return_value=("https://example/jdk.tar.gz", "abc")) as m:
            got = jt._jdk_source("21", "darwin", "aarch64")
        self.assertEqual(got, ("https://example/jdk.tar.gz", "abc"))
        m.assert_called_once_with("21", "darwin", "aarch64")

    def test_adoptium_is_used_for_old_versions(self):
        """Java 8 / 11 / 16 只有 Adoptium 有，镜像表里根本没有这几个版本。"""
        for ver in ("8", "11", "16"):
            self.assertNotIn(ver, jt.JDK_MIRROR_URLS)
            self.assertTrue(jt.can_auto_download(ver))
            with mock.patch.object(jt, "_adoptium_package",
                                   return_value=("u", "s")):
                self.assertEqual(jt._jdk_source(ver, "linux", "x64"), ("u", "s"))

    def test_arch_names_are_mapped(self):
        self.assertEqual(jt._ADOPTIUM_ARCH["arm64"], "aarch64")
        self.assertEqual(jt._ADOPTIUM_ARCH["aarch64"], "aarch64")
        self.assertEqual(jt._ADOPTIUM_ARCH["x86_64"], "x64")
        self.assertEqual(jt._ADOPTIUM_ARCH["amd64"], "x64")

    def test_unknown_version_cannot_be_downloaded(self):
        self.assertFalse(jt.can_auto_download("30"))

    def test_mirror_failure_falls_back_to_adoptium(self):
        """镜像上的具体小版本被撤掉（HEAD 在、GET 500）时要换 Adoptium 再试。

        镜像表钉的是 graalvm-community-jdk-17.0.9 这种精确文件名，上游删一个
        文件就会让「自动下载」变成一句「不支持」，而 Adoptium 那边明明还有。
        """
        tried = []

        def _fake_fetch(url, expected, ver, os_name, arch, project_dir):
            tried.append(url)
            return None if "fastmirror" in url else f"/jdk-{ver}/bin/java"

        with mock.patch.object(jt, "_download_with_progress", return_value=False), \
             mock.patch.object(jt, "_adoptium_package",
                               return_value=("https://example/adoptium.tar.gz", "abc")), \
             mock.patch.object(jt, "_fetch_jdk", side_effect=_fake_fetch), \
             mock.patch.object(jt, "_os_platform", return_value="linux"), \
             mock.patch.object(jt, "_machine_arch", return_value="x64"):
            got = jt._download_jdk("21", ".")

        self.assertEqual(got, "/jdk-21/bin/java")
        self.assertEqual(len(tried), 2)
        self.assertIn("fastmirror", tried[0])
        self.assertIn("adoptium", tried[1])

    def test_no_second_attempt_when_adoptium_has_the_same_url(self):
        """镜像本身就是 Adoptium 地址时不要白试第二遍。"""
        with mock.patch.object(jt, "_fetch_jdk", return_value=None) as fetch, \
             mock.patch.object(jt, "_adoptium_package",
                               return_value=("https://example/same.tar.gz", "abc")), \
             mock.patch.object(jt, "_os_platform", return_value="darwin"), \
             mock.patch.object(jt, "_machine_arch", return_value="aarch64"), \
             mock.patch.object(jt, "_jdk_source",
                               return_value=("https://example/same.tar.gz", "abc")):
            self.assertIsNone(jt._download_jdk("21", "."))
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
