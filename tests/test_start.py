"""入口层的测试：目前只有一处，就是标准输出的编码兜底。

这条用例守的是一个真实故障：CI 的 stdout 是管道，Windows 上退化成
cp1252、LANG=C 的 Linux 上退化成 ascii，此时任何一句中文都会抛
UnicodeEncodeError，而这条路径恰好是 `MSTL.exe > log.txt` 最常见的用法。
"""

import io
import sys
import unittest

import start


class Utf8StdoutTest(unittest.TestCase):
    """_ensure_utf8_stdout 必须把窄编码的流切到 UTF-8。"""

    def setUp(self):
        self._saved = (sys.stdout, sys.stderr)

    def tearDown(self):
        sys.stdout, sys.stderr = self._saved

    def _cp1252_stream(self):
        """造一个和英文 Windows 重定向输出时同款的流。"""
        return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")

    def test_chinese_cannot_be_written_before_the_fix(self):
        """先证明这个故障是真的：不修的话中文就是写不出去。"""
        stream = self._cp1252_stream()
        with self.assertRaises(UnicodeEncodeError):
            stream.write("[JDK] 正在下载 Java 21")

    def test_reconfigures_stdout_and_stderr(self):
        sys.stdout = self._cp1252_stream()
        sys.stderr = self._cp1252_stream()

        start._ensure_utf8_stdout()

        self.assertEqual("utf-8", sys.stdout.encoding)
        self.assertEqual("utf-8", sys.stderr.encoding)
        # errors 必须是 replace：编码不了的字符顶多显示成问号，
        # 不该为了打印一行字把进程带走
        self.assertEqual("replace", sys.stdout.errors)

    def test_chinese_is_writable_after_the_fix(self):
        sys.stdout = stream = self._cp1252_stream()
        start._ensure_utf8_stdout()

        stream.write("[JDK] 正在下载 Java 21（darwin/aarch64）...")
        stream.flush()

        self.assertEqual("[JDK] 正在下载 Java 21（darwin/aarch64）...",
                         stream.buffer.getvalue().decode("utf-8"))

    def test_streams_without_reconfigure_are_skipped(self):
        """被替换成没有 reconfigure 的对象时不能炸。"""

        class Plain:
            def write(self, text):
                return len(text)

        sys.stdout = Plain()
        sys.stderr = Plain()
        start._ensure_utf8_stdout()  # 不抛就是通过


if __name__ == "__main__":
    unittest.main()
