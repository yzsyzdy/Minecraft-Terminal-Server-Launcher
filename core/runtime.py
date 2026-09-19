"""
实例运行时：把服务端当成一个可以被远程操控的子进程来管。

CLI 的做法是让子进程直接继承 stdin / stdout，人就在终端跟前，敲什么进去
就是什么。Web 面板和 TUI 都吃不了这一套：前者压根没有终端，后者要独占
整个屏幕。所以这里换成 PIPE 双向 + 一个读线程：

  输出行 → 环形缓冲（同时追加到 logs/mstl-console.log）→ 广播给订阅者
  输入行 → 直接写进子进程的 stdin

订阅者是界面层的事。core 这边只负责把事件推出去，谁在听、听来干什么，
一概不问。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .errors import CoreError
from .java import resolve_java_for_launch
from .report import Reporter, as_reporter
from server_launcher import build_jvm_command, spawn_priority_process

Event = dict
OnEvent = Callable[[Event], None]

STATE_STOPPED = "stopped"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"


@dataclass
class LaunchPlan:
    """启动一个实例需要知道的一切。构造它不需要真的去动那个实例。"""

    name: str
    java: str
    jar: str
    workdir: str
    cmd: list[str]
    min_mem: str = "1G"
    max_mem: str = "4G"
    log_path: str = ""


def build_launch_plan(
    server_cfg: dict,
    server_dir: str,
    project_dir: str,
    *,
    reporter: Optional[Reporter] = None,
) -> LaunchPlan:
    """解析 Java、校验核心 jar、拼出完整的 JVM 命令行。"""
    reporter = as_reporter(reporter)
    fallback_name = os.path.basename(os.path.normpath(server_dir)) or "server"
    name = server_cfg.get("name") or fallback_name

    java = resolve_java_for_launch(
        server_cfg.get("java_path"),
        project_dir,
        server_cfg.get("mc_version", ""),
        reporter=reporter,
    )

    jar_rel = server_cfg.get("jar", "server.jar")
    jar_abs = os.path.abspath(os.path.join(server_dir, jar_rel))
    if not os.path.isfile(jar_abs):
        raise CoreError("app.error_jar_missing", path=jar_abs)

    min_mem = server_cfg.get("min_mem", "1G")
    max_mem = server_cfg.get("max_mem", "4G")
    cmd = build_jvm_command(
        java, jar_abs, min_mem, max_mem,
        server_cfg.get("extra_jvm_args") or None,
        server_cfg.get("extra_server_args") or None,
        nogui=True,
    )
    return LaunchPlan(
        name=name,
        java=java,
        jar=jar_abs,
        workdir=os.path.dirname(jar_abs),
        cmd=cmd,
        min_mem=min_mem,
        max_mem=max_mem,
        log_path=os.path.join(server_dir, "logs", "mstl-console.log"),
    )


class ServerRuntime:
    """一个实例的进程、输出缓冲与状态。所有公开方法都是线程安全的。"""

    def __init__(self, plan: LaunchPlan, *, on_event: Optional[OnEvent] = None,
                 history: int = 800):
        self.plan = plan
        self._on_event = on_event
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._state = STATE_STOPPED
        self._exit_code: Optional[int] = None
        self._started_at: Optional[float] = None
        self._history: list[Event] = []
        self._history_limit = history
        self._reader: Optional[threading.Thread] = None

    # -- 只读 --------------------------------------------------------------

    def state(self) -> str:
        with self._lock:
            if (self._state == STATE_RUNNING and self._proc is not None
                    and self._proc.poll() is not None):
                # 读线程还没来得及翻状态，先兜一下
                self._state = STATE_STOPPED
                self._exit_code = self._proc.returncode
            return self._state

    def history(self, tail: int = 200) -> list[Event]:
        with self._lock:
            return list(self._history[-tail:])

    def snapshot(self) -> dict:
        with self._lock:
            pid = None
            if self._proc is not None and self._proc.poll() is None:
                pid = self._proc.pid
            return {
                "name": self.plan.name,
                "state": self._state,
                "exit_code": self._exit_code,
                "started_at": self._started_at,
                "pid": pid,
                "java": self.plan.java,
                "jar": self.plan.jar,
                "workdir": self.plan.workdir,
                "min_mem": self.plan.min_mem,
                "max_mem": self.plan.max_mem,
                "command": list(self.plan.cmd),
            }

    # -- 控制 --------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise CoreError("web.error.already_running", name=self.plan.name)
            if self.plan.log_path:
                os.makedirs(os.path.dirname(self.plan.log_path), exist_ok=True)
            try:
                self._proc = spawn_priority_process(
                    self.plan.cmd, self.plan.workdir, "normal",
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
            except OSError as e:
                raise CoreError("web.error.spawn_failed", msg=str(e)) from e

            self._state = STATE_RUNNING
            self._exit_code = None
            self._started_at = time.time()
            self._note("web.console.started", pid=self._proc.pid)
            self._emit_state()
            self._reader = threading.Thread(
                target=self._pump, args=(self._proc,), daemon=True)
            self._reader.start()

    def send(self, command: str) -> None:
        command = (command or "").strip()
        if not command:
            return
        with self._lock:
            if (self._proc is None or self._proc.poll() is not None
                    or self._proc.stdin is None):
                raise CoreError("web.error.not_running", name=self.plan.name)
            try:
                self._proc.stdin.write(command + "\n")
                self._proc.stdin.flush()
            except (OSError, ValueError) as e:
                raise CoreError("web.error.send_failed", msg=str(e)) from e
        self._push({"type": "line", "kind": "input", "text": command})

    def stop(self, timeout: float = 20.0) -> None:
        """先好好说：往控制台发 stop；超时再 terminate，再不行就 kill。"""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                raise CoreError("web.error.not_running", name=self.plan.name)
            proc = self._proc
            self._state = STATE_STOPPING
            self._note("web.console.stop_sent")
            self._emit_state()
            try:
                if proc.stdin:
                    proc.stdin.write("stop\n")
                    proc.stdin.flush()
            except (OSError, ValueError):
                pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def kill(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    # -- 内部 --------------------------------------------------------------

    def _pump(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                self._push({"type": "line", "kind": "out",
                            "text": line.rstrip("\r\n")})
        except (OSError, ValueError):
            pass
        code = proc.wait()
        with self._lock:
            self._state = STATE_STOPPED
            self._exit_code = code
        self._note("web.console.exited", code=code)
        self._emit_state()

    def _push(self, event: Event) -> None:
        # 事件在落进历史前就带上实例名：这样一条全局流里能分辨哪行来自哪台服，
        # 重放历史时也不用临时补字段。
        event.setdefault("instance", self.plan.name)
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._history_limit:
                del self._history[: len(self._history) - self._history_limit]
        if event.get("kind") == "out" and self.plan.log_path:
            self._append_log(event.get("text", ""))
        self._emit(event)

    def _append_log(self, text: str) -> None:
        try:
            with open(self.plan.log_path, "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass

    def _note(self, key: str, **kwargs) -> None:
        self._push({"type": "line", "kind": "note", "key": key, "kwargs": kwargs})

    def _emit_state(self) -> None:
        # 走 _push 而不是 _emit：状态也要进历史，这样中途连上来的客户端
        # 一上来就知道实例现在是什么状态，不用等下一次变化。
        snap = self.snapshot()
        snap.pop("command", None)
        self._push({"type": "state", **snap})

    def _emit(self, event: Event) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception:
            pass


class RuntimeManager:
    """把实例名映射到运行时，并对外提供一个全局事件流。"""

    def __init__(self, project_dir: str, *, plan_builder=build_launch_plan):
        self.project_dir = project_dir
        self._plan_builder = plan_builder
        self._runtimes: dict[str, ServerRuntime] = {}
        self._subs: list[OnEvent] = []
        self._lock = threading.RLock()

    # -- 事件流 ------------------------------------------------------------

    def subscribe(self, callback: OnEvent) -> Callable[[], None]:
        with self._lock:
            self._subs.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subs:
                    self._subs.remove(callback)

        return unsubscribe

    def _broadcast(self, name: str, event: Event) -> None:
        payload = dict(event)
        payload["instance"] = name
        with self._lock:
            subs = list(self._subs)
        for cb in subs:
            try:
                cb(payload)
            except Exception:
                pass

    # -- 实例 --------------------------------------------------------------

    def _name_of(self, server_cfg: dict, server_dir: str) -> str:
        fallback = os.path.basename(os.path.normpath(server_dir)) or "server"
        return server_cfg.get("name") or fallback

    def find(self, servers_dir: str, name: str) -> tuple[dict, str]:
        """按实例名从磁盘上找配置，找不到就抛错。"""
        from server_manager import load_server_config, list_servers

        for s in list_servers(servers_dir):
            if s.get("name") == name:
                return load_server_config(s["_path"]), s["_path"]
        raise CoreError("web.error.unknown_instance", name=name)

    def start(self, server_cfg: dict, server_dir: str) -> ServerRuntime:
        name = self._name_of(server_cfg, server_dir)
        existing = self._runtimes.get(name)
        if existing is not None and existing.state() != STATE_STOPPED:
            raise CoreError("web.error.already_running", name=name)

        plan = self._plan_builder(server_cfg, server_dir, self.project_dir)
        runtime = ServerRuntime(
            plan, on_event=lambda e, n=name: self._broadcast(n, e))
        with self._lock:
            current = self._runtimes.get(name)
            if current is not None and current.state() != STATE_STOPPED:
                raise CoreError("web.error.already_running", name=name)
            self._runtimes[name] = runtime
        runtime.start()
        return runtime

    def start_by_name(self, servers_dir: str, name: str) -> ServerRuntime:
        server_cfg, server_dir = self.find(servers_dir, name)
        return self.start(server_cfg, server_dir)

    def get(self, name: str) -> Optional[ServerRuntime]:
        return self._runtimes.get(name)

    def require(self, name: str) -> ServerRuntime:
        runtime = self._runtimes.get(name)
        if runtime is None:
            raise CoreError("web.error.not_running", name=name)
        return runtime

    def stop(self, name: str) -> None:
        self.require(name).stop()

    def send(self, name: str, command: str) -> None:
        self.require(name).send(command)

    def state_of(self, name: str) -> Optional[dict]:
        runtime = self._runtimes.get(name)
        return runtime.snapshot() if runtime is not None else None

    def histories(self) -> dict[str, list[Event]]:
        return {name: rt.history() for name, rt in self._runtimes.items()}


__all__ = [
    "LaunchPlan",
    "build_launch_plan",
    "ServerRuntime",
    "RuntimeManager",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "STATE_STOPPING",
]
