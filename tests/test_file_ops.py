"""压缩、加密包与文件搬运的回归测试。

和 test_archive.py 的分工：那边测的是「解压不许越界」，这边测的是新加的能力
本身——打包、7z + 密码、复制 / 移动的边界。
"""

import os
import shutil
import tempfile
import unittest
import zipfile

import core.archive as archive
import core.files as fileops
from core.errors import CoreError


def has_py7zr():
    try:
        import py7zr  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


class ArchiveCreateTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mstl-test-create-")
        os.makedirs(os.path.join(self.root, "pack", "inner"))
        with open(os.path.join(self.root, "pack", "a.txt"), "w",
                  encoding="utf-8") as f:
            f.write("hello")
        with open(os.path.join(self.root, "pack", "inner", "b.txt"), "w",
                  encoding="utf-8") as f:
            f.write("world")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_zip_roundtrip_keeps_tree(self):
        dest = archive.create_archive(self.root, ["pack"], "pack.zip")
        self.assertEqual(dest, "pack.zip")

        result = archive.extract(self.root, "pack.zip", "out")
        self.assertEqual(result.extracted, 2)
        with open(os.path.join(self.root, "out", "pack", "inner", "b.txt"),
                  encoding="utf-8") as f:
            self.assertEqual(f.read(), "world")

    def test_create_refuses_existing_without_overwrite(self):
        archive.create_archive(self.root, ["pack"], "pack.zip")
        with self.assertRaises(CoreError) as ctx:
            archive.create_archive(self.root, ["pack"], "pack.zip")
        self.assertEqual(ctx.exception.key, "web.error.already_exists")

        archive.create_archive(self.root, ["pack"], "pack.zip", overwrite=True)

    def test_bad_format_rejected(self):
        with self.assertRaises(CoreError) as ctx:
            archive.create_archive(self.root, ["pack"], "pack.rar", fmt="rar")
        self.assertEqual(ctx.exception.key, "web.error.bad_archive_format")

    @unittest.skipUnless(has_py7zr(), "需要可选的 py7zr")
    def test_7z_with_password(self):
        archive.create_archive(self.root, ["pack"], "pack.7z",
                               fmt="7z", password="pw123")

        with self.assertRaises(CoreError) as ctx:
            archive.extract(self.root, "pack.7z", "bad", password="wrong")
        self.assertEqual(ctx.exception.key, "web.error.archive_password")

        ok = archive.extract(self.root, "pack.7z", "ok", password="pw123")
        self.assertEqual(ok.extracted, 2)

    @unittest.skipUnless(has_py7zr(), "需要可选的 py7zr")
    def test_7z_detected_by_signature(self):
        archive.create_archive(self.root, ["pack"], "pack.7z", fmt="7z")
        self.assertEqual(
            archive.archive_kind(os.path.join(self.root, "pack.7z")), "7z")


class FileTransferTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mstl-test-move-")
        os.makedirs(os.path.join(self.root, "a", "deep"))
        with open(os.path.join(self.root, "a", "deep", "x.txt"), "w",
                  encoding="utf-8") as f:
            f.write("x")
        os.makedirs(os.path.join(self.root, "b"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_copy_file_and_dir(self):
        self.assertEqual(fileops.copy_entry(self.root, "a/deep/x.txt", "b"),
                         "b/x.txt")
        self.assertEqual(fileops.copy_entry(self.root, "a/deep", "b"), "b/deep")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "b", "deep",
                                                    "x.txt")))
        # 原位置还在
        self.assertTrue(os.path.isfile(os.path.join(self.root, "a", "deep",
                                                    "x.txt")))

    def test_move_removes_source(self):
        self.assertEqual(fileops.move_entry(self.root, "a/deep/x.txt", "b"),
                         "b/x.txt")
        self.assertFalse(os.path.exists(
            os.path.join(self.root, "a", "deep", "x.txt")))

    def test_copy_into_self_rejected(self):
        with self.assertRaises(CoreError) as ctx:
            fileops.copy_entry(self.root, "a/deep", "a/deep")
        self.assertEqual(ctx.exception.key, "web.error.copy_into_self")

    def test_existing_target_needs_overwrite(self):
        fileops.copy_entry(self.root, "a/deep/x.txt", "b")
        with self.assertRaises(CoreError) as ctx:
            fileops.copy_entry(self.root, "a/deep/x.txt", "b")
        self.assertEqual(ctx.exception.key, "web.error.already_exists")
        fileops.copy_entry(self.root, "a/deep/x.txt", "b", overwrite=True)

    def test_root_cannot_be_moved(self):
        with self.assertRaises(CoreError) as ctx:
            fileops.move_entry(self.root, "", "b")
        self.assertEqual(ctx.exception.key, "web.error.root_locked")


class ImageDetectTest(unittest.TestCase):
    def test_image_flags(self):
        self.assertTrue(fileops.is_probably_image("a.PNG"))
        self.assertTrue(fileops.is_probably_image("b.webp"))
        self.assertFalse(fileops.is_probably_image("c.txt"))
        self.assertEqual(fileops.image_mime("d.jpeg"), "image/jpeg")
        self.assertEqual(fileops.image_mime("e.bin"), "")


if __name__ == "__main__":
    unittest.main()
