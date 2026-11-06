"""把 PNG 编码抽出来共用。

地图瓦片和桌面截帧都需要「把像素变成 PNG」，而项目承诺零第三方依赖，
所以这里放一份基于标准库 zlib 的实现，谁要谁 import。
"""

from __future__ import annotations

import struct
import zlib


def encode_rgba(width: int, height: int, rgba: bytes | bytearray,
                level: int = 6) -> bytes:
    """把 RGBA 像素编码成 PNG。

    level 是 zlib 的压缩等级：静态地图用 6，实时截帧用 1——1080p 一帧在
    level 6 下要几百毫秒，实时看画面时那点体积不值得。
    """
    raw = bytearray()
    stride = width * 4
    for row in range(height):
        raw.append(0)                       # filter: none
        raw.extend(rgba[row * stride:(row + 1) * stride])

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), level))
            + chunk(b"IEND", b""))


__all__ = ["encode_rgba"]
