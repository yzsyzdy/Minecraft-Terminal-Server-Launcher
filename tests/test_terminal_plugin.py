"""终端插件：ConPTY 封装与插件后端。

这些都是「有平台依赖」的用例，只在 Windows 上跑；非 Windows 上一律 skip，
否则 CI 会在 Linux/macOS 上挂（这个坑在 v1.1.42 已经踩过一次）。
"""

import json
import os
import sys
import tempfile
import time
import unittest

_HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "plugins", "terminal")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

WIN_ONLY = unittest.skipUnless(os.name == "nt", "Windows only")


class PluginManifestTest(unittest.TestCase):
    def test_manifest_is_valid(self):
        path = os.path.join(_HERE, "plugin.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["id"], "terminal")
        self.assertEqual(data["entry"], "plugin.py")
        # 核心零依赖：插件不许声明 requires，否则装了 exe 也跑不起来
        self.assertEqual(data.get("requires") or [], [])
        self.assertIn("xterm.js", data["web"])

    def test_declared_assets_exist(self):
        path = os.path.join(_HERE, "plugin.json")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for name in data["web"]:
            self.assertTrue(os.path.isfile(os.path.join(_HERE, "web", name)),
                            name)


@WIN_ONLY
class ConPtyTest(unittest.TestCase):
    def test_available_on_windows(self):
        import conpty
        self.assertTrue(conpty.conpty_available())

    def test_shell_choices_has_cmd(self):
        import conpty
        ids = [s["id"] for s in conpty.shell_choices()]
        self.assertIn("cmd", ids)

    def test_round_trip(self):
        """起一个 cmd，写一条命令，能把回显读回来。

        这是整个终端功能的最小闭环：读不到回显就说明 ConPTY 没接通。
        """
        import conpty
        session = conpty.ConPtySession("cmd", cols=90, rows=24, cwd=".")
        session.start()
        try:
            self.assertGreater(session.pid, 0)
            self.assertTrue(session.alive)
            time.sleep(0.6)
            session.read()                      # 吃掉首屏
            self.assertTrue(session.write(b"echo CONPTY-ROUNDTRIP\r\n"))
            got = b""
            for _ in range(40):
                time.sleep(0.1)
                got += session.read()
                if b"CONPTY-ROUNDTRIP" in got:
                    break
            self.assertIn(b"CONPTY-ROUNDTRIP", got)
        finally:
            session.close()
        self.assertFalse(session.alive)

    def test_resize(self):
        import conpty
        session = conpty.ConPtySession("cmd", cols=80, rows=20, cwd=".")
        session.start()
        try:
            self.assertTrue(session.resize(120, 40))
            self.assertEqual((session.cols, session.rows), (120, 40))
        finally:
            session.close()

    def test_write_after_close_fails_cleanly(self):
        import conpty
        session = conpty.ConPtySession("cmd", cols=80, rows=20, cwd=".")
        session.start()
        session.close()
        self.assertFalse(session.write(b"x"))
        self.assertEqual(session.read(), b"")


@WIN_ONLY
class BackendTest(unittest.TestCase):
    """走插件后端那层 HTTP 处理函数（不经过真正的 HTTP 服务器）。"""

    def setUp(self):
        import plugin
        self.plugin = plugin
        # 会话池是模块级单例，前一个用例残留的会话会串进来（读到别人的输出、
        # 或者 since 对不上），每个用例开始前清干净
        plugin._POOL = None
        self.handlers = plugin.make_handlers(os.getcwd())
        self.sessions = []

    def tearDown(self):
        for sid in self.sessions:
            self.handlers["kill"]({}, {"id": sid})
        self.plugin._POOL = None

    def _create(self):
        status, _ctype, body = self.handlers["create"](
            {}, {"shell": "cmd", "cols": 90, "rows": 24})
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.sessions.append(data["id"])
        return data

    def test_shells_reports_supported(self):
        status, _ctype, body = self.handlers["shells"]({}, None)
        data = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(data["supported"])

    def test_create_output_echo_kill(self):
        data = self._create()
        sid = data["id"]
        time.sleep(0.6)
        self.handlers["output"]({"id": sid, "since": 0}, None)   # 吃掉首屏

        self.handlers["input"]({}, {"id": sid, "data": "echo BACKEND-CHECK\r\n"})
        # 输出是 base64 编码的（终端字节里含 ANSI 序列与 UTF-8），
        # 所以要解码后再比对，不能直接在 JSON 里找原文
        import base64
        found = False
        for _ in range(30):
            time.sleep(0.15)
            _s, _c, body = self.handlers["output"](
                {"id": sid, "since": 0}, None)
            data = json.loads(body)
            text = "".join(base64.b64decode(pair[1]).decode("utf-8", "replace")
                           for pair in data.get("chunks") or [])
            if "BACKEND-CHECK" in text:
                found = True
                break
        self.assertTrue(found, "命令回显没出现")

        status, _ctype, body = self.handlers["kill"]({}, {"id": sid})
        self.assertTrue(json.loads(body)["ok"])
        self.sessions.remove(sid)

    def test_unknown_session_is_404(self):
        status, _ctype, _body = self.handlers["output"](
            {"id": "nope", "since": 0}, None)
        self.assertEqual(status, 404)

    def test_resize_validates(self):
        data = self._create()
        status, _ctype, body = self.handlers["resize"](
            {}, {"id": data["id"], "cols": 120, "rows": 40})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_workdir_falls_back_to_project_root(self):
        cwd = self.plugin._workdir(os.getcwd(), "no-such-instance")
        self.assertEqual(cwd, os.getcwd())

    def test_workdir_rejects_absolute_paths(self):
        """server 来自 HTTP 请求体，是**不可信输入**。

        os.path.join(base, "servers", "C:/Windows") 会直接返回 C:/Windows
        （join 遇到绝对路径丢掉前面所有段），终端就开在系统目录里了。
        """
        base = os.getcwd()
        for probe in ("C:/Windows", r"C:\Windows", r"\server\share",
                      "../../../Windows", "..", "t/../.."):
            self.assertEqual(self.plugin._workdir(base, probe), base, probe)

    def test_session_limit(self):
        """每个会话是一个真实子进程，必须设上限。

        不设的话连点几次按钮就能堆出几十个 cmd.exe，吃光内存和句柄。
        """
        self.plugin._POOL = None
        handlers = self.plugin.make_handlers(os.getcwd())
        made = []
        rejected = 0
        for _ in range(self.plugin._MAX_SESSIONS + 4):
            data = json.loads(handlers["create"]({}, {"shell": "cmd"})[2])
            if data.get("ok"):
                made.append(data["id"])
            else:
                rejected += 1
        try:
            self.assertEqual(len(made), self.plugin._MAX_SESSIONS)
            self.assertGreater(rejected, 0, "超过上限时应当拒绝")
        finally:
            for sid in made:
                handlers["kill"]({}, {"id": sid})
            self.plugin._POOL = None

    def test_dimensions_are_clamped(self):
        """cols=0 或 99999 会让 ConPTY 画出畸形画面，必须收敛到合理区间。"""
        self.plugin._POOL = None
        handlers = self.plugin.make_handlers(os.getcwd())
        cases = [((0, 0), (100, 30)), ((-5, 99999),
                                      (self.plugin._MIN_COLS, self.plugin._MAX_ROWS)),
                 ((10000, -1), (self.plugin._MAX_COLS, self.plugin._MIN_ROWS))]
        made = []
        try:
            for (cols, rows), (want_c, want_r) in cases:
                data = json.loads(handlers["create"](
                    {}, {"shell": "cmd", "cols": cols, "rows": rows})[2])
                self.assertTrue(data.get("ok"), data)
                made.append(data["id"])
                session = self.plugin._POOL.get(data["id"])
                self.assertEqual((session.pty.cols, session.pty.rows),
                                 (want_c, want_r), (cols, rows))
        finally:
            for sid in made:
                handlers["kill"]({}, {"id": sid})
            self.plugin._POOL = None

    def test_unknown_shell_is_rejected(self):
        """选了不存在的 shell 要报错，不能静默回退成 cmd。"""
        self.plugin._POOL = None
        handlers = self.plugin.make_handlers(os.getcwd())
        try:
            status, _ctype, body = handlers["create"]({}, {"shell": "no-such-shell"})
            self.assertEqual(status, 400)
            self.assertFalse(json.loads(body)["ok"])
        finally:
            self.plugin._POOL = None

    def test_workdir_accepts_a_real_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "servers", "demo"))
            got = self.plugin._workdir(tmp, "demo")
            self.assertEqual(os.path.realpath(got),
                             os.path.realpath(os.path.join(tmp, "servers", "demo")))


if __name__ == "__main__":
    unittest.main()
