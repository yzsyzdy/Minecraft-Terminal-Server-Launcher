"""两套隧道的安装入口。

原版 frp 与 OpenFrp 的 frpc 处理已经合并到 frp/frpc_files，这里盯住两边
对外的入口行为没变，并且确认合并时把「校验可执行文件」也带给了 OpenFrp
（它原来会把用户传错的 txt 当成 frpc 装好）。
"""

import io
import os
import shutil
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

import core.frp as frpops
from frp import frpc_files
from frp import tunnel_manager as frp_tm
from openfrp import tunnel_manager as of_tm

MZ = b"MZ" + b"\x00" * 62
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


class FrpInstallTest(unittest.TestCase):
    """原版 frp：用户手动下载 zip / 裸二进制后安装。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-frptm-")
        self.win = mock.patch.object(frpc_files.platform, "system",
                                     lambda: "Windows")
        self.win.start()

    def tearDown(self):
        self.win.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_installs_from_a_release_zip(self):
        src = os.path.join(self.dir, "frp_0.61.0_windows_amd64.zip")
        make_zip(src, [("frp_0.61.0_windows_amd64/frpc.exe", MZ),
                       ("frp_0.61.0_windows_amd64/frps.exe", MZ),
                       ("frp_0.61.0_windows_amd64/frpc.toml", b"x")])
        out = frp_tm.install_frpc_from_file(src, self.dir)
        self.assertTrue(out)
        self.assertEqual(os.path.basename(out), "frpc.exe")
        self.assertEqual(os.path.abspath(out),
                         os.path.abspath(frp_tm.get_frpc_bin_path(self.dir)))

    def test_installs_from_a_bare_binary(self):
        src = os.path.join(self.dir, "downloaded")
        with open(src, "wb") as f:
            f.write(MZ)
        out = frp_tm.install_frpc_from_file(src, self.dir)
        self.assertTrue(out)

    def test_rejects_a_text_file(self):
        src = os.path.join(self.dir, "notes.txt")
        with open(src, "w", encoding="utf-8") as f:
            f.write("hello")
        self.assertIsNone(frp_tm.install_frpc_from_file(src, self.dir))

    def test_rejects_a_wrong_platform_archive(self):
        """Windows 上下到 linux 的包，要当场拒掉而不是装上一个跑不起来的。"""
        src = os.path.join(self.dir, "frp_linux.tar.gz")
        make_tar(src, [("frp_0.61.0_linux_amd64/frpc", ELF)])
        self.assertIsNone(frp_tm.install_frpc_from_file(src, self.dir))
        self.assertFalse(os.path.exists(frp_tm.get_frpc_bin_path(self.dir)))

    def test_missing_source(self):
        self.assertIsNone(frp_tm.install_frpc_from_file(
            os.path.join(self.dir, "nope.zip"), self.dir))

    def test_expected_asset_name_keeps_the_version(self):
        self.assertEqual(frp_tm.expected_asset_name("0.61.0"),
                         "frp_0.61.0_windows_amd64.zip")

    def test_expected_asset_name_has_a_placeholder(self):
        self.assertIn("<版本>", frp_tm.expected_asset_name())

    def test_bin_path_is_platform_specific(self):
        self.assertTrue(frp_tm.get_frpc_bin_path(self.dir).endswith("frpc.exe"))


class FrpPanelUploadTest(unittest.TestCase):
    """面板上传那条路（core.frp.install_from_stream）。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-upload-")
        self.win = mock.patch.object(frpc_files.platform, "system",
                                     lambda: "Windows")
        self.win.start()

    def tearDown(self):
        self.win.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_upload_a_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("frpc.exe", MZ)
        payload = buf.getvalue()
        out = frpops.install_from_stream(self.dir, io.BytesIO(payload),
                                         len(payload))
        self.assertTrue(os.path.isfile(out))

    def test_empty_upload_is_rejected(self):
        from core.errors import CoreError
        with self.assertRaises(CoreError):
            frpops.install_from_stream(self.dir, io.BytesIO(b""), 0)

    def test_oversized_upload_is_rejected(self):
        from core.errors import CoreError
        with self.assertRaises(CoreError):
            frpops.install_from_stream(self.dir, io.BytesIO(b"x" * 10), 10,
                                       max_bytes=5)

    def test_garbage_upload_is_rejected(self):
        from core.errors import CoreError
        payload = b"this is not frpc"
        with self.assertRaises(CoreError):
            frpops.install_from_stream(self.dir, io.BytesIO(payload),
                                       len(payload))

    def test_incomplete_upload_is_rejected(self):
        from core.errors import CoreError
        with self.assertRaises(CoreError):
            frpops.install_from_stream(self.dir, io.BytesIO(MZ[:10]), 999)

    def test_no_temp_files_left_behind(self):
        import glob
        payload = b"garbage"
        try:
            frpops.install_from_stream(self.dir, io.BytesIO(payload), len(payload))
        except Exception:  # noqa: BLE001
            pass
        leftovers = glob.glob(os.path.join(tempfile.gettempdir(), "mstl-frpc-*"))
        self.assertEqual(leftovers, [])


class OpenFrpExtractTest(unittest.TestCase):
    """OpenFrp 侧：合并后也要能解出 frpc，并且开始校验可执行文件。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-oftm-")
        self.win = mock.patch.object(frpc_files.platform, "system",
                                     lambda: "Windows")
        self.win.start()

    def tearDown(self):
        self.win.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _archive(self, name, entries):
        path = os.path.join(self.dir, "openfrp", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        make_zip(path, entries)
        return path

    def test_extracts_the_binary(self):
        path = self._archive("frpc_windows_amd64.zip", [("frpc.exe", MZ)])
        out = of_tm._extract_frpc_if_needed(path, self.dir)
        self.assertTrue(out)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(os.path.basename(out), "frpc.exe")

    def test_extracts_from_a_nested_directory(self):
        path = self._archive("frpc_windows_amd64.zip",
                             [("frpc_windows_amd64/frpc.exe", MZ)])
        out = of_tm._extract_frpc_if_needed(path, self.dir)
        self.assertTrue(out)
        self.assertTrue(os.path.isfile(out))

    def test_rejects_a_non_executable(self):
        """合并前这里不校验：传个 txt 进来也会被当成 frpc 装好。"""
        path = self._archive("frpc_windows_amd64.zip",
                             [("frpc.exe", b"just text")])
        self.assertEqual(of_tm._extract_frpc_if_needed(path, self.dir), "")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "openfrp", "frpc.exe")))

    def test_does_not_dump_the_whole_archive(self):
        """只挑那一个文件出来，不往数据目录里倒一整个第三方包。"""
        path = self._archive("frpc_windows_amd64.zip",
                             [("frpc.exe", MZ), ("LICENSE", b"x"),
                              ("docs/readme.md", b"y")])
        self.assertTrue(of_tm._extract_frpc_if_needed(path, self.dir))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "openfrp", "LICENSE")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "openfrp", "docs")))

    def test_missing_binary_fails(self):
        path = self._archive("frpc_windows_amd64.zip", [("other.exe", MZ)])
        self.assertEqual(of_tm._extract_frpc_if_needed(path, self.dir), "")

    def test_asset_name_is_unversioned(self):
        self.assertEqual(of_tm.expected_asset_name(), "frpc_windows_amd64.zip")

    def test_local_path_matches_the_asset_name(self):
        self.assertEqual(os.path.basename(of_tm.get_frpc_local_path(self.dir)),
                         "frpc_windows_amd64.zip")


class MergeIntegrityTest(unittest.TestCase):
    """两边现在共用同一份实现——别再各自长回去。"""

    def test_both_sides_use_the_shared_module(self):
        import inspect
        for func in (frp_tm.install_frpc_from_file, frp_tm._extract_frpc,
                     of_tm._extract_frpc_if_needed, frp_tm._os_arch,
                     of_tm._os_arch):
            src = inspect.getsource(func)
            self.assertIn("frpc_files", src, func.__qualname__)

    def test_arch_detection_is_shared(self):
        with mock.patch.object(frpc_files.platform, "system", lambda: "Linux"), \
                mock.patch.object(frpc_files.platform, "machine", lambda: "armv7l"):
            # 合并前原版 frp 会把 armv7 当成 amd64
            self.assertEqual(frp_tm._os_arch()[1], "arm")
            self.assertEqual(of_tm._os_arch()[1], "arm")


if __name__ == "__main__":
    unittest.main()
