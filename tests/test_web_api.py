"""网页面板的 HTTP 契约。

两块最容易回归的东西：
  1. 鉴权：三条出示通道（请求头 / cookie / ?token=）并列，任意一条对就放行。
     以前是「谁先命中就只看谁」，浏览器里留个旧 token 的 cookie 就能把地址栏
     里正确的新 token 顶掉，用户拿对 token 也永远进不来。
  2. /api/state 回显的值必须等于磁盘上的值。它一旦取自「启动那一刻」的计划
     快照，设置对话框就会回显旧值，用户随手一保存就把自己刚改的改了回去。
"""

import http.client
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest

from core.runtime import LaunchPlan, RuntimeManager, ServerRuntime
from web import server as web


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_instance(root, name, **overrides):
    path = os.path.join(root, "servers", name)
    os.makedirs(path, exist_ok=True)
    data = {
        "name": name,
        "kind": "minecraft",
        "mc_version": "1.20.1",
        "jar": "server.jar",
        "java_path": None,
        "min_mem": "2G",
        "max_mem": "4G",
        "extra_jvm_args": ["-XX:+UseG1GC"],
        "extra_server_args": ["nogui"],
        "process_priority": "normal",
        "auto_start": False,
        "openfrp": {"token": "", "proxy_id": 0, "auto_start": False},
        "launch": {"mode": "exec", "program": "", "args": [],
                   "command": "", "workdir": ""},
    }
    data.update(overrides)
    with open(os.path.join(path, ".server.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


class PanelCase(unittest.TestCase):
    """起一个真面板（临时目录、随机端口），跑完关掉。"""

    token = "TESTTOKEN"
    instances = ()

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-web-")
        os.makedirs(os.path.join(self.dir, "servers"), exist_ok=True)
        for name in self.instances:
            write_instance(self.dir, name)
        with open(os.path.join(self.dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"language": "zh",
                       "web": {"host": "127.0.0.1", "port": 0,
                               "token": self.token}}, f)

        self.manager = RuntimeManager(self.dir)
        self.servers_dir = os.path.join(self.dir, "servers")
        # 默认返回启动时的 token；用例里可以换成别的值或一个会抛异常的函数
        self.token_provider = lambda: self.token
        self.port = free_port()
        self.httpd = web.build_server(
            self.dir, host="127.0.0.1", port=self.port, token=self.token,
            manager=self.manager, servers_dir=self.servers_dir,
            token_provider=lambda: self.token_provider())
        # poll_interval 默认 0.5s，会让每个用例的 shutdown() 白等半秒
        threading.Thread(
            target=lambda: self.httpd.serve_forever(poll_interval=0.05),
            daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- 请求工具 ----------------------------------------------------------

    def request(self, path, method="GET", body=None, cookie=None, header=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if header:
            headers["X-MSTL-Token"] = header
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, payload, headers)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", "replace")
        result = (resp.status, resp.getheader("Set-Cookie") or "", raw)
        conn.close()
        return result

    def api(self, path, body=None):
        sep = "&" if "?" in path else "?"
        status, _, raw = self.request(
            f"{path}{sep}token={self.token}",
            method="POST" if body is not None else "GET", body=body)
        return status, json.loads(raw)

    def instance_row(self, name):
        _, data = self.api("/api/state")
        for row in data["instances"]:
            if row["name"] == name:
                return row
        self.fail(f"实例 {name} 不在 /api/state 里")

    def status_of(self, path, **kwargs):
        return self.request(path, **kwargs)[0]


class AuthTest(PanelCase):
    instances = ("a",)

    def test_no_credentials_is_rejected(self):
        self.assertEqual(self.status_of("/"), 401)
        self.assertEqual(self.status_of("/api/state"), 401)

    def test_query_token_is_accepted(self):
        self.assertEqual(self.status_of(f"/?token={self.token}"), 200)

    def test_cookie_token_is_accepted(self):
        self.assertEqual(self.status_of("/", cookie=f"mstl_token={self.token}"), 200)

    def test_header_token_is_accepted(self):
        self.assertEqual(self.status_of("/", header=self.token), 200)

    def test_correct_query_token_beats_a_stale_cookie(self):
        """用户报的那个 bug：旧 cookie 不能把正确的新 token 顶掉。"""
        status, cookie, _ = self.request(f"/?token={self.token}",
                                         cookie="mstl_token=STALE")
        self.assertEqual(status, 200)
        self.assertIn(f"mstl_token={self.token}", cookie)

    def test_stale_cookie_alone_is_still_rejected(self):
        self.assertEqual(self.status_of("/", cookie="mstl_token=STALE"), 401)

    def test_wrong_token_is_rejected(self):
        self.assertEqual(self.status_of("/?token=WRONG"), 401)
        self.assertEqual(self.status_of("/", header="WRONG"), 401)

    def test_prefix_is_not_enough(self):
        self.assertEqual(self.status_of("/", header=self.token[:-1]), 401)
        self.assertEqual(self.status_of("/", header=self.token + "x"), 401)

    def test_empty_query_token_is_rejected(self):
        self.assertEqual(self.status_of("/?token="), 401)

    def test_similar_cookie_name_does_not_authenticate(self):
        self.assertEqual(self.status_of("/", cookie=f"other={self.token}"), 401)

    def test_401_page_explains_the_likely_causes(self):
        status, _, body = self.request("/")
        self.assertEqual(status, 401)
        self.assertIn("cookie", body)
        self.assertIn("127.0.0.1", body)
        self.assertNotIn("web.auth.", body)

    def test_regenerated_token_takes_effect_without_a_restart(self):
        """菜单里换了 token，正在跑的面板要立刻认新的。"""
        self.assertEqual(self.status_of(f"/?token={self.token}"), 200)
        self.token_provider = lambda: "BRANDNEW"
        self.assertEqual(self.status_of("/?token=BRANDNEW"), 200)
        self.assertEqual(self.status_of(f"/?token={self.token}"), 401)

    def test_unreadable_config_falls_back_and_never_opens_up(self):
        """读不到配置时退回启动 token，绝不能把鉴权放开。"""
        self.token_provider = lambda: ""
        self.assertEqual(self.status_of(f"/?token={self.token}"), 200)
        self.assertEqual(self.status_of("/"), 401)

    def test_provider_exception_falls_back_and_never_opens_up(self):
        def boom():
            raise RuntimeError("config 读炸了")
        self.token_provider = boom
        self.assertEqual(self.status_of(f"/?token={self.token}"), 200)
        self.assertEqual(self.status_of("/"), 401)


class NoTokenTest(PanelCase):
    token = ""
    instances = ("a",)

    def test_everything_is_open_when_no_token_is_configured(self):
        self.assertEqual(self.status_of("/"), 200)
        self.assertEqual(self.status_of("/api/state"), 200)


class StateContractTest(PanelCase):
    instances = ("a", "b")

    def test_lists_every_instance(self):
        _, data = self.api("/api/state")
        self.assertEqual(sorted(x["name"] for x in data["instances"]), ["a", "b"])

    def test_every_field_the_settings_dialog_needs_is_present(self):
        row = self.instance_row("a")
        for key in ("name", "kind", "mc_version", "jar", "java_path", "min_mem",
                    "max_mem", "extra_jvm_args", "extra_server_args",
                    "process_priority", "auto_start", "launch", "openfrp",
                    "state", "workdir"):
            self.assertIn(key, row, key)

    def test_strings_are_delivered_without_raw_keys(self):
        _, data = self.api("/api/state")
        strings = data["strings"]
        self.assertTrue(strings.get("web.auth.hint_cookie"))
        stray = sorted(v for v in strings.values() if v.startswith("web."))
        self.assertEqual(stray, [])

    def test_memory_is_read_from_disk_not_from_the_runtime_snapshot(self):
        """实例被启动过之后，/api/state 不能再用启动计划里的旧内存值。

        计划快照里的 min_mem/max_mem 是启动那一刻抄下来的，通用实例更是
        1G/4G 默认值；用它回显的话，用户在设置里改完内存，对话框显示旧值，
        随手一保存就把自己刚改的写回去了。
        """
        self.assertEqual(self.instance_row("b")["min_mem"], "2G")

        plan = LaunchPlan(name="b", workdir=os.path.join(self.dir, "servers", "b"),
                          cmd=["cmd", "/c", "exit"], kind="generic",
                          min_mem="1G", max_mem="4G",
                          log_path=os.path.join(self.dir, "l.txt"))
        self.manager._runtimes["b"] = ServerRuntime(plan)

        row = self.instance_row("b")
        self.assertEqual(row["min_mem"], "2G")
        self.assertEqual(row["max_mem"], "4G")

    def test_unknown_path_is_404(self):
        status, _, _ = self.request(f"/api/nope?token={self.token}")
        self.assertEqual(status, 404)


class SettingsWriteTest(PanelCase):
    instances = ("a", "g")

    def _disk(self, name):
        return read_json(os.path.join(self.dir, "servers", name, ".server.json"))

    def test_scalar_fields_are_persisted(self):
        status, data = self.api("/api/instances/settings", {
            "name": "a", "min_mem": "8G", "max_mem": "12G",
            "process_priority": "low", "auto_start": True,
            "java_path": "C:/jdk/bin/java.exe",
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

        disk = self._disk("a")
        self.assertEqual(disk["min_mem"], "8G")
        self.assertEqual(disk["max_mem"], "12G")
        self.assertEqual(disk["process_priority"], "low")
        self.assertTrue(disk["auto_start"])
        self.assertEqual(disk["java_path"], "C:/jdk/bin/java.exe")

    def test_java_path_is_not_silently_dropped(self):
        """接口白名单漏掉它的话，收下了却永远写不进 .server.json。"""
        self.api("/api/instances/settings",
                 {"name": "a", "java_path": "C:/other/java.exe"})
        self.assertEqual(self._disk("a")["java_path"], "C:/other/java.exe")

    def test_state_reflects_the_write_immediately(self):
        self.api("/api/instances/settings",
                 {"name": "a", "min_mem": "16G", "max_mem": "20G"})
        row = self.instance_row("a")
        self.assertEqual(row["min_mem"], "16G")
        self.assertEqual(row["max_mem"], "20G")

    def test_openfrp_block_is_persisted(self):
        self.api("/api/instances/settings", {
            "name": "a",
            "openfrp": {"token": "tk", "proxy_id": 123, "auto_start": True},
        })
        disk = self._disk("a")
        self.assertEqual(disk["openfrp"]["token"], "tk")
        self.assertEqual(disk["openfrp"]["proxy_id"], 123)
        self.assertTrue(disk["openfrp"]["auto_start"])

    def test_unknown_instance_is_rejected(self):
        status, data = self.api("/api/instances/settings",
                                {"name": "nope", "min_mem": "1G"})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_generic_launch_cannot_be_emptied(self):
        write_instance(self.dir, "g", kind="generic")
        status, data = self.api("/api/instances/settings", {
            "name": "g",
            "launch": {"mode": "cmd", "program": "", "args": [],
                       "command": "", "workdir": ""},
        })
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_kind_and_name_are_not_patchable(self):
        """改名/换种类不能让「保存设置」悄悄做掉。"""
        self.api("/api/instances/settings",
                 {"name": "a", "kind": "generic", "new_name": "hacked"})
        disk = self._disk("a")
        self.assertEqual(disk["kind"], "minecraft")
        self.assertEqual(disk["name"], "a")


if __name__ == "__main__":
    unittest.main()
