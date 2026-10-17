"""导入压缩包时的解压闸门。

`core/transfer.py` 处理的正是用户从网上下回来的整合包——最需要防的就是
这类输入。这里只测 `_safe_extract` 自己负责的部分：zip slip、符号链接、
Windows 风格的目录项，以及压缩炸弹的两道闸门（成员数、总字节数）。
"""

import os
import shutil
import tempfile
import unittest
import zipfile

import core.transfer as transfer
from core.errors import CoreError


class RecordingReporter:
    """只记不画，用来断言 core 汇报了什么。"""

    def __init__(self):
        self.warnings = []
        self.points = []

    def log(self, key, **kwargs):
        pass

    def warn(self, key, **kwargs):
        self.warnings.append((key, kwargs))

    def progress(self, **kwargs):
        self.points.append(kwargs)


class SafeExtractTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mstl-test-tr-")
        self.dest = os.path.join(self.root, "out")
        os.makedirs(self.dest, exist_ok=True)
        self.reporter = RecordingReporter()
        # 闸门常量在调用时从 transfer 的模块命名空间读，测试里可以调小
        self._saved = (transfer.MAX_MEMBERS, transfer.MAX_TOTAL_BYTES)

    def tearDown(self):
        transfer.MAX_MEMBERS, transfer.MAX_TOTAL_BYTES = self._saved
        shutil.rmtree(self.root, ignore_errors=True)

    # -- 构造 --------------------------------------------------------------

    def _zip(self, name, entries, compression=zipfile.ZIP_STORED):
        path = os.path.join(self.root, name)
        with zipfile.ZipFile(path, "w", compression) as z:
            for entry in entries:
                if isinstance(entry, tuple):
                    z.writestr(entry[0], entry[1])
                else:                                  # 已经是 ZipInfo
                    z.writestr(entry, b"")
        return path

    def _extract(self, path):
        transfer._safe_extract(path, self.dest, self.reporter)

    def _path(self, *parts):
        return os.path.join(self.dest, *parts)

    # -- 正常路径 ----------------------------------------------------------

    def test_extracts_nested_directories(self):
        p = self._zip("ok.zip", [("a/b/c.txt", b"hi")])
        self._extract(p)
        with open(self._path("a", "b", "c.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hi")

    def test_progress_counts_real_bytes(self):
        p = self._zip("ok.zip", [("a.txt", b"12345")])
        self._extract(p)
        self.assertTrue(self.reporter.points, "一个进度点都没汇报")
        self.assertEqual(self.reporter.points[-1].get("done"), True)
        counts = [(pt.get("current"), pt.get("total")) for pt in self.reporter.points]
        self.assertIn((5, 5), counts)

    def test_windows_style_directory_entry(self):
        """Windows 打的 zip 把目录项存成 `sub\\`，ZipInfo.is_dir() 只认 `/`。

        旧实现会把 `sub` 建成零字节普通文件，下一个成员 `sub/f.txt` 需要
        `sub` 是目录，于是 FileExistsError 冒出去被上层当成「压缩包损坏」。
        """
        p = self._zip("win.zip", [("sub\\", b""), ("sub/f.txt", b"hi")])
        self._extract(p)
        self.assertTrue(os.path.isdir(self._path("sub")))
        with open(self._path("sub", "f.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hi")

    # -- 越界 --------------------------------------------------------------

    def test_refuses_parent_traversal(self):
        p = self._zip("evil.zip", [("../escaped.txt", b"pwned")])
        self._extract(p)
        self.assertFalse(os.path.exists(os.path.join(self.root, "escaped.txt")))
        self.assertEqual([w[0] for w in self.reporter.warnings],
                         ["import.path_traversal"])

    def test_refuses_deep_traversal(self):
        p = self._zip("evil.zip", [("a/b/../../../escaped.txt", b"pwned")])
        self._extract(p)
        self.assertFalse(os.path.exists(os.path.join(self.root, "escaped.txt")))

    def test_refuses_symlink_members(self):
        """成员名本身合法，但它是条指向外部的软链，后面的成员可以写穿它。

        软链必须被丢掉。后续的 `link/passwd` 名字合法，会落成目标目录里
        一个真实目录下的文件——那是允许的，关键是没有东西跑到目录外面。
        """
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        p = self._zip("link.zip", [info, ("link/passwd", b"pwned")])
        self._extract(p)
        self.assertFalse(os.path.islink(self._path("link")))
        self.assertEqual(self.reporter.warnings,
                         [("import.path_traversal", {"name": "link"})])
        self.assertFalse(os.path.exists(os.path.join(self.root, "passwd")))

    # -- 压缩炸弹闸门 ------------------------------------------------------

    def test_member_count_gate_is_a_hard_error(self):
        transfer.MAX_MEMBERS = 2
        p = self._zip("many.zip", [(f"f{i}.txt", b"x") for i in range(3)])
        with self.assertRaises(CoreError) as ctx:
            self._extract(p)
        self.assertEqual(ctx.exception.key, "web.error.extract_too_many")
        self.assertFalse(os.path.exists(self._path("f0.txt")), "拒绝前不该落地任何文件")

    def test_declared_total_bytes_gate_is_a_hard_error(self):
        transfer.MAX_TOTAL_BYTES = 100
        p = self._zip("big.zip", [("a.txt", b"x" * 200)])
        with self.assertRaises(CoreError) as ctx:
            self._extract(p)
        self.assertEqual(ctx.exception.key, "web.error.extract_too_large")
        self.assertFalse(os.path.exists(self._path("a.txt")))

    def test_declared_total_is_summed_across_members(self):
        """每个成员单独都不超标，加起来超了也要拦。"""
        transfer.MAX_TOTAL_BYTES = 100
        p = self._zip("sum.zip", [("a.txt", b"x" * 60), ("b.txt", b"x" * 60)])
        with self.assertRaises(CoreError) as ctx:
            self._extract(p)
        self.assertEqual(ctx.exception.key, "web.error.extract_too_large")

    def test_within_the_budget_extracts_everything(self):
        transfer.MAX_TOTAL_BYTES = 1000
        p = self._zip("ok.zip", [("a.txt", b"x" * 10), ("b.txt", b"y" * 10)])
        self._extract(p)
        self.assertEqual(os.path.getsize(self._path("a.txt")), 10)
        self.assertEqual(os.path.getsize(self._path("b.txt")), 10)

    def test_gates_share_the_constants_with_core_archive(self):
        """两条导入路径必须用同一套闸门，不能各写一份。"""
        import core.archive as archive
        self.assertIs(transfer.MAX_MEMBERS, archive.MAX_MEMBERS)
        self.assertIs(transfer.MAX_TOTAL_BYTES, archive.MAX_TOTAL_BYTES)


class CommitImportNameTest(unittest.TestCase):
    """导入时落盘的目录名必须先过 validate_instance_name。

    面板把用户填的实例名原样交给导入流程，而 `commit_import` 以前直接
    `os.path.join(servers_dir, name)`：填 `../x` 就往 servers 外面写，填绝对
    路径连 servers 目录都不看。这条路径必须和新实例同名，走同一套校验。
    """

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="mstl-test-ci-")
        self.servers = os.path.join(self.root, "servers")
        self.temp = os.path.join(self.root, "temp")
        os.makedirs(self.servers, exist_ok=True)
        os.makedirs(self.temp, exist_ok=True)
        with open(os.path.join(self.temp, "server.jar"), "wb") as f:
            f.write(b"jar")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _plan(self, suggested="pack"):
        return transfer.ImportPlan(
            zip_path=os.path.join(self.root, "pack.zip"),
            temp_dir=self.temp, project_dir=self.root,
            candidates=["server.jar"], suggested_name=suggested)

    def test_relative_escape_is_rejected(self):
        for name in ("../escape", "..\\escape", "a/b", "a\\b", "..", "."):
            with self.assertRaises(CoreError) as ctx:
                transfer.commit_import(self._plan(), "server.jar",
                                       self.servers, name=name)
            self.assertIn(ctx.exception.key, ("web.error.bad_instance_name",
                                              "web.error.name_required"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "escape")))

    def test_absolute_path_is_rejected(self):
        target = os.path.join(self.root, "absolute_escape")
        with self.assertRaises(CoreError):
            transfer.commit_import(self._plan(), "server.jar",
                                   self.servers, name=target)
        self.assertFalse(os.path.exists(target))

    def test_reserved_windows_name_is_rejected(self):
        with self.assertRaises(CoreError) as ctx:
            transfer.commit_import(self._plan(), "server.jar",
                                   self.servers, name="CON")
        self.assertEqual(ctx.exception.key, "web.error.bad_instance_name")

    def test_normal_name_still_lands_under_servers(self):
        path = transfer.commit_import(self._plan(), "server.jar",
                                      self.servers, name="我的服务器")
        self.assertEqual(os.path.dirname(path), self.servers)
        self.assertTrue(os.path.isfile(os.path.join(path, "server.jar")))

    def test_zip_filename_is_the_default_name(self):
        """没填名字时用压缩包文件名（plan_import 已把非法字符换成下划线）。"""
        path = transfer.commit_import(self._plan(suggested="pack 2024"),
                                      "server.jar", self.servers)
        self.assertEqual(os.path.dirname(path), self.servers)
        self.assertEqual(os.path.basename(path), "pack 2024")


if __name__ == "__main__":
    unittest.main()
