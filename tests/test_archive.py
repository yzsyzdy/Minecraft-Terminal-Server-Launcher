"""在线解压的安全性。

这段代码面对的是用户上传的压缩包，唯一不能出错的地方就是「不许写到目标
目录外面去」，以及别被炸弹包拖死。
"""

import io
import os
import shutil
import tarfile
import tempfile
import unittest
import zipfile

import core.archive as archive
from core.errors import CoreError


def make_zip(path, entries):
    """entries: [(名字, 内容 bytes)]"""
    with zipfile.ZipFile(path, "w") as z:
        for name, data in entries:
            z.writestr(name, data)


def make_tar(path, entries, mode="w:gz"):
    with tarfile.open(path, mode) as t:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


class ArchiveKindTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-arc-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_detects_zip_by_content(self):
        p = os.path.join(self.dir, "a.zip")
        make_zip(p, [("x.txt", b"hi")])
        self.assertEqual(archive.archive_kind(p), "zip")

    def test_detects_tar_gz_by_content(self):
        p = os.path.join(self.dir, "a.tar.gz")
        make_tar(p, [("x.txt", b"hi")])
        self.assertEqual(archive.archive_kind(p), "gzip")

    def test_detects_plain_tar(self):
        p = os.path.join(self.dir, "a.tar")
        make_tar(p, [("x.txt", b"hi")], mode="w")
        self.assertEqual(archive.archive_kind(p), "tar")

    def test_unknown_content(self):
        p = os.path.join(self.dir, "a.dat")
        with open(p, "wb") as f:
            f.write(b"not an archive")
        self.assertEqual(archive.archive_kind(p), "")

    def test_missing_file(self):
        self.assertEqual(archive.archive_kind(os.path.join(self.dir, "nope.zip")), "")

    def test_name_recognition_ignores_case(self):
        self.assertTrue(archive.is_archive_name("Server.ZIP"))
        self.assertTrue(archive.is_archive_name("x.tar.gz"))
        self.assertFalse(archive.is_archive_name("server.jar"))
        self.assertFalse(archive.is_archive_name("notes.txt"))


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mstl-test-ext-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _path(self, rel):
        return os.path.join(self.root, rel.replace("/", os.sep))

    def test_extracts_zip_in_place(self):
        make_zip(self._path("a.zip"), [("dir/f.txt", b"hello")])
        result = archive.extract(self.root, "a.zip")
        self.assertEqual(result.extracted, 1)
        self.assertEqual(result.total_bytes, 5)
        with open(self._path("dir/f.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hello")

    def test_extracts_into_subdirectory(self):
        make_zip(self._path("a.zip"), [("f.txt", b"x")])
        archive.extract(self.root, "a.zip", "out")
        self.assertTrue(os.path.isfile(self._path("out/f.txt")))

    def test_does_not_overwrite_by_default(self):
        make_zip(self._path("a.zip"), [("f.txt", b"old")])
        archive.extract(self.root, "a.zip")
        with open(self._path("f.txt"), "wb") as f:
            f.write(b"new")
        make_zip(self._path("b.zip"), [("f.txt", b"fromzip")])
        result = archive.extract(self.root, "b.zip")
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.extracted, 0)
        with open(self._path("f.txt"), "rb") as f:
            self.assertEqual(f.read(), b"new")

    def test_overwrite_flag_replaces(self):
        make_zip(self._path("a.zip"), [("f.txt", b"old")])
        archive.extract(self.root, "a.zip")
        make_zip(self._path("b.zip"), [("f.txt", b"new")])
        result = archive.extract(self.root, "b.zip", overwrite=True)
        self.assertEqual(result.extracted, 1)
        with open(self._path("f.txt"), "rb") as f:
            self.assertEqual(f.read(), b"new")

    # -- 不许跑到目录外面 --------------------------------------------------

    def test_refuses_parent_traversal(self):
        make_zip(self._path("evil.zip"), [("../escaped.txt", b"pwned")])
        result = archive.extract(self.root, "evil.zip")
        self.assertEqual(result.refused, 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "..", "escaped.txt")))
        self.assertFalse(os.path.exists(self._path("escaped.txt")))

    def test_refuses_deep_traversal(self):
        make_zip(self._path("evil.zip"),
                 [("a/b/../../../escaped.txt", b"pwned")])
        result = archive.extract(self.root, "evil.zip")
        self.assertEqual(result.refused, 1)

    def test_refuses_absolute_paths(self):
        make_zip(self._path("evil.zip"), [("/tmp/mstl-absolute-escape.txt", b"x")])
        archive.extract(self.root, "evil.zip")
        self.assertFalse(os.path.exists("/tmp/mstl-absolute-escape.txt"))

    def test_refuses_windows_drive_paths(self):
        make_zip(self._path("evil.zip"), [("C:/mstl-escape.txt", b"x")])
        result = archive.extract(self.root, "evil.zip")
        self.assertEqual(result.refused, 1)

    def test_traversal_inside_a_subdirectory_dest(self):
        make_zip(self._path("evil.zip"), [("../../escaped.txt", b"x")])
        result = archive.extract(self.root, "evil.zip", "out")
        self.assertEqual(result.refused, 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, "escaped.txt")))

    def test_refuses_symlinks_in_zip(self):
        p = self._path("link.zip")
        with zipfile.ZipFile(p, "w") as z:
            info = zipfile.ZipInfo("link")
            info.external_attr = (0o120777 << 16)     # 符号链接
            z.writestr(info, "/etc/passwd")
        result = archive.extract(self.root, "link.zip")
        self.assertEqual(result.refused, 1)
        self.assertFalse(os.path.lexists(self._path("link")))

    def test_refuses_symlinks_in_tar(self):
        p = self._path("link.tar")
        with tarfile.open(p, "w") as t:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            t.addfile(info)
        result = archive.extract(self.root, "link.tar")
        self.assertEqual(result.refused, 1)

    def test_refuses_hardlinks_in_tar(self):
        p = self._path("hard.tar")
        with tarfile.open(p, "w") as t:
            target = tarfile.TarInfo("real.txt")
            target.size = 1
            t.addfile(target, io.BytesIO(b"x"))
            link = tarfile.TarInfo("hard")
            link.type = tarfile.LNKTYPE
            link.linkname = "real.txt"
            t.addfile(link)
        result = archive.extract(self.root, "hard.tar")
        self.assertGreaterEqual(result.refused, 1)

    # -- 边界与错误 --------------------------------------------------------

    def test_missing_archive_raises(self):
        with self.assertRaises(CoreError):
            archive.extract(self.root, "nope.zip")

    def test_non_archive_raises(self):
        with open(self._path("plain.dat"), "wb") as f:
            f.write(b"nope")
        with self.assertRaises(CoreError):
            archive.extract(self.root, "plain.dat")

    def test_member_count_limit_is_a_hard_error(self):
        """超过成员上限直接拒掉整包，而不是解一半——压缩炸弹防护就该这样。"""
        make_zip(self._path("many.zip"), [(f"f{i}.txt", b"x") for i in range(6)])
        with self.assertRaises(CoreError):
            archive.extract(self.root, "many.zip", max_members=3)

    def test_total_bytes_limit_is_a_hard_error(self):
        make_zip(self._path("big.zip"), [(f"f{i}.txt", b"x" * 100) for i in range(5)])
        with self.assertRaises(CoreError):
            archive.extract(self.root, "big.zip", max_bytes=250)

    def test_extraction_stops_within_the_byte_budget(self):
        make_zip(self._path("ok.zip"), [(f"f{i}.txt", b"x" * 10) for i in range(5)])
        result = archive.extract(self.root, "ok.zip", max_bytes=1000)
        self.assertEqual(result.extracted, 5)
        self.assertEqual(result.total_bytes, 50)

    def test_zip_without_a_local_file_header_is_not_an_archive(self):
        """空 zip 只有中央目录结束记录（PK\\x05\\x06），按内容是认不出来的。"""
        make_zip(self._path("empty.zip"), [])
        self.assertEqual(archive.archive_kind(self._path("empty.zip")), "")
        with self.assertRaises(CoreError):
            archive.extract(self.root, "empty.zip")

    def test_tar_gz_round_trip(self):
        make_tar(self._path("a.tar.gz"), [("d/f.txt", b"hi")])
        result = archive.extract(self.root, "a.tar.gz")
        self.assertEqual(result.extracted, 1)
        with open(self._path("d/f.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hi")


if __name__ == "__main__":
    unittest.main()
