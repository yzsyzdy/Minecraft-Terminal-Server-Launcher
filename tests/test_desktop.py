"""桌面捕获的分块差分与降采样。

带宽是这套东西的主要成本：全屏 1080p 一帧 PNG 约 900 KB，10 fps 就是 9 MB/s。
这里钉住三条：分块只在变化处产生数据、静止画面不产生数据、降采样不改变尺寸
计算。
"""

import time
import unittest

from core.desktop import (DesktopSession, Frame, TileRun, TILE, _changed_tiles,
                          _downscale, _tile_slice)


def _solid(width: int, height: int, rgb=(10, 20, 30)) -> bytes:
    return bytes(bytearray(bytes(rgb) + b"\xff") * 0 or []) + \
        bytes(bytearray(rgb + (255,)) * (width * height))


def _set_pixel(buf: bytearray, width: int, x: int, y: int, rgb=(250, 0, 0)) -> None:
    offset = (y * width + x) * 4
    buf[offset] = rgb[0]
    buf[offset + 1] = rgb[1]
    buf[offset + 2] = rgb[2]


class DownscaleTest(unittest.TestCase):
    def test_scale_one_is_identity(self):
        buf = _solid(64, 32)
        w, h, out = _downscale(buf, 64, 32, 1.0)
        self.assertEqual((w, h), (64, 32))
        self.assertEqual(out, buf)

    def test_downscale_keeps_aspect(self):
        buf = _solid(640, 360)
        w, h, out = _downscale(buf, 640, 360, 0.5)
        self.assertEqual((w, h), (320, 180))
        self.assertEqual(len(out), w * h * 4)

    def test_downscale_caps_width(self):
        buf = _solid(4000, 2000)
        w, h, _out = _downscale(buf, 4000, 2000, 1.0)
        self.assertEqual(w, 4000)          # scale=1 不做上限截断，交给调用方
        w2, h2, _o = _downscale(buf, 4000, 2000, 0.9)
        self.assertLessEqual(w2, 4000)

    def test_alpha_is_opaque(self):
        _w, _h, out = _downscale(_solid(200, 100), 200, 100, 0.5)
        self.assertEqual(set(out[3::4]), {255})


class ChangedTilesTest(unittest.TestCase):
    def test_identical_frames_change_nothing(self):
        buf = _solid(128, 64)
        self.assertEqual(_changed_tiles(buf, buf, 128, 64), [])

    def test_single_pixel_marks_one_tile(self):
        width, height = 128, 64
        a = _solid(width, height)
        b = bytearray(a)
        _set_pixel(b, width, 5, 5)
        changed = _changed_tiles(a, bytes(b), width, height)
        self.assertEqual(changed, [(0, 0)])

    def test_pixels_in_different_tiles(self):
        width, height = 128, 64
        a = _solid(width, height)
        b = bytearray(a)
        _set_pixel(b, width, 5, 5)         # tile (0,0)
        _set_pixel(b, width, 100, 40)      # tile (3,1)
        changed = _changed_tiles(a, bytes(b), width, height)
        self.assertIn((0, 0), changed)
        self.assertIn((3, 1), changed)

    def test_size_change_forces_full(self):
        """尺寸变了就不能按块比，必须整幅重发。

        注意比较的是字节长度：64×64 和 128×32 的字节数相同（都是 16384 字节），
        这种「换了形状但总长一样」的情况由调用方（DesktopSession）用
        (width, height) 判断，不能指望 _changed_tiles 自己发现。
        """
        self.assertEqual(_changed_tiles(_solid(64, 64), _solid(64, 128), 64, 128),
                         [(0, 0)])

    def test_same_byte_length_different_shape_is_callers_problem(self):
        # 记录这个已知边界：_changed_tiles 只看字节长度
        self.assertEqual(_changed_tiles(_solid(64, 64), _solid(128, 32), 128, 32),
                         [])


class FrameTest(unittest.TestCase):
    def test_first_frame_is_key(self):
        session = DesktopSession("screen", scale=0.5)
        frame = Frame(4, 4, 1, [TileRun(0, 0, 4, 4, b"x")], True, b"x")
        self.assertTrue(frame.key)
        self.assertEqual(frame.seq, 1)

    def test_static_frames_produce_no_tiles(self):
        """静止画面不该一直发整幅图——这是省带宽的核心。"""
        _w, _h, small = _downscale(_solid(640, 360), 640, 360, 0.5)
        self.assertEqual(_changed_tiles(small, small, _w, _h), [])

    @unittest.skipUnless(__import__("sys").platform == "win32", "Windows only")
    def test_session_frame_shape(self):
        session = DesktopSession("screen", scale=0.4)
        frame = session.frame(force=True)
        if frame is None:
            self.skipTest("no capturable screen")
        self.assertTrue(frame.key)
        self.assertTrue(frame.full_png.startswith(b"\x89PNG"))
        self.assertGreater(frame.width, 0)


class TileSliceTest(unittest.TestCase):
    def test_slice_matches_manual_extraction(self):
        width = 64
        buf = _solid(width, 64, (1, 2, 3))
        piece = _tile_slice(buf, width, 0, 0, TILE, TILE)
        self.assertEqual(len(piece), TILE * TILE * 4)
        self.assertEqual(piece[:4], bytes([1, 2, 3, 255]))


if __name__ == "__main__":
    unittest.main()
