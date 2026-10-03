"""启动计划：通用实例三种模式、优先级、以及运行时的状态快照。"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from core.errors import CoreError
from core.runtime import (STATE_STOPPED, LaunchPlan, RuntimeManager, ServerRuntime,
                          build_generic_plan, build_launch_plan)
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
