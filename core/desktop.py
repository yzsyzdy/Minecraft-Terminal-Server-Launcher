"""桌面捕获：把 Windows 程序当成「屏幕」来看、来点。

带宽是这里的主要约束。全屏 1080p 一帧 PNG 约 870 KB，12 fps 就是 10 MB/s，
放到浏览器里基本没法用。所以做了三层削减：

1. **降采样**：按 `scale` 把画面缩到 1280 宽以内（默认 0.6），像素少一半以上。
2. **分块差分**：画面切成 32×32 的小块，只在前一帧基础上**逐块比较**，
   没变的块一个字节都不发。静止的界面（大部分时间）带宽接近 0。
3. **按块编码**：把一行里相邻的变化块合并成一条「条带」，每条单独编码成
   PNG。小图比大图更容易压，也避免每块一个 HTTP/JSON 头。

这一层只管抓帧和转发输入，不管权限。开关在命令行（`--allow-desktop`）。
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Optional

from . import wincapture
from .png import encode_rgba

# 相邻两帧最小间隔，约 10 fps。再高对「看界面」没意义，带宽线性上涨。
MIN_FRAME_INTERVAL = 1.0 / 10.0
# 差分粒度。32 像素是折中：太小导致条带碎、每帧几十个 PNG；太大则
TILE = 32
# 缩放上限：宽度超过它就先缩，长边一致按比例。
MAX_WIDTH = 1280
# 每个会话最多记住多少帧的全量像素（只留最新一帧，用于差分）
_KEYFRAME_EVERY = 90          # 约 9 秒补一次全量，防客户端状态漂移


@dataclass
class TileRun:
    """一条横向连续的「变化条带」。"""

    x: int
    y: int
    width: int
    height: int
    png: bytes


@dataclass
class Frame:
    width: int
    height: int
    seq: int
    # 变化的条带；空列表 + key=True 表示「全都没变」
    tiles: list[TileRun] = field(default_factory=list)
    key: bool = False          # True = 这一帧是整幅全量
    full_png: bytes = b""      # key 帧时给整幅，省得客户端拼


def _downscale(rgba: bytes, width: int, height: int,
               scale: float) -> tuple[int, int, bytes]:
    """按整数倍抽样降采样。scale>=1 或尺寸没变就原样返回。

    必须用**整数步长**（宽高同一个 step），否则不是缩放而是裁剪：步长算成 1
    的时候输出尺寸变小、内容却只截了左上角一块——这正是之前「显示不全」的原因。

    抽样而不是平均：平均要在 Python 里跑 207 万次乘加，实测 740 ms 一帧，
    满足不了 10 fps；整数步长 + 扩展切片只要 18 ms。
    """
    if scale >= 1.0 or width <= 0 or height <= 0:
        return width, height, rgba
    step = max(1, int(round(1.0 / max(0.05, scale))))
    if width // step > MAX_WIDTH:
        step = max(step, (width + MAX_WIDTH - 1) // MAX_WIDTH)
    target_w = width // step
    target_h = height // step
    if target_w < 1 or target_h < 1:
        return width, height, rgba
    if target_w == width and target_h == height:
        return width, height, rgba

    stride = width * 4
    alpha = bytes([255]) * target_w
    # 一次切片取到「每隔 step 个像素」的通道：4*step 是字节步长。
    # 不能用 `line[0::4][::step]`——那样得到的是 ceil(width/step) 个元素，
    # 而 target_w 是 floor(width/step)，宽不是 step 整数倍时两者差一个，
    # 赋值给扩展切片就会抛 "attempt to assign bytes of size N to extended
    # slice of size N-1"（用户实测 565 vs 564）。所以最后统一截到 target_w。
    out = bytearray()
    for ty in range(target_h):
        y = ty * step
        line = rgba[y * stride:(y + 1) * stride]
        r = line[0::4 * step][:target_w]
        g = line[1::4 * step][:target_w]
        b = line[2::4 * step][:target_w]
        tile = bytearray(target_w * 4)
        tile[0::4] = r
        tile[1::4] = g
        tile[2::4] = b
        tile[3::4] = alpha
        out.extend(tile)
    return target_w, target_h, bytes(out)


def _tile_slice(rgba: bytes, width: int, x: int, y: int, w: int, h: int) -> bytes:
    """取一块像素（行内切片，避免逐像素 Python 循环）。"""
    stride = width * 4
    parts = []
    for row in range(y, y + h):
        start = row * stride + x * 4
        parts.append(rgba[start:start + w * 4])
    return b"".join(parts)


def _changed_tiles(prev: bytes, current: bytes, width: int,
                   height: int) -> list[tuple[int, int]]:
    """返回所有「和上一帧不同」的块坐标 (tile_col, tile_row)。

    先按整条行带比较：界面大部分时间是不动的，20 条带里通常 0 条有变化，
    直接返回。只有确实变了的条带才逐块细查——原来是每块每行都比一遍
    （720 块 × 32 行 = 23040 次切片），实测 30 ms 一帧，占了差不多三分之一的
    帧时间。
    """
    if prev is None or len(prev) != len(current):
        return [(0, 0)]
    stride = width * 4
    changed: list[tuple[int, int]] = []
    cols = (width + TILE - 1) // TILE
    for ty in range(0, height, TILE):
        rows = min(TILE, height - ty)
        prev_band = prev[ty * stride:(ty + rows) * stride]
        cur_band = current[ty * stride:(ty + rows) * stride]
        if prev_band == cur_band:
            continue                      # 整条带没动，最常见的情况
        for tx in range(cols):
            x0 = tx * TILE * 4
            x1 = min(width * 4, x0 + TILE * 4)
            for row in range(rows):
                start = row * stride
                if prev_band[start + x0:start + x1] != cur_band[start + x0:start + x1]:
                    changed.append((tx, ty // TILE))
                    break
    return changed


class DesktopSession:
    """一次「正在看某个窗口 / 屏幕」的会话。"""

    def __init__(self, target: str = "screen", hwnd: int = 0,
                 scale: float = 0.6) -> None:
        self.target = target
        self.hwnd = hwnd
        self.scale = max(0.2, min(1.0, float(scale or 0.6)))
        self.seq = 0
        self._prev: Optional[bytes] = None
        self._prev_size: tuple[int, int] = (0, 0)
        self._last_at = 0.0
        self._forced_full = True

    # -- 抓帧 --------------------------------------------------------------

    def frame(self, force: bool = False) -> Optional[Frame]:
        """抓一帧并算出变化条带。没有变化时返回 tiles 为空的 Frame。"""
        if not wincapture.IS_WINDOWS:
            return None
        import time
        now = time.time()
        if not force and now - self._last_at < MIN_FRAME_INTERVAL:
            return None
        # 缩放交给 GDI 的 StretchBlt（HALFTONE 面积平均），不再用「按整数步长
        # 抽样」：抽样只能取 1/2、1/3、1/4，中间倍率（比如 70%）根本做不出来，
        # 而且「每 n 行取一行」会把文字笔画砍掉。StretchBlt 支持任意倍率，
        # 缩到 60% 文字依然完整，代价只是多一次 GDI 内存拷贝（约 20 ms）。
        target_w = target_h = 0
        if self.scale < 1.0:
            probe = wincapture.window_capture_rect(self.hwnd)                 if self.target == "window" else None
            if self.target == "window":
                if not self.hwnd or not probe:
                    return None
                src_w, src_h = probe[2], probe[3]
            else:
                src_w, src_h = wincapture.virtual_screen()[2:]
            if src_w > 0 and src_h > 0:
                target_w = max(1, int(src_w * self.scale))
                target_h = max(1, int(src_h * self.scale))
        if self.target == "window":
            if not self.hwnd:
                return None
            shot = wincapture.capture_window(self.hwnd, target_w, target_h)
        else:
            shot = wincapture.capture_screen(target_w, target_h)
        if not shot:
            return None
        width, height, rgba = shot

        key = self._forced_full or self._prev is None \
            or (width, height) != self._prev_size
        if key:
            tiles = [TileRun(0, 0, width, height, encode_rgba(width, height, rgba, 1))]
            full_png = tiles[0].png
        else:
            changed = _changed_tiles(self._prev, rgba, width, height)
            tiles = self._encode_runs(changed, rgba, width, height)
            full_png = b""

        self._prev = rgba
        self._prev_size = (width, height)
        self._last_at = now
        self.seq += 1
        self._forced_full = False
        frame = Frame(width, height, self.seq, tiles, key, full_png)
        if self.seq % _KEYFRAME_EVERY == 0:
            self._forced_full = True      # 下一帧补一次全量
        return frame

    def _encode_runs(self, changed: list[tuple[int, int]], rgba: bytes,
                     width: int, height: int) -> list[TileRun]:
        """把变化块按行合并成条带，每条编码成一张小 PNG。"""
        if not changed:
            return []
        by_row: dict[int, list[int]] = {}
        for col, row in changed:
            by_row.setdefault(row, []).append(col)
        runs: list[TileRun] = []
        for row, cols in sorted(by_row.items()):
            cols.sort()
            start = prev = cols[0]
            for col in cols[1:] + [None]:
                if col is not None and col == prev + 1:
                    prev = col
                    continue
                x = start * TILE
                w = min(width - x, (prev - start + 1) * TILE)
                y = row * TILE
                h = min(height - y, TILE)
                piece = _tile_slice(rgba, width, x, y, w, h)
                runs.append(TileRun(x, y, w, h, encode_rgba(w, h, piece, 1)))
                if col is not None:
                    start = prev = col
        return runs

    def request_keyframe(self) -> None:
        self._forced_full = True

    def close(self) -> None:
        self._prev = None


__all__ = ["DesktopSession", "Frame", "TileRun", "TILE", "MIN_FRAME_INTERVAL",
           "MAX_WIDTH", "_changed_tiles", "_downscale"]
