"""通用实例的「首次安装」与端口探活。

这两件事都是 A 档里为了让「把项目挂进来」少走几步而加的：装依赖不必手动
先跑一遍；端口在不在监听面板要能自己看出来，而不是让用户去猜。
"""

import os
import tempfile
import sys
import unittest
from unittest import mock

from core import netprobe
from core import runtime as rt


def make_plan(**kwargs) -> rt.LaunchPlan:
    base = dict(name="svc", workdir=os.getcwd(), cmd=["echo", "hi"])
    base.update(kwargs)
    return rt.LaunchPlan(**base)


class SetupNeededTest(unittest.TestCase):
    def test_no_setup_command_means_nothing_to_do(self):
        self.assertFalse(rt.ServerRuntime(make_plan())._setup_needed())

    def test_marker_written_after_a_successful_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, ".mstl-setup.json")
            runtime = rt.ServerRuntime(make_plan(setup="echo ok", setup_marker=marker))
            self.assertTrue(runtime._setup_needed())
            runtime._write_setup_marker()
            self.assertFalse(runtime._setup_needed())

    def test_changing_the_command_runs_it_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, ".mstl-setup.json")
            runtime = rt.ServerRuntime(make_plan(setup="echo one", setup_marker=marker))
            runtime._write_setup_marker()
            other = rt.ServerRuntime(make_plan(setup="echo two", setup_marker=marker))
            self.assertTrue(other._setup_needed())

    def test_corrupt_marker_is_treated_as_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, ".mstl-setup.json")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("{not json")
            runtime = rt.ServerRuntime(make_plan(setup="echo x", setup_marker=marker))
            self.assertTrue(runtime._setup_needed())


class SetupArgvTest(unittest.TestCase):
    def test_no_command_gives_no_argv(self):
        self.assertIsNone(rt.setup_argv(make_plan()))

    def test_command_is_wrapped_for_the_platform(self):
        argv = rt.setup_argv(make_plan(setup="pip install astrbot"))
        self.assertIsNotNone(argv)
        self.assertEqual(argv[-1], "pip install astrbot")
        self.assertEqual(len(argv), 3)


class PortProbeTest(unittest.TestCase):
    def test_closed_port_is_false(self):
        self.assertFalse(netprobe.port_open(1))

    def test_invalid_ports_are_false(self):
        for bad in (0, -1, 70000, None, "8080"):
            self.assertFalse(netprobe.port_open(bad), bad)

    def test_open_socket_is_detected(self):
        import socket
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.assertTrue(netprobe.port_open(port))


class TreeKillTest(unittest.TestCase):
    """停止安装时要连子孙进程一起杀，否则 uv 会变孤儿继续写 venv。"""

    def test_kill_process_tree_handles_none_and_dead(self):
        self.assertIsNone(rt.kill_process_tree(None))
        import subprocess
        done = subprocess.Popen(["cmd", "/c", "exit 0"])
        done.wait()
        rt.kill_process_tree(done)          # 已退出，不该抛

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_kill_process_tree_kills_grandchild(self):
        import subprocess, time
        # 父进程拉一个长时间的子进程（cmd 起 ping），只杀父会留下 ping
        proc = subprocess.Popen(
            ["cmd", "/c", "ping -n 60 127.0.0.1 >nul"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(0.5)
        rt.kill_process_tree(proc, timeout=10)
        self.assertIsNotNone(proc.poll(), "父进程应当已退出")


class PreparingStateTest(unittest.TestCase):
    def test_stop_during_preparing_does_not_raise(self):
        """准备中按停止应该能停，而不是报「没在运行」。"""
        runtime = rt.ServerRuntime(make_plan(setup="echo x"))
        with runtime._lock:
            runtime._state = rt.STATE_PREPARING
        runtime.stop()
        self.assertEqual(runtime.state(), rt.STATE_STOPPED)


if __name__ == "__main__":
    unittest.main()
