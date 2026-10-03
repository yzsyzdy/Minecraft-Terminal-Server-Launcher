"""config.json 的读写：未知键不能丢、内容没变不重写、写盘要原子。"""

import json
import os
import shutil
import tempfile
import unittest

import config
from config import DEFAULT_CONFIG, load_config, save_config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-cfg-")
        self.path = os.path.join(self.dir, "config.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def _read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    # -- 默认值与隔离 ------------------------------------------------------

    def test_missing_file_gives_defaults(self):
        cfg = load_config(self.dir)
        self.assertEqual(cfg["language"], "")
        self.assertEqual(cfg["web"]["port"], 8765)

    def test_defaults_are_not_shared_state(self):
        """默认值里的可变容器不能被调用方改到（浅拷贝的老坑）。"""
        cfg = load_config(self.dir)
        cfg["web"]["port"] = 9999
        self.assertEqual(DEFAULT_CONFIG["web"]["port"], 8765)
        self.assertEqual(load_config(self.dir)["web"]["port"], 8765)

    def test_web_section_is_repaired(self):
        """web 段被手改成非 dict 时要补回默认结构，而不是崩掉。"""
        self._write({"web": "not-a-dict"})
        cfg = load_config(self.dir)
        self.assertEqual(cfg["web"]["host"], "127.0.0.1")
        self.assertEqual(cfg["web"]["port"], 8765)

    def test_corrupt_file_falls_back_to_defaults(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        self.assertEqual(load_config(self.dir)["web"]["port"], 8765)

    # -- 未知键 ------------------------------------------------------------

    def test_unknown_top_level_keys_survive_a_save(self):
        """本版不认识的顶层键不能在保存时被删掉。

        start.py 每次启动都会 save_config()，所以只认识已知键的话，用户自己
        加的键（或更新版本写进去的设置）会在下次启动时静默消失。
        """
        self._write({"language": "zh", "my_key": "keep", "nested": {"a": 1}})
        save_config(load_config(self.dir), self.dir)
        after = self._read()
        self.assertEqual(after["my_key"], "keep")
        self.assertEqual(after["nested"], {"a": 1})

    def test_unknown_web_keys_survive_a_save(self):
        self._write({"web": {"token": "t", "my_web_key": "keep"}})
        save_config(load_config(self.dir), self.dir)
        self.assertEqual(self._read()["web"]["my_web_key"], "keep")

    def test_known_keys_are_still_filled_in(self):
        self._write({"language": "zh"})
        save_config(load_config(self.dir), self.dir)
        after = self._read()
        self.assertEqual(after["download_threads"], 16)
        self.assertIn("web", after)

    # -- 写入策略 ----------------------------------------------------------

    def test_unchanged_config_is_not_rewritten(self):
        """内容没变就不该动文件，否则每次启动都白写一遍、mtime 一直在变。"""
        self._write({"language": "zh"})
        save_config(load_config(self.dir), self.dir)
        first = os.stat(self.path).st_mtime_ns

        save_config(load_config(self.dir), self.dir)
        self.assertEqual(os.stat(self.path).st_mtime_ns, first)

    def test_changed_config_is_written(self):
        self._write({"language": "zh"})
        cfg = load_config(self.dir)
        cfg["language"] = "en"
        save_config(cfg, self.dir)
        self.assertEqual(self._read()["language"], "en")

    def test_first_run_creates_the_file(self):
        save_config(load_config(self.dir), self.dir)
        self.assertTrue(os.path.isfile(self.path))

    def test_no_temp_file_left_behind(self):
        self._write({"language": "zh"})
        cfg = load_config(self.dir)
        cfg["language"] = "en"
        save_config(cfg, self.dir)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_save_creates_missing_directory(self):
        target = os.path.join(self.dir, "deep", "nested")
        save_config({"language": "zh"}, target)
        self.assertTrue(os.path.isfile(os.path.join(target, "config.json")))


if __name__ == "__main__":
    unittest.main()
