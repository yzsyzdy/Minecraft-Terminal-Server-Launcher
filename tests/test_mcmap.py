"""地图渲染里最容易错的两块：高度图的位打包、palette 的三种写法。

真实存档不一定在测试机上，所以这里用最小的人工 chunk 结构钉住解析逻辑。
"""

import os
import tempfile
import unittest

from core import mcmap


def packed_heights(values, bits=4):
    """按 BitStorage 的规则（不跨 long）把高度值打包成 long 列表。"""
    per_long = 64 // bits
    words = [0] * ((len(values) + per_long - 1) // per_long)
    for index, value in enumerate(values):
        words[index // per_long] |= (value & ((1 << bits) - 1)) << (
            (index % per_long) * bits)
    return words


def make_chunk(height=5, block="minecraft:grass_block", min_section=0):
    """8 位的 heightmap 需要 32 个 long；这里只造 4 位的 16 个 long 世界高度 5。"""
    values = [height] * 256
    data = [0] * (16 * 16)          # section 内 4096 个位置，bits=4 → 每个 long 16 个
    # 让 (0,0) 这一列 y=height-1 的 palette 指向 1
    local_y = (height - 1) & 15
    data_index = local_y * 256
    if data_index // 16 < len(data):
        data[data_index // 16] |= 1 << ((data_index % 16) * 4)
    return {
        "Heightmaps": {"MOTION_BLOCKING": packed_heights(values)},
        "sections": [{
            "Y": min_section,
            "block_states": {
                "palette": ["minecraft:air", block],
                "data": data,
            },
        }],
    }


class HeightmapTest(unittest.TestCase):
    def test_reads_packed_value(self):
        chunk = make_chunk(height=5)
        self.assertEqual(mcmap._height_at(chunk, 0, 0), 5)

    def test_bit_storage_does_not_span_longs(self):
        values = list(range(256))
        # _height_at 从 long 数量反推位宽：32 个 long 正好对应 8 位
        words = packed_heights(values, bits=8)
        chunk = {"Heightmaps": {"MOTION_BLOCKING": words}}
        for index in (0, 15, 16, 255):
            x, z = index % 16, index // 16
            self.assertEqual(mcmap._height_at(chunk, x, z), index)

    def test_missing_heightmap_is_none(self):
        self.assertIsNone(mcmap._height_at({"sections": []}, 0, 0))

    def test_min_section_derives_world_bottom(self):
        self.assertEqual(mcmap._column_min_y(make_chunk(min_section=-4)), -64)
        self.assertEqual(mcmap._column_min_y(make_chunk(min_section=0)), 0)


class PaletteTest(unittest.TestCase):
    def test_three_palette_shapes(self):
        self.assertEqual(mcmap._entry_name({"Name": "minecraft:stone"}),
                         "minecraft:stone")
        self.assertEqual(mcmap._entry_name("minecraft:stone[snowy=false]"),
                         "minecraft:stone")
        self.assertEqual(mcmap._entry_name({"": "minecraft:air"}),
                         "minecraft:air")
        self.assertEqual(mcmap._entry_name({"id": "minecraft:oak_leaves",
                                            "properties": {}}),
                         "minecraft:oak_leaves")

    def test_single_entry_palette_needs_no_data(self):
        section = {"Y": 0, "block_states": {"palette": ["minecraft:stone"]}}
        self.assertEqual(mcmap._palette_name(section, 3, 3, 3), "minecraft:stone")

    def test_surface_at_returns_block(self):
        chunk = make_chunk(height=5, block="minecraft:grass_block")
        y, block = mcmap.surface_at(chunk, 0, 0)
        self.assertEqual((y, block), (4, "minecraft:grass_block"))


class HardeningTest(unittest.TestCase):
    def test_huge_array_count_is_rejected(self):
        """损坏的 .mca 里 count 可以很大，不能先按它分配内存。"""
        blob = bytes([0x0A, 0x00, 0x00,   # root compound, name ""
                      0x0B, 0x00, 0x00,   # int array, name ""
                      0x7F, 0xFF, 0xFF, 0xFF])
        with self.assertRaises(ValueError):
            mcmap.parse_nbt(blob)

    def test_absurd_heightmap_bits_returns_none(self):
        # 260 个 long -> bits = 65 -> 64 // bits == 0
        chunk = {"Heightmaps": {"MOTION_BLOCKING": [0] * 260}}
        self.assertIsNone(mcmap._height_at(chunk, 0, 0))

    def test_palette_with_short_data_returns_none(self):
        section = {"Y": 0, "block_states": {
            "palette": ["minecraft:air", "minecraft:stone"],
            "data": [0],          # 只够前 16 个位置
        }}
        self.assertIsNone(mcmap._palette_name(section, 15, 15, 15))

    def test_negative_surface_y_is_not_skipped(self):
        """1.18 之后主世界底部是 -64，y<0 是合法地表。"""
        idx = 15 * 256
        words = [0] * (idx // 16 + 1)
        words[idx // 16] = 1 << ((idx % 16) * 4)
        chunk = {
            "Heightmaps": {"MOTION_BLOCKING": packed_heights([0] * 256)},
            "sections": [{"Y": -1, "block_states": {
                "palette": ["minecraft:air", "minecraft:stone"],
                "data": words,
            }}],
        }
        self.assertEqual(mcmap.surface_at(chunk, 0, 0),
                         (-1, "minecraft:stone"))


class ColorTest(unittest.TestCase):
    def test_known_block_color(self):
        self.assertEqual(mcmap.color_for("minecraft:grass_block", 70),
                         mcmap.BLOCK_COLORS["minecraft:grass_block"])

    def test_unknown_block_falls_back_to_height(self):
        low = mcmap.color_for("minecraft:unknown_block", 1)
        high = mcmap.color_for("minecraft:unknown_block", 250)
        self.assertNotEqual(low, high)

    def test_snow_and_water_suffix_rules(self):
        self.assertEqual(mcmap.color_for("minecraft:water", 62),
                         mcmap.BLOCK_COLORS["minecraft:water"])


class PngTest(unittest.TestCase):
    def test_png_header_and_size(self):
        rgba = bytearray(4 * 4 * 4)
        png = mcmap._png_bytes(4, 4, rgba)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        width = int.from_bytes(png[16:20], "big")
        height = int.from_bytes(png[20:24], "big")
        self.assertEqual((width, height), (4, 4))

    def test_render_region_rejects_tiny_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            with open(path, "wb") as handle:
                handle.write(b"\x00" * 100)
            self.assertIsNone(mcmap.render_region(path))

    def test_render_region_on_empty_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "r.0.0.mca")
            with open(path, "wb") as handle:
                handle.write(b"\x00" * 8192)
            png = mcmap.render_region(path)
            self.assertTrue(png.startswith(b"\x89PNG"))


class WorldsTest(unittest.TestCase):
    def test_finds_classic_and_modern_layouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            for rel in ("world/region", "world/dimensions/minecraft/overworld/region"):
                region = os.path.join(tmp, rel)
                os.makedirs(region)
                with open(os.path.join(region, "r.0.0.mca"), "wb") as handle:
                    handle.write(b"\x00" * 8192)
            worlds = mcmap.list_worlds(tmp)
            ids = {world["id"] for world in worlds}
            self.assertIn("world", ids)
            self.assertIn("world/dimensions/minecraft/overworld", ids)
            for world in worlds:
                self.assertEqual(world["regions"], [[0, 0]])


class PathInsideTest(unittest.TestCase):
    """地图接口的世界参数必须锁在实例目录里，前缀相同的兄弟目录不算。"""

    def test_sibling_prefix_is_rejected(self):
        import web.server as ws
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "t")
            sibling = os.path.join(tmp, "t2")
            os.makedirs(os.path.join(root, "world"))
            os.makedirs(os.path.join(sibling, "world"))
            self.assertTrue(ws.path_inside(root, os.path.join(root, "world")))
            self.assertFalse(ws.path_inside(root, sibling))
            self.assertFalse(ws.path_inside(root, os.path.join(root, "..", "t2")))


if __name__ == "__main__":
    unittest.main()
