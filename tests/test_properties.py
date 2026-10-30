"""server.properties 的解析与回写。

这个文件在游戏侧是按 ISO-8859-1 读的，中文必须写成 \\uXXXX，所以「写完还能
原样读回来」是这里最要紧的一条。
"""

import os
import tempfile
import unittest

from core import properties as props


class ParseTest(unittest.TestCase):
    def test_basic_pairs_and_comments(self):
        text = "# 注释\nmotd=Hello\nmax-players=20\n! 另一种注释\n"
        self.assertEqual(props.parse(text), {"motd": "Hello", "max-players": "20"})

    def test_colon_and_space_separators(self):
        text = "level-name: world\npvp true\n"
        self.assertEqual(props.parse(text), {"level-name": "world", "pvp": "true"})

    def test_continuation_lines(self):
        text = "motd=第一行\\\n  第二行\n"
        self.assertEqual(props.parse(text), {"motd": "第一行第二行"})

    def test_escapes_and_unicode(self):
        text = r"motd=\u4f60\u597d\=x" + "\n"
        self.assertEqual(props.parse(text), {"motd": "你好=x"})

    def test_empty_and_missing_file(self):
        self.assertEqual(props.parse(""), {})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(props.load(tmp), {})
            self.assertFalse(props.exists(tmp))


class UpdateTest(unittest.TestCase):
    def test_keeps_comments_order_and_other_keys(self):
        original = ("# 手写注释\n"
                    "motd=Old\n"
                    "difficulty=easy\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "server.properties")
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(original)
            merged = props.update(tmp, {"motd": "New"})
            self.assertEqual(merged["motd"], "New")
            self.assertEqual(merged["difficulty"], "easy")
            raw = open(path, encoding="utf-8").read()
            self.assertIn("# 手写注释", raw)
            self.assertNotIn("motd=Old", raw)

    def test_appends_missing_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "server.properties")
            open(path, "w", encoding="utf-8").write("motd=Hi\n")
            merged = props.update(tmp, {"pvp": "false"})
            self.assertEqual(merged["pvp"], "false")
            self.assertEqual(merged["motd"], "Hi")

    def test_chinese_is_written_as_unicode_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "server.properties")
            open(path, "w", encoding="utf-8").write("motd=X\n")
            props.update(tmp, {"motd": "你好"})
            raw = open(path, encoding="utf-8").read()
            self.assertIn(r"\u4f60\u597d", raw)
            # 再读回来必须还原，否则游戏里就是乱码
            self.assertEqual(props.load(tmp)["motd"], "你好")

    def test_creates_file_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            merged = props.update(tmp, {"server-port": "25566"})
            self.assertEqual(merged["server-port"], "25566")
            self.assertTrue(os.path.isfile(os.path.join(tmp, "server.properties")))

    def test_backslash_and_newline_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "server.properties"), "w").write("motd=x\n")
            props.update(tmp, {"motd": "a\\b\tc"})
            self.assertEqual(props.load(tmp)["motd"], "a\\b\tc")


if __name__ == "__main__":
    unittest.main()
