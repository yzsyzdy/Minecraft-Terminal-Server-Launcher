"""两套隧道共用的 frpc 文件处理。

这一层替代了原来各自维护的两份实现，所以要把两边原本的行为都覆盖住，
并且钉住合并时修掉的两个差异：
  - ARM 架构要认出来（原版那份不认 armv7，会去下 amd64 的包）
  - 解出来的东西必须像本平台的可执行文件（OpenFrp 那份原来不校验）
"""

import io
import os
import shutil
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

from frp import frpc_files as ff

MZ = b"MZ" + b"\x00" * 62          # 最小的 PE 头
ELF = b"\x7fELF" + b"\x00" * 60


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries:
            z.writestr(name, data)


def make_tar(path, entries, mode="w:gz"):
    with tarfile.open(path, mode) as t:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


class OsArchTest(unittest.TestCase):
    def test_returns_three_parts(self):
        os_name, arch, ext = ff.os_arch()
        self.assertIn(os_name, ("windows", "linux", "darwin"))
        self.assertTrue(arch)
        self.assertIn(ext, (".zip", ".tar.gz"))

    def test_windows_uses_zip(self):
        with mock.patch.object(ff.platform, "system", lambda: "Windows"), \
                mock.patch.object(ff.platform, "machine", lambda: "AMD64"):
            self.assertEqual(ff.os_arch(), ("windows", "amd64", ".zip"))

    def test_linux_uses_tar_gz(self):
        with mock.patch.object(ff.platform, "system", lambda: "Linux"), \
                mock.patch.object(ff.platform, "machine", lambda: "x86_64"):
            self.assertEqual(ff.os_arch(), ("linux", "amd64", ".tar.gz"))

    def test_arm_variants_are_recognised(self):
        """原版那份不认 armv7，会去下 amd64 的包。"""
        for machine, expected in (("armv7l", "arm"), ("armv7", "arm"),
                                  ("aarch64", "arm64"), ("arm64", "arm64")):
            with mock.patch.object(ff.platform, "system", lambda: "Linux"), \
                    mock.patch.object(ff.platform, "machine", lambda m=machine: m):
                self.assertEqual(ff.os_arch()[1], expected, machine)

    def test_unknown_machine_falls_back_to_amd64(self):
        with mock.patch.object(ff.platform, "system", lambda: "Linux"), \
                mock.patch.object(ff.platform, "machine", lambda: "sparc"):
            self.assertEqual(ff.os_arch()[1], "amd64")

    def test_default_binary_name_matches_the_platform(self):
        with mock.patch.object(ff.platform, "system", lambda: "Windows"):
            self.assertEqual(ff.default_binary_name(), "frpc.exe")
        with mock.patch.object(ff.platform, "system", lambda: "Linux"):
            self.assertEqual(ff.default_binary_name(), "frpc")


class ExecutableCheckTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-frpc-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, data, name="f.bin"):
        path = os.path.join(self.dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_windows_accepts_pe(self):
        p = self._write(MZ)
        with mock.patch.object(ff.platform, "system", lambda: "Windows"):
            self.assertTrue(ff.looks_like_executable(p))

    def test_windows_rejects_elf(self):
        p = self._write(ELF)
        with mock.patch.object(ff.platform, "system", lambda: "Windows"):
            self.assertFalse(ff.looks_like_executable(p))

    def test_linux_accepts_elf(self):
        p = self._write(ELF)
        with mock.patch.object(ff.platform, "system", lambda: "Linux"):
            self.assertTrue(ff.looks_like_executable(p))

    def test_linux_rejects_pe(self):
        p = self._write(MZ)
        with mock.patch.object(ff.platform, "system", lambda: "Linux"):
            self.assertFalse(ff.looks_like_executable(p))

    def test_darwin_accepts_macho(self):
        for magic in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
            p = self._write(magic + b"\x00" * 60)
            with mock.patch.object(ff.platform, "system", lambda: "Darwin"):
                self.assertTrue(ff.looks_like_executable(p))

    def test_text_is_never_an_executable(self):
        p = self._write(b"this is just text")
        for system in ("Windows", "Linux", "Darwin"):
            with mock.patch.object(ff.platform, "system", lambda s=system: s):
                self.assertFalse(ff.looks_like_executable(p))

    def test_missing_file(self):
        self.assertFalse(ff.looks_like_executable(os.path.join(self.dir, "nope")))


class ArchiveKindTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-kind-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_zip(self):
        p = os.path.join(self.dir, "a")
        make_zip(p, [("x", b"y")])
        self.assertEqual(ff.archive_kind(p), "zip")

    def test_gzip(self):
        p = os.path.join(self.dir, "a")
        make_tar(p, [("x", b"y")])
        self.assertEqual(ff.archive_kind(p), "tar")

    def test_plain_tar_is_not_detected_by_content(self):
        """裸 tar 没有魔数，按内容认不出来——调用方会退回按后缀判断。"""
        p = os.path.join(self.dir, "a")
        make_tar(p, [("x", b"y")], mode="w")
        self.assertEqual(ff.archive_kind(p), "")

    def test_plain_file(self):
        p = os.path.join(self.dir, "a")
        with open(p, "wb") as f:
            f.write(b"nothing to see")
        self.assertEqual(ff.archive_kind(p), "")

    def test_missing(self):
        self.assertEqual(ff.archive_kind(os.path.join(self.dir, "nope")), "")


class MemberMatchTest(unittest.TestCase):
    def test_exact_name(self):
        self.assertTrue(ff.is_target_member("frpc.exe", "frpc.exe"))
        self.assertTrue(ff.is_target_member("frpc", "frpc"))

    def test_nested_path(self):
        self.assertTrue(ff.is_target_member("frp_0.61.0_windows_amd64/frpc.exe",
                                            "frpc.exe"))

    def test_case_insensitive(self):
        self.assertTrue(ff.is_target_member("FRPC.EXE", "frpc.exe"))

    def test_versioned_variant_is_accepted(self):
        self.assertTrue(ff.is_target_member("frpc_0.5.0.exe", "frpc.exe"))

    def test_other_files_are_not_targets(self):
        for name in ("frps.exe", "frpc.toml", "LICENSE", "readme.md",
                     "frpc.exe.sha256"):
            self.assertFalse(ff.is_target_member(name, "frpc.exe"), name)

    def test_directory_entries_are_not_targets(self):
        self.assertFalse(ff.is_target_member("frpc/", "frpc"))
        self.assertFalse(ff.is_target_member("", "frpc"))


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mstl-test-extract-")
        self.dest = os.path.join(self.root, "out")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _src(self, name):
        return os.path.join(self.root, name)

    def test_zip_with_nested_binary(self):
        p = self._src("a.zip")
        make_zip(p, [("frp_0.61.0/frpc.exe", MZ), ("frp_0.61.0/frps.exe", MZ)])
        out = ff.extract_binary(p, self.dest, base_name="frpc.exe")
        self.assertTrue(out)
        self.assertEqual(os.path.basename(out), "frpc.exe")
        self.assertTrue(os.path.isfile(out))
        with open(out, "rb") as f:
            self.assertEqual(f.read(2), b"MZ")

    def test_tar_gz_with_nested_binary(self):
        p = self._src("a.tar.gz")
        make_tar(p, [("frp_0.61.0_linux_amd64/frpc", ELF)])
        # 内容是 ELF，所以要在「Linux」这个前提下校验
        with mock.patch.object(ff.platform, "system", lambda: "Linux"):
            out = ff.extract_binary(p, self.dest, base_name="frpc")
        self.assertTrue(out)

    def test_plain_tar_is_supported_too(self):
        """原来硬编码 "r:gz"，裸 tar 会解不开。"""
        p = self._src("a.tar")
        make_tar(p, [("frpc", ELF)], mode="w")
        with mock.patch.object(ff.platform, "system", lambda: "Linux"):
            out = ff.extract_binary(p, self.dest, base_name="frpc")
        self.assertTrue(out)

    def test_tar_bz2_is_supported_too(self):
        p = self._src("a.tar.bz2")
        make_tar(p, [("frpc", ELF)], mode="w:bz2")
        with mock.patch.object(ff.platform, "system", lambda: "Linux"):
            out = ff.extract_binary(p, self.dest, base_name="frpc")
        self.assertTrue(out)

    def test_creates_the_destination_directory(self):
        p = self._src("a.zip")
        make_zip(p, [("frpc.exe", MZ)])
        deeper = os.path.join(self.root, "x", "y")
        out = ff.extract_binary(p, deeper, base_name="frpc.exe")
        self.assertTrue(out)
        self.assertTrue(os.path.isdir(deeper))

    def test_archive_without_the_binary_fails(self):
        p = self._src("a.zip")
        make_zip(p, [("frps.exe", MZ), ("frpc.toml", b"x")])
        self.assertEqual(ff.extract_binary(p, self.dest, base_name="frpc.exe"), "")

    def test_wrong_platform_binary_is_refused_and_removed(self):
        """拿错平台的包要当场拒掉，而不是装上一个跑不起来的东西。"""
        p = self._src("a.zip")
        make_zip(p, [("frpc.exe", ELF)])
        with mock.patch.object(ff.platform, "system", lambda: "Windows"):
            out = ff.extract_binary(p, self.dest, base_name="frpc.exe")
        self.assertEqual(out, "")
        self.assertFalse(os.path.exists(os.path.join(self.dest, "frpc.exe")))

    def test_non_executable_content_is_refused(self):
        p = self._src("a.zip")
        make_zip(p, [("frpc.exe", b"just some text")])
        with mock.patch.object(ff.platform, "system", lambda: "Windows"):
            self.assertEqual(ff.extract_binary(p, self.dest, base_name="frpc.exe"), "")

    def test_corrupt_zip_fails_cleanly(self):
        p = self._src("bad.zip")
        with open(p, "wb") as f:
            f.write(b"PK\x03\x04 not really a zip")
        self.assertEqual(ff.extract_binary(p, self.dest, base_name="frpc.exe"), "")

    def test_exact_name_wins_over_a_variant(self):
        p = self._src("a.zip")
        make_zip(p, [("frpc_old.exe", b"not-an-exe"), ("frpc.exe", MZ)])
        with mock.patch.object(ff.platform, "system", lambda: "Windows"):
            out = ff.extract_binary(p, self.dest, base_name="frpc.exe")
        self.assertTrue(out)


class InstallFromFileTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mstl-test-install-")
        self.dest = os.path.join(self.root, "out")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_bare_binary_is_copied(self):
        src = os.path.join(self.root, "downloaded")
        with open(src, "wb") as f:
            f.write(ELF)
        with mock.patch.object(ff.platform, "system", lambda: "Linux"):
            out = ff.install_from_file(src, self.dest, base_name="frpc")
        self.assertTrue(out)
        self.assertTrue(os.path.isfile(out))

    def test_bare_text_file_is_refused(self):
        src = os.path.join(self.root, "notes.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("hello")
        self.assertEqual(ff.install_from_file(src, self.dest, base_name="frpc"), "")
        self.assertFalse(os.path.exists(os.path.join(self.dest, "frpc")))

    def test_archive_is_extracted(self):
        src = os.path.join(self.root, "frpc_windows_amd64.zip")
        make_zip(src, [("frpc.exe", MZ)])
        with mock.patch.object(ff.platform, "system", lambda: "Windows"):
            out = ff.install_from_file(src, self.dest, base_name="frpc.exe")
        self.assertTrue(out)

    def test_missing_source(self):
        self.assertEqual(ff.install_from_file(os.path.join(self.root, "nope"),
                                              self.dest), "")

    def test_extension_is_ignored_content_wins(self):
        """浏览器存下来的名字常常被改过，类型必须按内容判断。"""
        src = os.path.join(self.root, "frpc.dat")
        make_zip(src, [("frpc.exe", MZ)])
        with mock.patch.object(ff.platform, "system", lambda: "Windows"):
            out = ff.install_from_file(src, self.dest, base_name="frpc.exe")
        self.assertTrue(out)


class AssetNameTest(unittest.TestCase):
    def test_versioned_name(self):
        with mock.patch.object(ff.platform, "system", lambda: "Windows"), \
                mock.patch.object(ff.platform, "machine", lambda: "AMD64"):
            self.assertEqual(ff.expected_asset_name("frp", "0.61.0"),
                             "frp_0.61.0_windows_amd64.zip")

    def test_placeholder_when_version_unknown(self):
        with mock.patch.object(ff.platform, "system", lambda: "Linux"), \
                mock.patch.object(ff.platform, "machine", lambda: "x86_64"):
            self.assertEqual(ff.expected_asset_name("frpc"),
                             "frpc_<版本>_linux_amd64.tar.gz")

    def test_unversioned_name(self):
        with mock.patch.object(ff.platform, "system", lambda: "Windows"), \
                mock.patch.object(ff.platform, "machine", lambda: "AMD64"):
            self.assertEqual(ff.expected_asset_name("frpc").split("_")[1],
                             "<版本>")


if __name__ == "__main__":
    unittest.main()
