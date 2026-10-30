"""自定义背景的纯函数与边界。

图片落盘、路径推导、类型嗅探这几段都留在模块级函数里，就是为了能在不启
HTTP 服务的情况下测：背景图是唯一一个「用户上传的字节会被原样发回浏览器」
的面板资源，类型判断错一次就等于让面板托管任意内容。
"""

import os
import shutil
import tempfile
import unittest

from web.server import (
    BG_BASENAME,
    background_extension,
    background_path,
    background_version,
    clear_background,
    _clamp_int,
)


class BackgroundTypeTest(unittest.TestCase):
    def test_png(self):
        self.assertEqual(background_extension(b"\x89PNG\r\n\x1a\nxxxx"), ".png")

    def test_jpeg(self):
        self.assertEqual(background_extension(b"\xff\xd8\xff\xe0xxxx"), ".jpg")

    def test_gif(self):
        self.assertEqual(background_extension(b"GIF89a...."), ".gif")

    def test_webp(self):
        head = b"RIFF\x24\x00\x00\x00WEBPVP8 "
        self.assertEqual(background_extension(head), ".webp")

    def test_rejects_other_bytes(self):
        self.assertEqual(background_extension(b"<svg xmlns="), "")
        self.assertEqual(background_extension(b"not an image at all"), "")
        self.assertEqual(background_extension(b""), "")

    def test_webp_needs_both_markers(self):
        # RIFF 也是 wav 的头，少了 WEBP 标记不能当成图片
        self.assertEqual(background_extension(b"RIFF\x24\x00\x00\x00WAVEfmt "), "")


class BackgroundStorageTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-bg-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, ext, payload=b"x"):
        path = os.path.join(self.dir, BG_BASENAME + ext)
        with open(path, "wb") as f:
            f.write(payload)
        return path

    def test_path_empty_when_not_set(self):
        self.assertEqual(background_path(self.dir), "")
        self.assertEqual(background_version(self.dir), 0)

    def test_path_found_by_extension(self):
        self.write(".jpg")
        self.assertTrue(background_path(self.dir).endswith(".jpg"))

    def test_version_tracks_mtime(self):
        self.write(".png")
        self.assertGreater(background_version(self.dir), 0)

    def test_clear_removes_every_extension(self):
        self.write(".png")
        self.write(".webp")
        clear_background(self.dir)
        self.assertEqual(background_path(self.dir), "")
        leftovers = [n for n in os.listdir(self.dir) if n.startswith(BG_BASENAME)]
        self.assertEqual(leftovers, [])

    def test_clear_on_empty_dir_is_safe(self):
        clear_background(self.dir)     # 不该抛


class ClampTest(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(_clamp_int(999, 12, 0, 40), 40)
        self.assertEqual(_clamp_int(-5, 12, 0, 40), 0)
        self.assertEqual(_clamp_int("26", 12, 0, 40), 26)

    def test_bad_values_fall_back(self):
        self.assertEqual(_clamp_int("abc", 12, 0, 40), 12)
        self.assertEqual(_clamp_int(None, 100, 40, 100), 100)


if __name__ == "__main__":
    unittest.main()
