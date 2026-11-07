"""备份、定时调度、玩家名单三块运维功能。

这些模块都不碰进程，纯文件操作，所以不受平台限制——Windows / Linux / macOS
上都能跑。真正用 ConPTY 或子进程的测试在别处。
"""

import json
import os
import sys
import tempfile
import time
import unittest
import zipfile

from core import backup
from core import players
from core.errors import CoreError
from core.schedule import DailyScheduler, format_hhmm, parse_hhmm


def make_instance(tmp: str) -> str:
    """造一个像样的实例目录。"""
    files = {
        "server.properties": "motd=hi\n",
        "eula.txt": "eula=true\n",
        "world/level.dat": "L" * 200,
        "world/region/r.0.0.mca": "R" * 5000,
        "plugins/foo.jar": "J" * 2000,
        "logs/latest.log": "log line\n" * 500,
        "cache/tmp.bin": "c" * 800,
        "crash-reports/crash.txt": "boom\n",
    }
    for rel, text in files.items():
        path = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return tmp


class BackupCreateTest(unittest.TestCase):
    def test_creates_zip_with_expected_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            entry = backup.create(tmp)
            self.assertTrue(os.path.isfile(entry.path))
            self.assertGreater(entry.size, 0)
            with zipfile.ZipFile(entry.path) as zf:
                names = set(zf.namelist())
            self.assertIn("server.properties", names)
            self.assertIn("world/region/r.0.0.mca", names)
            # 备份自己不能套娃
            self.assertFalse(any(n.startswith("backups/") for n in names))

    def test_excludes_logs_cache_and_crash_reports(self):
        """日志和缓存没有恢复价值，打进去只会让备份大好几倍。"""
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            entry = backup.create(tmp)
            with zipfile.ZipFile(entry.path) as zf:
                names = zf.namelist()
            for prefix in ("logs/", "cache/", "crash-reports/"):
                self.assertFalse(any(n.startswith(prefix) for n in names), prefix)

    def test_backup_is_inside_instance_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            entry = backup.create(tmp)
            self.assertEqual(os.path.dirname(entry.path), backup.backup_dir(tmp))

    def test_nothing_to_back_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(CoreError) as ctx:
                backup.create(tmp)
            self.assertEqual(ctx.exception.key, "web.error.backup_nothing")

    def test_missing_dir(self):
        with self.assertRaises(CoreError):
            backup.create(os.path.join(tempfile.gettempdir(), "no-such-dir-xyz"))

    def test_no_partial_file_left_behind(self):
        """打包中途失败不能留下半个 zip。"""
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            backup.create(tmp)
            leftovers = [n for n in os.listdir(backup.backup_dir(tmp))
                         if n.endswith(".part")]
            self.assertEqual(leftovers, [])


class BackupRetentionTest(unittest.TestCase):
    def test_prune_keeps_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            made = []
            for i in range(5):
                # 用固定时间戳拉开间隔，避免同一秒内被 -1 -2 后缀干扰排序
                made.append(backup.create(tmp, when=time.time() - (10 - i) * 60))
                time.sleep(0.01)
            self.assertEqual(len(backup.list_backups(tmp)), 5)
            removed = backup.prune(tmp, 2)
            self.assertEqual(len(removed), 3)
            left = backup.list_backups(tmp)
            self.assertEqual(len(left), 2)
            # 留下的是最新的那两份
            newest = os.path.basename(made[-1].path)
            self.assertIn(newest, [e.name for e in left])

    def test_prune_zero_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            backup.create(tmp)
            self.assertEqual(backup.prune(tmp, 0), [])
            self.assertEqual(len(backup.list_backups(tmp)), 1)

    def test_create_with_keep_prunes_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            for i in range(4):
                backup.create(tmp, keep=2, when=time.time() - (10 - i) * 60)
            self.assertEqual(len(backup.list_backups(tmp)), 2)

    def test_two_backups_in_same_second_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            stamp = time.time()
            a = backup.create(tmp, when=stamp)
            b = backup.create(tmp, when=stamp)
            self.assertNotEqual(a.path, b.path)
            self.assertEqual(len(backup.list_backups(tmp)), 2)


class BackupDeleteTest(unittest.TestCase):
    def test_delete_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            entry = backup.create(tmp)
            self.assertTrue(backup.delete(tmp, entry.name))
            self.assertEqual(backup.list_backups(tmp), [])

    def test_delete_missing_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            self.assertFalse(backup.delete(tmp, "nope.zip"))

    def test_delete_rejects_traversal(self):
        """备份名来自 HTTP 请求体，不能拼出 backups/ 之外的路径。"""
        with tempfile.TemporaryDirectory() as tmp:
            make_instance(tmp)
            for bad in ("../server.properties", "..\\server.properties",
                        "a/b.zip", "", "nope.txt"):
                self.assertFalse(backup.delete(tmp, bad), bad)
            # 原文件必须还在
            self.assertTrue(os.path.isfile(os.path.join(tmp, "server.properties")))


class SchedulerParseTest(unittest.TestCase):
    def test_valid_times(self):
        self.assertEqual(parse_hhmm("04:30"), (4, 30))
        self.assertEqual(parse_hhmm("00:00"), (0, 0))
        self.assertEqual(parse_hhmm("23:59"), (23, 59))
        self.assertEqual(parse_hhmm(" 7:5 "), (7, 5))

    def test_invalid_times(self):
        for bad in ("", "abc", "24:00", "12:60", "-1:00", "12", ":"):
            self.assertIsNone(parse_hhmm(bad), bad)

    def test_format(self):
        self.assertEqual(format_hhmm(4, 30), "04:30")
        self.assertEqual(format_hhmm(0, 0), "00:00")


class SchedulerRunTest(unittest.TestCase):
    def test_fires_once_per_day(self):
        import datetime

        hits = []
        job = DailyScheduler(lambda: hits.append(1), "04:30", "test")
        now = datetime.datetime.now()
        job._hour, job._minute = now.hour, now.minute
        job._compute_next()
        job._tick()
        self.assertEqual(len(hits), 1)
        job._tick()          # 同一天不该再触发
        self.assertEqual(len(hits), 1)

    def test_set_when_rejects_bad_value(self):
        job = DailyScheduler(lambda: None, "04:30")
        self.assertTrue(job.set_when("06:15"))
        self.assertEqual(job.when, "06:15")
        self.assertFalse(job.set_when("99:99"))
        self.assertEqual(job.when, "06:15")

    def test_next_run_text_is_human_readable(self):
        job = DailyScheduler(lambda: None, "04:30")
        text = job.next_run_text()
        self.assertTrue(text)
        self.assertTrue("后" in text)

    def test_start_stop_is_safe(self):
        job = DailyScheduler(lambda: None, "04:30")
        job.start()
        self.assertTrue(job.running)
        job.start()          # 重复启动不该叠线程
        job.stop()
        self.assertFalse(job.running)
        job.stop()           # 重复停止无害


class PlayersReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    def test_read_all_three_lists(self):
        self._write("whitelist.json", [{"uuid": "a", "name": "Alice"},
                                       {"uuid": "b", "name": "Bob"}])
        self._write("ops.json", [{"uuid": "a", "name": "Alice", "level": 4,
                                  "bypassesPlayerLimit": True}])
        self._write("banned-players.json", [{"uuid": "c", "name": "Griefer",
                                             "reason": "炸服",
                                             "expires": "forever",
                                             "source": "Server"}])
        data = players.summary(self.dir)
        self.assertEqual([p["name"] for p in data["whitelist"]], ["Alice", "Bob"])
        self.assertEqual(data["ops"][0]["level"], 4)
        self.assertTrue(data["ops"][0]["bypasses"])
        self.assertEqual(data["banned"][0]["reason"], "炸服")

    def test_missing_files_mean_empty(self):
        data = players.summary(self.dir)
        self.assertEqual(data, {"whitelist": [], "ops": [], "banned": []})

    def test_broken_json_means_empty(self):
        with open(os.path.join(self.dir, "whitelist.json"), "w") as f:
            f.write("{not json")
        self.assertEqual(players.read_whitelist(self.dir), [])

    def test_expired_ban_is_marked(self):
        self._write("banned-players.json", [
            {"name": "Old", "expires": "2000-01-01 00:00:00 +0000"},
            {"name": "New", "expires": "2099-01-01 00:00:00 +0000"},
        ])
        entries = players.read_banned(self.dir)
        by_name = {e.name: e.expires for e in entries}
        self.assertEqual(by_name["Old"], "expired")
        self.assertNotEqual(by_name["New"], "expired")


class PlayersWriteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        with open(os.path.join(self.dir, "whitelist.json"), "w",
                  encoding="utf-8") as f:
            json.dump([{"uuid": "a", "name": "Alice"},
                       {"uuid": "b", "name": "Bob"}], f)

    def tearDown(self):
        self.tmp.cleanup()

    def test_remove_is_case_insensitive(self):
        self.assertTrue(players.remove_whitelist(self.dir, "alice"))
        left = [p.name for p in players.read_whitelist(self.dir)]
        self.assertEqual(left, ["Bob"])

    def test_remove_missing_returns_false(self):
        self.assertFalse(players.remove_whitelist(self.dir, "Nobody"))

    def test_commands_for_each_list(self):
        self.assertEqual(players.player_commands("whitelist", "Alice", "add"),
                         ["whitelist add Alice"])
        self.assertEqual(players.player_commands("whitelist", "Alice", "remove"),
                         ["whitelist remove Alice"])
        self.assertEqual(players.player_commands("op", "Alice", "add"),
                         ["op Alice"])
        self.assertEqual(players.player_commands("op", "Alice", "remove"),
                         ["deop Alice"])
        self.assertEqual(players.player_commands("ban", "Bob", "remove"),
                         ["pardon Bob"])

    def test_ban_reason_is_single_line(self):
        """封禁原因会被拼进控制台命令，换行等于注入第二条命令。"""
        commands = players.player_commands("ban", "Bob", "add",
                                           reason="坏\nsay pwned")
        self.assertEqual(len(commands), 1)
        self.assertNotIn("\n", commands[0])
        self.assertIn("坏 say pwned", commands[0])

    def test_bad_player_names_are_rejected(self):
        """名字最终会进控制台命令，必须挡住分隔符与换行。"""
        for bad in ("", "a b", "a;stop", "a\nb", "x" * 17, "玩家"):
            with self.assertRaises(CoreError, msg=bad):
                players.player_commands("whitelist", bad, "add")

    def test_unknown_list_is_rejected(self):
        with self.assertRaises(CoreError) as ctx:
            players.player_commands("nope", "Alice", "add")
        self.assertEqual(ctx.exception.key, "web.error.bad_player_list")


if __name__ == "__main__":
    unittest.main()
