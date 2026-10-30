"""把 Minecraft 的 region 文件渲染成 2D 俯视地图。

一条链路：`.mca` 头表 → 解压 chunk → 读 NBT → 用 Heightmaps.MOTION_BLOCKING
拿到每一列的地表高度 → 在对应 section 的 palette 里查那一个方块 → 按方块着色
→ 拼成 512×512 的 PNG（一个 region 一张图）。

只依赖标准库：NBT 解析和 PNG 编码都是手写的，因为这个项目承诺零外部依赖。
性能上的取舍写在 `render_region()` 里：只查「地表那一格」，不扫整根柱子。
"""

from __future__ import annotations

import gzip
import os
import struct
import zlib
from typing import Any, Optional

REGION_SIZE = 512          # 一个 region 32×32 个 chunk，每 chunk 16 格
CHUNK = 16

# 常见地表方块的颜色。查不到的名字退到按高度着色，所以这张表不需要很全。
BLOCK_COLORS: dict[str, tuple[int, int, int]] = {
    "minecraft:grass_block": (106, 156, 68),
    "minecraft:grass_path": (148, 121, 65),
    "minecraft:dirt_path": (148, 121, 65),
    "minecraft:dirt": (134, 96, 67),
    "minecraft:coarse_dirt": (120, 85, 58),
    "minecraft:podzol": (94, 68, 37),
    "minecraft:mycelium": (111, 99, 105),
    "minecraft:moss_block": (89, 123, 47),
    "minecraft:mud": (60, 57, 61),
    "minecraft:stone": (125, 125, 125),
    "minecraft:cobblestone": (110, 110, 110),
    "minecraft:deepslate": (80, 80, 84),
    "minecraft:tuff": (108, 109, 102),
    "minecraft:calcite": (223, 224, 220),
    "minecraft:gravel": (131, 127, 126),
    "minecraft:sand": (219, 207, 163),
    "minecraft:red_sand": (190, 102, 33),
    "minecraft:sandstone": (216, 203, 155),
    "minecraft:clay": (160, 166, 179),
    "minecraft:water": (59, 90, 175),
    "minecraft:ice": (160, 200, 235),
    "minecraft:packed_ice": (141, 180, 214),
    "minecraft:blue_ice": (116, 167, 214),
    "minecraft:snow": (240, 246, 250),
    "minecraft:snow_block": (240, 246, 250),
    "minecraft:powder_snow": (232, 240, 248),
    "minecraft:lava": (207, 92, 25),
    "minecraft:bedrock": (60, 60, 60),
    "minecraft:obsidian": (21, 18, 30),
    "minecraft:netherrack": (111, 54, 52),
    "minecraft:soul_sand": (81, 62, 50),
    "minecraft:crimson_nylium": (130, 40, 44),
    "minecraft:warped_nylium": (43, 112, 106),
    "minecraft:end_stone": (219, 222, 158),
    "minecraft:terracotta": (152, 94, 67),
    "minecraft:oak_leaves": (60, 122, 43),
    "minecraft:birch_leaves": (110, 145, 70),
    "minecraft:spruce_leaves": (52, 88, 51),
    "minecraft:jungle_leaves": (70, 132, 38),
    "minecraft:acacia_leaves": (104, 132, 38),
    "minecraft:dark_oak_leaves": (52, 96, 40),
    "minecraft:mangrove_leaves": (80, 130, 44),
    "minecraft:cherry_leaves": (226, 164, 200),
    "minecraft:azalea_leaves": (102, 128, 52),
    "minecraft:flowering_azalea_leaves": (118, 132, 70),
    "minecraft:oak_planks": (162, 130, 78),
    "minecraft:spruce_planks": (114, 84, 48),
    "minecraft:birch_planks": (196, 179, 123),
    "minecraft:stone_bricks": (122, 121, 122),
    "minecraft:bricks": (150, 97, 83),
    "minecraft:white_wool": (234, 236, 237),
    "minecraft:black_wool": (25, 25, 29),
    "minecraft:farmland": (79, 55, 32),
    "minecraft:rooted_dirt": (144, 103, 76),
    "minecraft:moss_carpet": (89, 123, 47),
    "minecraft:short_grass": (106, 156, 68),
    "minecraft:tall_grass": (106, 156, 68),
    "minecraft:fern": (90, 140, 60),
    "minecraft:lily_pad": (58, 110, 46),
    "minecraft:sea_lantern": (170, 200, 195),
    "minecraft:glowstone": (190, 160, 90),
}

# 高度着色兜底：低处偏绿、高处偏灰白，用来在认不出方块时也能看出地形
_HEIGHT_RAMP = (
    (0, (52, 74, 52)),
    (62, (72, 110, 60)),
    (80, (106, 140, 74)),
    (110, (150, 150, 130)),
    (150, (176, 176, 176)),
    (256, (235, 235, 235)),
)


# ---------------------------------------------------------------- NBT

class _NbtReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, count: int) -> bytes:
        chunk = self.data[self.pos:self.pos + count]
        if len(chunk) != count:
            raise ValueError("NBT 提前结束")
        self.pos += count
        return chunk

    def number(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(size))[0]

    def string(self) -> str:
        length = self.number(">H")
        return self.take(length).decode("utf-8", "replace")

    def payload(self, tag: int) -> Any:
        if tag == 1:
            return self.number(">b")
        if tag == 2:
            return self.number(">h")
        if tag == 3:
            return self.number(">i")
        if tag == 4:
            return self.number(">q")
        if tag == 5:
            return self.number(">f")
        if tag == 6:
            return self.number(">d")
        if tag == 7:
            return self.take(self.number(">i"))
        if tag == 8:
            return self.string()
        if tag == 9:
            item = self.number(">b")
            count = self.number(">i")
            return [self.payload(item) for _ in range(max(0, count))]
        if tag == 10:
            result: dict[str, Any] = {}
            while True:
                child = self.number(">b")
                if child == 0:
                    return result
                name = self.string()
                result[name] = self.payload(child)
        if tag == 11:
            count = self.number(">i")
            return list(struct.unpack(">" + "i" * count, self.take(4 * count)))
        if tag == 12:
            count = self.number(">i")
            return list(struct.unpack(">" + "q" * count, self.take(8 * count)))
        raise ValueError(f"未知 NBT 类型 {tag}")


def parse_nbt(data: bytes) -> dict:
    reader = _NbtReader(data)
    tag = reader.number(">b")
    if tag != 10:
        raise ValueError("根标签不是 Compound")
    reader.string()             # 根名字，通常是空串
    return reader.payload(10)


# ------------------------------------------------------------- region

def _chunk_payload(data: bytes, x: int, z: int) -> Optional[dict]:
    index = (x & 31) + (z & 31) * 32
    located = struct.unpack_from(">I", data, index * 4)[0]
    if located == 0:
        return None
    offset = (located >> 8) * 4096
    if offset + 5 > len(data):
        return None
    length = struct.unpack_from(">I", data, offset)[0]
    compression = data[offset + 4]
    raw = data[offset + 5: offset + 4 + length]
    try:
        if compression == 1:
            raw = gzip.decompress(raw)
        elif compression == 2:
            raw = zlib.decompress(raw)
        elif compression != 3:
            return None
    except (OSError, zlib.error):
        return None
    try:
        return parse_nbt(raw)
    except (ValueError, struct.error):
        return None


def _height_at(chunk: dict, x: int, z: int) -> Optional[int]:
    """从 MOTION_BLOCKING 里取 (x,z) 这一列的地表高度。

    Minecraft 的 BitStorage 不跨 long：每个 long 装 `64 // bits` 个值。
    值是相对世界底部的偏移（1.18 主世界底部是 -64），绝对 y 要再加回去，
    换算见 `_column_min_y()`。
    """
    heightmaps = chunk.get("Heightmaps") or {}
    packed = heightmaps.get("MOTION_BLOCKING") or heightmaps.get("WORLD_SURFACE")
    if not isinstance(packed, list) or not packed:
        return None
    bits = (len(packed) * 64) // 256
    if bits <= 0:
        return None
    per_long = 64 // bits
    index = (z & 15) * 16 + (x & 15)
    word = packed[index // per_long]
    shift = (index % per_long) * bits
    return (word >> shift) & ((1 << bits) - 1)


def _column_min_y(chunk: dict) -> int:
    """这个 chunk 的最低 section 对应的高度（主世界 -64，老世界 0）。"""
    sections = chunk.get("sections")
    if not isinstance(sections, list):
        return 0
    lowest = None
    for section in sections:
        if isinstance(section, dict) and isinstance(section.get("Y"), int):
            lowest = section["Y"] if lowest is None else min(lowest, section["Y"])
    return 0 if lowest is None else lowest * 16


_AIR = ("minecraft:air", "minecraft:cave_air", "minecraft:void_air")


def surface_at(chunk: dict, x: int, z: int) -> tuple[Optional[int], Optional[str]]:
    """(x,z) 这一列的地表 y 与方块名。

    高度图的数值有两种口径（绝对 y，或相对世界底部的偏移），这里两种都试：
    先按绝对 y 查，查到空气就加上世界底部再查一次。每个 chunk 最多多一次查表，
    比猜错整张图要好。
    """
    raw = _height_at(chunk, x, z)
    if raw is None:
        return None, None
    min_y = _column_min_y(chunk)
    candidates = [raw - 1]
    if min_y:
        candidates.append(raw - 1 + min_y)
    sections = {section.get("Y"): section for section in chunk.get("sections") or []
                if isinstance(section, dict)}
    for y in candidates:
        if y < 0:
            continue
        section = sections.get(y >> 4)
        if not section:
            continue
        block = _palette_name(section, x & 15, y & 15, z & 15)
        if block and block not in _AIR:
            return y, block
    return candidates[-1], None


def _entry_name(entry: Any) -> Optional[str]:
    """palette 项见过三种写法：{"Name": ...}、纯字符串、以及 Paper 的
    {"": ...} / {"id": ..., "properties": {...}}。"""
    if isinstance(entry, str):
        return entry.split("[", 1)[0]
    if isinstance(entry, dict):
        for key in ("Name", "id", ""):
            value = entry.get(key)
            if isinstance(value, str) and value:
                return value.split("[", 1)[0]
    return None


def _palette_name(section: dict, local_x: int, local_y: int,
                  local_z: int) -> Optional[str]:
    states = section.get("block_states")
    if not isinstance(states, dict):
        return None
    palette = states.get("palette")
    if not isinstance(palette, list) or not palette:
        return None
    if len(palette) == 1:
        return _entry_name(palette[0])
    data = states.get("data")
    if not isinstance(data, list) or not data:
        return None
    bits = max(4, (len(palette) - 1).bit_length())
    per_long = 64 // bits
    index = local_y * 256 + local_z * 16 + local_x
    word = data[index // per_long]
    shift = (index % per_long) * bits
    entry_index = (word >> shift) & ((1 << bits) - 1)
    if entry_index >= len(palette):
        return None
    return _entry_name(palette[entry_index])


def surface_block(chunk: dict, x: int, z: int) -> Optional[str]:
    return surface_at(chunk, x, z)[1]


# -------------------------------------------------------------- color

def color_for(block: Optional[str], y: int) -> tuple[int, int, int]:
    if block and block in BLOCK_COLORS:
        return BLOCK_COLORS[block]
    if block:
        if block.endswith("_leaves"):
            return (72, 122, 52)
        if block.endswith("_planks"):
            return (162, 130, 78)
        if block.endswith("_wool") or block.endswith("_concrete"):
            return (170, 170, 170)
        if "water" in block:
            return BLOCK_COLORS["minecraft:water"]
        if "snow" in block:
            return BLOCK_COLORS["minecraft:snow_block"]
    for limit, rgb in _HEIGHT_RAMP:
        if y <= limit:
            return rgb
    return _HEIGHT_RAMP[-1][1]


# --------------------------------------------------------------- PNG

def _png_bytes(width: int, height: int, rgba: bytearray) -> bytes:
    """最小的 RGBA PNG 编码器（标准库 zlib）。"""
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
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


# ------------------------------------------------------------ render

def render_region(path: str) -> Optional[bytes]:
    """把一个 .mca 渲染成 512×512 的 PNG 字节；读不出来返回 None。

    每个 chunk 只做 256 次「地表那一格」查询，不遍历 384 格高的整根柱子——
    这是能在一两秒内出图的关键。
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if len(data) < 8192:
        return None

    rgba = bytearray(REGION_SIZE * REGION_SIZE * 4)
    heights: list[list[Optional[int]]] = [[None] * REGION_SIZE
                                          for _ in range(REGION_SIZE)]

    for chunk_z in range(32):
        for chunk_x in range(32):
            chunk = _chunk_payload(data, chunk_x, chunk_z)
            if not chunk:
                continue
            for local_z in range(16):
                row_z = chunk_z * 16 + local_z
                base = row_z * REGION_SIZE
                for local_x in range(16):
                    x = chunk_x * 16 + local_x
                    y, block = surface_at(chunk, local_x, local_z)
                    if y is None:
                        continue
                    heights[row_z][x] = y
                    r, g, b = color_for(block, y)
                    # 西北打光的浮雕效果：和左上方高度差决定明暗
                    shade = 0
                    if x > 0 and heights[row_z][x - 1] is not None:
                        shade += (y - heights[row_z][x - 1])
                    if row_z > 0 and heights[row_z - 1][x] is not None:
                        shade += (y - heights[row_z - 1][x])
                    factor = max(0.75, min(1.25, 1 + shade * 0.04))
                    offset = (base + x) * 4
                    rgba[offset] = min(255, int(r * factor))
                    rgba[offset + 1] = min(255, int(g * factor))
                    rgba[offset + 2] = min(255, int(b * factor))
                    rgba[offset + 3] = 255

    return _png_bytes(REGION_SIZE, REGION_SIZE, rgba)


# ------------------------------------------------------------- worlds

def _region_files(world_dir: str) -> list[tuple[int, int, str]]:
    region_dir = os.path.join(world_dir, "region")
    if not os.path.isdir(region_dir):
        return []
    found = []
    for name in os.listdir(region_dir):
        if not name.endswith(".mca"):
            continue
        parts = name[:-4].split(".")
        if len(parts) != 3:
            continue
        try:
            found.append((int(parts[1]), int(parts[2]),
                          os.path.join(region_dir, name)))
        except ValueError:
            continue
    found.sort()
    return found


def list_worlds(instance_dir: str) -> list[dict]:
    """找出实例下所有带 region 的世界。

    Paper 1.19+ 把主世界放在 `world/dimensions/minecraft/overworld/region`，
    老版本是 `world/region`；这里递归找，深度限制在 5 层内。
    """
    worlds = []
    for current, dirs, _files in os.walk(instance_dir):
        depth = current[len(instance_dir):].count(os.sep)
        if depth > 5 or ".mstl-map" in current:
            dirs[:] = []
            continue
        if os.path.basename(current) != "region":
            continue
        world_dir = os.path.dirname(current)
        regions = _region_files(world_dir)
        if not regions:
            continue
        rel = os.path.relpath(world_dir, instance_dir).replace(os.sep, "/")
        worlds.append({
            "id": rel,
            "name": os.path.basename(world_dir),
            "path": rel,
            "regions": [[x, z] for x, z, _ in regions],
        })
    worlds.sort(key=lambda item: item["id"])
    return worlds


__all__ = [
    "BLOCK_COLORS", "CHUNK", "REGION_SIZE",
    "parse_nbt", "surface_block", "color_for", "render_region", "list_worlds",
]
