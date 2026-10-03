"""实例存储层：扫描、归一化、缓存正确性与隔离。"""

import json
import os
import shutil
import tempfile
import time
import unittest

import server_manager as SM


def make_instance(root, name, **overrides):
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    data = {
        "name": name,
        "kind": "minecraft",
        "mc_version": "1.20.1",
        "jar": "server.jar",
        "min_mem": "2G",
        "max_mem": "4G",
        "extra_jvm_args": ["-XX:+UseG1GC"],
        "extra_server_args": ["nogui"],
        "openfrp": {"token": "tok", "proxy_id": 7, "auto_start": False},
        "launch": {"mode": "exec", "program": "", "args": ["a"],
                   "command": "", "workdir": ""},
    }
    data.update(overrides)
    with open(os.path.join(path, ".server.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


class ListServersTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-sm-")
        self.servers = os.path.join(self.dir, "servers")
        os.makedirs(self.servers)
        SM.invalidate_server_cache()

    def tearDown(self):
        SM.invalidate_server_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_empty_directory(self):
        self.assertEqual(SM.list_servers(self.servers), [])

    def test_missing_directory(self):
        self.assertEqual(SM.list_servers(os.path.join(self.dir, "nope")), [])

    def test_directories_without_config_are_skipped(self):
        os.makedirs(os.path.join(self.servers, "junk"))
        make_instance(self.servers, "real")
        self.assertEqual([s["name"] for s in SM.list_servers(self.servers)], ["real"])

    def test_sorted_by_directory_name(self):
        for n in ("c", "a", "b"):
            make_instance(self.servers, n)
        self.assertEqual([s["name"] for s in SM.list_servers(self.servers)],
                         ["a", "b", "c"])

    def test_corrupt_config_is_skipped_not_fatal(self):
        make_instance(self.servers, "good")
        bad = os.path.join(self.servers, "bad")
        os.makedirs(bad)
        with open(os.path.join(bad, ".server.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        self.assertEqual([s["name"] for s in SM.list_servers(self.servers)], ["good"])

    def test_non_dict_config_is_skipped(self):
        bad = os.path.join(self.servers, "bad")
        os.makedirs(bad)
        with open(os.path.join(bad, ".server.json"), "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(SM.list_servers(self.servers), [])

    # -- 归一化 ------------------------------------------------------------

    def test_list_fields_are_coerced(self):
        make_instance(self.servers, "a", extra_jvm_args=None, extra_server_args="-Xmx1G")
        entry = SM.list_servers(self.servers)[0]
        self.assertEqual(entry["extra_jvm_args"], [])
        self.assertEqual(entry["extra_server_args"], ["-Xmx1G"])

    def test_string_args_are_split(self):
        make_instance(self.servers, "a", launch={"mode": "cmd", "program": "", "args": "x y",
                                                 "command": "", "workdir": ""})
        self.assertEqual(SM.list_servers(self.servers)[0]["launch"]["args"], ["x", "y"])

    def test_unknown_kind_falls_back_to_minecraft(self):
        make_instance(self.servers, "a", kind="banana")
        self.assertEqual(SM.list_servers(self.servers)[0]["kind"], "minecraft")

    def test_unknown_launch_mode_falls_back_to_exec(self):
        make_instance(self.servers, "a", kind="generic",
                      launch={"mode": "banana", "program": "p", "args": [],
                              "command": "", "workdir": ""})
        self.assertEqual(SM.list_servers(self.servers)[0]["launch"]["mode"], "exec")

    def test_proxy_id_is_coerced_to_int(self):
        make_instance(self.servers, "a",
                      openfrp={"token": "t", "proxy_id": "42", "auto_start": True})
        self.assertEqual(SM.list_servers(self.servers)[0]["openfrp"]["proxy_id"], 42)

    def test_bad_proxy_id_becomes_zero(self):
        make_instance(self.servers, "a",
                      openfrp={"token": "t", "proxy_id": "abc", "auto_start": False})
        self.assertEqual(SM.list_servers(self.servers)[0]["openfrp"]["proxy_id"], 0)

    def test_missing_openfrp_block_gets_a_fresh_default(self):
        """openfrp 缺失/被手改成非 dict 时要补默认值，且不能等于默认值那个对象。

        这一支曾经在重构里漏掉（copy 的 import 被删了，这里还在用），
        只有「.server.json 没有 openfrp 段」才会走到——所以必须有用例盯着。
        """
        make_instance(self.servers, "a", openfrp=None)
        entry = SM.list_servers(self.servers)[0]
        self.assertEqual(entry["openfrp"]["token"], "")
        self.assertEqual(entry["openfrp"]["proxy_id"], 0)
        self.assertIsNot(entry["openfrp"], SM.DEFAULT_SERVER_CONFIG["openfrp"])

        # 就地改它不能影响默认值，也不能影响下一次读取
        entry["openfrp"]["token"] = "HACKED"
        self.assertEqual(SM.DEFAULT_SERVER_CONFIG["openfrp"]["token"], "")
        self.assertEqual(SM.list_servers(self.servers)[0]["openfrp"]["token"], "")

    def test_openfrp_as_a_string_is_repaired(self):
        make_instance(self.servers, "a", openfrp="nonsense")
        self.assertEqual(SM.list_servers(self.servers)[0]["openfrp"]["proxy_id"], 0)

    def test_launch_as_a_string_is_repaired(self):
        make_instance(self.servers, "a", launch="nonsense")
        entry = SM.list_servers(self.servers)[0]
        self.assertEqual(entry["launch"]["mode"], "exec")
        self.assertEqual(entry["launch"]["args"], [])

    # -- 缓存 --------------------------------------------------------------

    def test_repeated_calls_are_identical(self):
        make_instance(self.servers, "a")
        self.assertEqual(SM.list_servers(self.servers), SM.list_servers(self.servers))

    def test_callers_cannot_pollute_the_cache(self):
        """就地改返回的条目不能影响下一次读取（历史上真出过这类串味 bug）。"""
        make_instance(self.servers, "a")
        first = SM.list_servers(self.servers)
        first[0]["openfrp"]["token"] = "HACKED"
        first[0]["openfrp"]["proxy_id"] = 999
        first[0]["extra_jvm_args"].append("-XX:+HACK")
        first[0]["launch"]["args"].append("HACK")
        first[0]["min_mem"] = "64G"

        again = SM.list_servers(self.servers)[0]
        self.assertEqual(again["openfrp"]["token"], "tok")
        self.assertEqual(again["openfrp"]["proxy_id"], 7)
        self.assertEqual(again["extra_jvm_args"], ["-XX:+UseG1GC"])
        self.assertEqual(again["launch"]["args"], ["a"])
        self.assertEqual(again["min_mem"], "2G")
        self.assertEqual(SM.DEFAULT_SERVER_CONFIG["openfrp"]["proxy_id"], 0)

    def test_save_is_visible_immediately(self):
        """写完必须立刻能读到新值——光靠 mtime 在粗粒度文件系统上不够。"""
        path = make_instance(self.servers, "a")
        SM.list_servers(self.servers)          # 先把缓存填上
        cfg = SM.load_server_config(path)
        cfg["min_mem"] = "8G"
        SM.save_server_config(cfg, path)
        self.assertEqual(SM.list_servers(self.servers)[0]["min_mem"], "8G")

    def test_external_edit_is_picked_up(self):
        """用户直接在编辑器里改 .server.json 也要能看见。

        这里要等一下再写：Windows 的文件时间戳取自系统时钟，粒度约 15ms，
        紧挨着写可能拿到和上次读一样的 mtime（大小还常常一样，比如 4G->6G），
        那就变成「没变」。真实的手改当然不止隔 15ms。
        """
        path = make_instance(self.servers, "a")
        SM.list_servers(self.servers)
        time.sleep(0.05)
        cfg = SM.load_server_config(path)
        cfg["max_mem"] = "6G"
        with open(os.path.join(path, ".server.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        self.assertEqual(SM.list_servers(self.servers)[0]["max_mem"], "6G")

    def test_cache_does_not_outlive_its_ttl(self):
        """时间戳分辨不出来时，缓存也不能一直生效（最多活 TTL 那么久）。"""
        path = make_instance(self.servers, "a")
        SM.list_servers(self.servers)
        # 直接改磁盘内容但把 mtime/size 都恢复原样，模拟「时间戳没变」的情况
        cfg = SM.load_server_config(path)
        cfg["max_mem"] = "6G"
        with open(os.path.join(path, ".server.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        before = os.stat(os.path.join(path, ".server.json"))

        original = SM._CACHE_TTL
        SM._CACHE_TTL = 0.0            # 让缓存立刻过期
        try:
            self.assertEqual(SM.list_servers(self.servers)[0]["max_mem"], "6G")
        finally:
            SM._CACHE_TTL = original
        self.assertEqual(before.st_size,
                         os.stat(os.path.join(path, ".server.json")).st_size)

    def test_deleted_instance_disappears_and_cache_is_pruned(self):
        make_instance(self.servers, "a")
        make_instance(self.servers, "b")
        SM.list_servers(self.servers)
        shutil.rmtree(os.path.join(self.servers, "b"))
        self.assertEqual([s["name"] for s in SM.list_servers(self.servers)], ["a"])
        self.assertEqual(len(SM._ENTRY_CACHE), 1)


class DefaultsIsolationTest(unittest.TestCase):
    """默认配置里的可变容器必须是新的，否则一个实例的改动会串到别的实例。"""

    def test_each_default_is_independent(self):
        a = SM._default_server_config()
        b = SM._default_server_config()
        a["openfrp"]["token"] = "leaked"
        a["extra_jvm_args"].append("x")
        a["launch"]["args"].append("y")
        self.assertEqual(b["openfrp"]["token"], "")
        self.assertEqual(b["extra_jvm_args"], [])
        self.assertEqual(b["launch"]["args"], [])

    def test_module_constants_are_not_aliased(self):
        cfg = SM._default_server_config()
        self.assertIsNot(cfg["launch"], SM.DEFAULT_GENERIC_LAUNCH)
        self.assertIsNot(cfg["launch"]["args"], SM.DEFAULT_GENERIC_LAUNCH["args"])
        self.assertIsNot(cfg["openfrp"], SM.DEFAULT_SERVER_CONFIG["openfrp"])


if __name__ == "__main__":
    unittest.main()
