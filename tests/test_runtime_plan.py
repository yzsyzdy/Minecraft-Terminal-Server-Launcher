"""启动计划：通用实例三种模式、优先级、以及运行时的状态快照。"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from unittest import mock

from core.errors import CoreError
from core.runtime import (STATE_STOPPED, LaunchPlan, RuntimeManager, ServerRuntime,
                          _restart_policy_of, build_generic_plan, build_launch_plan)
from server_launcher import PRIORITY_CHOICES


class GenericPlanTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-plan-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _cfg(self, **launch):
        base = {"mode": "exec", "program": "", "args": [], "command": "",
                "workdir": ""}
        base.update(launch)
        return {"name": "g", "kind": "generic", "launch": base}

    def test_exec_mode_uses_the_program_directly(self):
        program = os.path.join(self.dir, "tool.exe")
        with open(program, "w", encoding="utf-8") as f:
            f.write("stub")
        plan = build_generic_plan(self._cfg(program=program, args=["-a", "b"]), self.dir)
        self.assertEqual(plan.cmd, [program, "-a", "b"])
        self.assertEqual(plan.program, program)

    def test_cmd_mode_goes_through_comspec(self):
        plan = build_generic_plan(self._cfg(mode="cmd", command="echo hi"), self.dir)
        self.assertEqual(plan.cmd[1:3], ["/c", "echo hi"])
        self.assertIn("cmd", os.path.basename(plan.cmd[0]).lower())

    def test_powershell_mode(self):
        plan = build_generic_plan(self._cfg(mode="powershell", command="Get-Date"),
                                  self.dir)
        self.assertIn("-Command", plan.cmd)
        self.assertEqual(plan.cmd[-1], "Get-Date")

    def test_exec_without_program_is_rejected(self):
        with self.assertRaises(CoreError):
            build_generic_plan(self._cfg(), self.dir)

    def test_exec_with_missing_program_is_rejected(self):
        with self.assertRaises(CoreError):
            build_generic_plan(self._cfg(program=os.path.join(self.dir, "nope.exe")),
                               self.dir)

    def test_cmd_without_command_is_rejected(self):
        with self.assertRaises(CoreError):
            build_generic_plan(self._cfg(mode="cmd"), self.dir)

    def test_relative_program_is_resolved_against_the_instance(self):
        program = os.path.join(self.dir, "bin", "tool.exe")
        os.makedirs(os.path.dirname(program))
        with open(program, "w", encoding="utf-8") as f:
            f.write("stub")
        plan = build_generic_plan(self._cfg(program=os.path.join("bin", "tool.exe")),
                                  self.dir)
        self.assertEqual(plan.cmd[0], os.path.normpath(program))

    def test_workdir_defaults_to_the_instance_directory(self):
        plan = build_generic_plan(self._cfg(mode="cmd", command="echo"), self.dir)
        self.assertEqual(plan.workdir, self.dir)

    def test_relative_workdir_is_resolved(self):
        os.makedirs(os.path.join(self.dir, "sub"))
        plan = build_generic_plan(self._cfg(mode="cmd", command="echo", workdir="sub"),
                                  self.dir)
        self.assertEqual(plan.workdir, os.path.join(self.dir, "sub"))

    def test_missing_workdir_is_rejected(self):
        with self.assertRaises(CoreError):
            build_generic_plan(self._cfg(mode="cmd", command="echo", workdir="nope"),
                               self.dir)

    # -- 进程优先级（面板里设了却从未生效的那个）--------------------------

    def test_priority_reaches_the_plan(self):
        plan = build_generic_plan({**self._cfg(mode="cmd", command="echo"),
                                   "process_priority": "low"}, self.dir)
        self.assertEqual(plan.priority, "low")

    def test_priority_defaults_to_normal(self):
        plan = build_generic_plan(self._cfg(mode="cmd", command="echo"), self.dir)
        self.assertEqual(plan.priority, "normal")

    def test_unknown_priority_falls_back_to_normal(self):
        plan = build_generic_plan({**self._cfg(mode="cmd", command="echo"),
                                   "process_priority": "banana"}, self.dir)
        self.assertEqual(plan.priority, "normal")

    def test_every_choice_survives(self):
        for choice in PRIORITY_CHOICES:
            plan = build_generic_plan({**self._cfg(mode="cmd", command="echo"),
                                       "process_priority": choice}, self.dir)
            self.assertEqual(plan.priority, choice)

    def test_priority_case_insensitive(self):
        plan = build_generic_plan({**self._cfg(mode="cmd", command="echo"),
                                   "process_priority": " LOW "}, self.dir)
        self.assertEqual(plan.priority, "low")


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-disp-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_generic_kind_is_dispatched(self):
        cfg = {"name": "g", "kind": "generic",
               "launch": {"mode": "cmd", "program": "", "args": [],
                          "command": "echo", "workdir": ""}}
        plan = build_launch_plan(cfg, self.dir, self.dir)
        self.assertEqual(plan.kind, "generic")

    def test_minecraft_without_jar_is_rejected(self):
        cfg = {"name": "m", "kind": "minecraft", "jar": "server.jar"}
        with self.assertRaises(CoreError):
            build_launch_plan(cfg, self.dir, self.dir)


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-rt-")
        self.plan = LaunchPlan(
            name="t", workdir=self.dir,
            # 用当前解释器起一个立刻退出的进程，跨平台且不依赖 cmd
            cmd=[sys.executable, "-c", "pass"],
            kind="generic", min_mem="2G", max_mem="4G",
            log_path=os.path.join(self.dir, "log.txt"),
        )

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_priority_is_passed_to_the_spawn(self):
        """start() 里写死 "normal" 过——面板设置的优先级必须真的传到 spawn。"""
        seen = {}
        runtime_mod = sys.modules["core.runtime"]
        original = runtime_mod.spawn_priority_process

        def spy(cmd, cwd, priority, **kwargs):
            seen["priority"] = priority
            return original(cmd, cwd, priority, **kwargs)

        runtime_mod.spawn_priority_process = spy
        try:
            plan = LaunchPlan(name="t", workdir=self.dir,
                              cmd=[sys.executable, "-c", "pass"],
                              kind="generic", priority="low",
                              log_path=os.path.join(self.dir, "log.txt"))
            rt = ServerRuntime(plan)
            rt.start()
            for _ in range(50):
                if "priority" in seen or rt.state() != "running":
                    break
                time.sleep(0.02)
            rt.stop(timeout=5)
        finally:
            runtime_mod.spawn_priority_process = original
        self.assertEqual(seen.get("priority"), "low")

    def test_snapshot_shape(self):
        rt = ServerRuntime(self.plan)
        snap = rt.snapshot()
        for key in ("name", "state", "pid", "exit_code", "kind", "min_mem",
                    "max_mem", "workdir", "command"):
            self.assertIn(key, snap)
        self.assertEqual(snap["state"], STATE_STOPPED)
        self.assertEqual(snap["min_mem"], "2G")

    def test_stopped_runtime_keeps_reporting_plan_memory(self):
        """快照里的 min_mem/max_mem 是「启动那一刻」的值，磁盘改了它不会跟着变。

        这正是 /api/state 不能用它回显设置的原因，这里把这个前提钉住。
        """
        rt = ServerRuntime(self.plan)
        self.assertEqual(rt.snapshot()["min_mem"], "2G")

    def test_start_and_stop_a_real_process(self):
        rt = ServerRuntime(self.plan)
        rt.start()
        self.assertEqual(rt.state(), "running")
        self.assertIsNotNone(rt.snapshot()["pid"])
        rt.stop(timeout=10)
        self.assertEqual(rt.state(), STATE_STOPPED)

    def test_start_twice_is_rejected(self):
        rt = ServerRuntime(self.plan)
        rt.start()
        try:
            with self.assertRaises(CoreError):
                rt.start()
        finally:
            rt.stop(timeout=10)


class RestartPolicyTest(unittest.TestCase):
    """.server.json 是可以手改的，策略读出来的值必须收敛成可信的类型。"""

    def test_bool_like_strings_are_read_literally(self):
        # bool("false") 是 True —— 用户明确写了 false，不能变成「打开」
        self.assertFalse(_restart_policy_of({"auto_restart": "false"})["auto_restart"])
        self.assertFalse(_restart_policy_of({"auto_restart": ""})["auto_restart"])
        self.assertFalse(_restart_policy_of({"auto_restart": None})["auto_restart"])
        self.assertTrue(_restart_policy_of({"auto_restart": "true"})["auto_restart"])
        self.assertTrue(_restart_policy_of({"auto_restart": True})["auto_restart"])
        self.assertTrue(_restart_policy_of({"auto_restart": 1})["auto_restart"])

    def test_restart_max_is_clamped(self):
        # 1e9 等于没有熔断，负数没有意义
        self.assertEqual(_restart_policy_of({"restart_max": 999})["restart_max"], 20)
        self.assertEqual(_restart_policy_of({"restart_max": -3})["restart_max"], 0)
        self.assertEqual(_restart_policy_of({"restart_max": "abc"})["restart_max"], 5)
        self.assertEqual(_restart_policy_of({"restart_max": "7"})["restart_max"], 7)

    def test_missing_keys_mean_off(self):
        policy = _restart_policy_of({})
        self.assertFalse(policy["auto_restart"])
        self.assertEqual(policy["restart_max"], 5)

    def test_the_plan_carries_the_policy(self):
        """策略必须进 LaunchPlan：重启要沿用同一套参数，不能过一会儿再读配置。"""
        d = tempfile.mkdtemp(prefix="mstl-test-policy-")
        try:
            plan = build_generic_plan(
                {"name": "x", "kind": "generic", "auto_restart": True,
                 "restart_max": 3,
                 "launch": {"mode": "exec", "program": sys.executable}},
                d)
            self.assertTrue(plan.auto_restart)
            self.assertEqual(plan.restart_max, 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class FakeTimer:
    """替掉 threading.Timer：只记录「隔多久、要跑谁」，不真的等。"""

    instances: list = []

    def __init__(self, delay, fn):
        self.delay = delay
        self.fn = fn
        self.daemon = False
        self.started = False
        self.cancelled = False
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


class CrashRestartTest(unittest.TestCase):
    """崩溃自动重启的决策逻辑。

    核心是三件事：只对「非 0 退出」重启、手动停止绝不重启、连续崩到上限就停手。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-restart-")
        FakeTimer.instances = []
        self._timer_patch = mock.patch.object(threading, "Timer", FakeTimer)
        self._timer_patch.start()
        self.events = []
        self.plan = LaunchPlan(
            name="t", workdir=self.dir,
            cmd=[sys.executable, "-c", "pass"], kind="generic",
            log_path=os.path.join(self.dir, "log.txt"),
            auto_restart=True, restart_max=3)

    def tearDown(self):
        self._timer_patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _rt(self, **overrides):
        return ServerRuntime(replace(self.plan, **overrides),
                             on_event=self.events.append)

    def _note_keys(self):
        return [e.get("key") for e in self.events if e.get("kind") == "note"]

    def test_policy_off_means_no_restart(self):
        self._rt(auto_restart=False)._maybe_restart(1)
        self.assertEqual(FakeTimer.instances, [])

    def test_clean_exit_is_not_a_crash(self):
        """退出码 0 = 控制台里敲了 stop / 插件把服务器关了，重启等于对着干。"""
        self._rt()._maybe_restart(0)
        self.assertEqual(FakeTimer.instances, [])

    def test_manual_stop_wins_over_the_policy(self):
        rt = self._rt()
        rt._stop_requested = True
        rt._maybe_restart(1)
        self.assertEqual(FakeTimer.instances, [])

    def test_crash_schedules_a_backed_off_restart(self):
        rt = self._rt()
        rt._maybe_restart(1)
        self.assertEqual(len(FakeTimer.instances), 1)
        timer = FakeTimer.instances[0]
        self.assertTrue(timer.started)
        self.assertEqual(timer.delay, 5.0)
        self.assertIn("web.console.restart_scheduled", self._note_keys())
        self.assertTrue(rt.snapshot()["restart_pending"])

    def test_backoff_doubles_and_is_capped(self):
        rt = self._rt(restart_max=5)
        for _ in range(5):
            rt._maybe_restart(1)
        self.assertEqual([t.delay for t in FakeTimer.instances],
                         [5.0, 10.0, 20.0, 40.0, 60.0])

    def test_gives_up_after_the_limit(self):
        rt = self._rt(restart_max=2)
        rt._maybe_restart(1)
        rt._maybe_restart(1)
        self.assertEqual(len(FakeTimer.instances), 2)
        rt._maybe_restart(1)                 # 第三次：不再排重启
        self.assertEqual(len(FakeTimer.instances), 2)
        self.assertIn("web.console.restart_give_up", self._note_keys())

    def test_a_long_run_resets_the_budget(self):
        """活过一分钟再崩，说明不是启动配置的问题，预算该重新算。"""
        rt = self._rt(restart_max=1)
        rt._restart_count = 1
        rt._started_at = time.time() - 120
        rt._maybe_restart(1)
        self.assertEqual(len(FakeTimer.instances), 1)
        self.assertEqual(rt._restart_count, 1)

    def test_cancelling_clears_the_pending_state(self):
        rt = self._rt()
        rt._maybe_restart(1)
        timer = FakeTimer.instances[0]
        rt._cancel_restart_locked()
        self.assertTrue(timer.cancelled)
        self.assertFalse(rt.snapshot()["restart_pending"])

    def test_restart_fire_starts_the_process_again(self):
        rt = self._rt()
        rt._restart_count = 1
        with mock.patch.object(rt, "start") as start:
            rt._restart_fire()
        start.assert_called_once()

    def test_restart_fire_does_nothing_after_a_manual_stop(self):
        rt = self._rt()
        rt._restart_count = 1
        rt._stop_requested = True
        with mock.patch.object(rt, "start") as start:
            rt._restart_fire()
        start.assert_not_called()

    def test_restart_failure_is_reported_not_retried(self):
        rt = self._rt()
        rt._restart_count = 2
        with mock.patch.object(rt, "start",
                               side_effect=CoreError("web.error.spawn_failed")):
            rt._restart_fire()
        self.assertIn("web.console.restart_failed", self._note_keys())
        self.assertEqual(rt._restart_count, 0)

    def test_a_real_nonzero_exit_schedules_a_restart(self):
        """端到端：真的跑一个退出码 3 的进程，看 _pump 会不会排重启。"""
        rt = self._rt(cmd=[sys.executable, "-c", "import sys; sys.exit(3)"])
        rt.start()
        for _ in range(200):
            if FakeTimer.instances:
                break
            time.sleep(0.02)
        # FakeTimer 不会真的到点，所以不用担心它在测试结束后去拉起进程
        rt._stop_requested = True
        self.assertEqual(len(FakeTimer.instances), 1)
        self.assertEqual(rt.snapshot()["exit_code"], 3)


class ManagerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="mstl-test-mgr-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_unknown_instance_is_rejected(self):
        mgr = RuntimeManager(self.dir)
        with self.assertRaises(CoreError):
            mgr.start_by_name(self.dir, "nope")

    def test_state_of_returns_none_for_unknown(self):
        mgr = RuntimeManager(self.dir)
        self.assertIsNone(mgr.state_of("nope"))

    def test_state_of_returns_a_snapshot_for_a_known_runtime(self):
        mgr = RuntimeManager(self.dir)
        plan = LaunchPlan(name="x", workdir=self.dir,
                          cmd=[sys.executable, "-c", "pass"], kind="generic",
                          min_mem="3G", log_path=os.path.join(self.dir, "l.txt"))
        mgr._runtimes["x"] = ServerRuntime(plan)
        snap = mgr.state_of("x")
        self.assertIsNotNone(snap)
        self.assertEqual(snap["min_mem"], "3G")
        # 没启动过就是停止状态，不该出现在 running() 里
        self.assertNotIn("x", mgr.running())


if __name__ == "__main__":
    unittest.main()
