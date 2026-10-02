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
from server_manager import (
    KIND_GENERIC,
    KIND_MINECRAFT,
    LAUNCH_CMD,
    LAUNCH_EXEC,
    LAUNCH_POWERSHELL,
)

Event = dict
OnEvent = Callable[[Event], None]

STATE_STOPPED = "stopped"
STATE_RUNNING = "running"
STATE_STOPPING = "stopping"


@dataclass
class LaunchPlan:
    """启动一个实例需要知道的一切。构造它不需要真的去动那个实例。"""

    name: str
    workdir: str
    cmd: list[str]
    kind: str = KIND_MINECRAFT
    # 通用实例与 Minecraft 实例各自看得懂的字段。界面上要显示「这是个什么
    # 东西」，所以 program 单独留一个位置，而不是把它塞进 java 里冒充。
    program: str = ""
    java: str = ""
    jar: str = ""
    min_mem: str = "1G"
    max_mem: str = "4G"
    log_path: str = ""


def _exec_args(value) -> list[str]:
    """把 launch.args 收敛成字符串列表。"""
    if not value:
        return []
    if isinstance(value, str):
        return value.split()
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


def _resolve_workdir(server_dir: str, raw: str) -> str:
    """通用实例的工作目录：相对路径按实例目录算，空则就是实例目录。"""
    raw = (raw or "").strip()
    if not raw:
        return server_dir
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(server_dir, raw))


def build_generic_plan(server_cfg: dict, server_dir: str) -> LaunchPlan:
    """给通用实例拼启动命令。

    三种方式：
      exec        —— 直接运行可执行文件，参数一项一个，不经 shell
      cmd         —— cmd.exe /c "<命令>"，管道、重定向、&& 都能用
      powershell  —— powershell -NoProfile -Command "<命令>"

    走 shell 的两种方式会把配置里的字符串原样交给 shell 解释，这是功能本身
    的要求（否则「执行一段命令」就无从谈起）。它的信任边界和「用户能改
    .server.json」「面板能发控制台指令」是同一条：能改这个配置的人本来就
    已经能在这台机器上跑东西了。
    """
    fallback_name = os.path.basename(os.path.normpath(server_dir)) or "instance"
    name = server_cfg.get("name") or fallback_name
    launch = server_cfg.get("launch") or {}
    mode = launch.get("mode") or LAUNCH_EXEC

    workdir = _resolve_workdir(server_dir, launch.get("workdir", ""))
    if not os.path.isdir(workdir):
        raise CoreError("app.error_workdir_missing", path=workdir)

    if mode == LAUNCH_EXEC:
        program = (launch.get("program") or "").strip()
        if not program:
            raise CoreError("app.error_program_missing")
        program_abs = program if os.path.isabs(program) else os.path.join(server_dir, program)
        program_abs = os.path.normpath(program_abs)
        if not os.path.isfile(program_abs):
            raise CoreError("app.error_program_not_found", path=program_abs)
        cmd = [program_abs] + _exec_args(launch.get("args"))
        summary = program_abs
    elif mode == LAUNCH_CMD:
        command = (launch.get("command") or "").strip()
        if not command:
            raise CoreError("app.error_command_missing")
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        cmd = [comspec, "/c", command]
        summary = command
    else:
        command = (launch.get("command") or "").strip()
        if not command:
            raise CoreError("app.error_command_missing")
        shell = "powershell.exe" if os.name == "nt" else "pwsh"
        cmd = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
        summary = command

    return LaunchPlan(
        name=name,
        workdir=workdir,
        cmd=cmd,
        kind=KIND_GENERIC,
        program=summary,
        log_path=os.path.join(server_dir, "logs", "mstl-console.log"),
    )


def build_launch_plan(
    server_cfg: dict,
    server_dir: str,
    project_dir: str,
    *,
    reporter: Optional[Reporter] = None,
) -> LaunchPlan:
    """按实例种类分流：Minecraft 走 Java/jar，通用实例走自己的启动配置。"""
    if (server_cfg.get("kind") or KIND_MINECRAFT) == KIND_GENERIC:
        return build_generic_plan(server_cfg, server_dir)

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
        workdir=os.path.dirname(jar_abs),
        cmd=cmd,
        kind=KIND_MINECRAFT,
        program=java,
        java=java,
        jar=jar_abs,
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
        self._seq = 0
        self._log_file = None

    # -- 只读 --------------------------------------------------------------

    def _reconcile(self) -> None:
        """进程已经没了但读线程还没翻状态时，先把状态摆正。调用方需持有锁。"""
        if (self._state == STATE_RUNNING and self._proc is not None
                and self._proc.poll() is not None):
            self._state = STATE_STOPPED
            self._exit_code = self._proc.returncode

    def state(self) -> str:
        with self._lock:
            self._reconcile()
            return self._state

    def history(self, tail: int = 200) -> list[Event]:
        with self._lock:
            return list(self._history[-tail:])

    def snapshot(self) -> dict:
        with self._lock:
            self._reconcile()
            pid = None
            if self._proc is not None and self._proc.poll() is None:
                pid = self._proc.pid
            return {
                "name": self.plan.name,
                "state": self._state,
                "exit_code": self._exit_code,
                "started_at": self._started_at,
                "pid": pid,
                "kind": self.plan.kind,
                "program": self.plan.program,
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
            self._open_log()
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
        self._close_log()

    def _push(self, event: Event) -> None:
        # 事件在落进历史前就带上实例名：这样一条全局流里能分辨哪行来自哪台服，
        # 重放历史时也不用临时补字段。
        event.setdefault("instance", self.plan.name)
        with self._lock:
            # 每条事件配一个单调递增的序号，让客户端能认出来「这条我已经见过」。
            # SSE 断线重连时服务端会把历史整段重放一遍，没有序号的话控制台里
            # 会出现成片的重复行。
            self._seq += 1
            event.setdefault("seq", self._seq)
            self._history.append(event)
            if len(self._history) > self._history_limit:
                del self._history[: len(self._history) - self._history_limit]
            if event.get("kind") == "out":
                self._append_log(event.get("text", ""))
        self._emit(event)

    def _open_log(self) -> None:
        """打开控制台日志文件并一直持有句柄。

        以前是每写一行就 open/close 一次，一台正常跑着的服务器每秒能刷出几十
        行日志，等于每秒几十次 open+close 系统调用。调用方需持有锁。
        """
        if not self.plan.log_path or self._log_file is not None:
            return
        try:
            os.makedirs(os.path.dirname(self.plan.log_path), exist_ok=True)
            self._log_file = open(self.plan.log_path, "a", encoding="utf-8")
        except OSError:
            self._log_file = None

    def _close_log(self) -> None:
        handle, self._log_file = self._log_file, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _append_log(self, text: str) -> None:
        # 只有读线程会产出 kind == "out"，调用方已持锁，是单写者
        if self._log_file is None:
            return
        try:
            self._log_file.write(text + "\n")
            self._log_file.flush()
        except (OSError, ValueError):
            self._close_log()

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

        plan = self._plan_builder(server_cfg, server_dir, self.project_dir)
        runtime = ServerRuntime(
            plan, on_event=lambda e, n=name: self._broadcast(n, e))
        with self._lock:
            current = self._runtimes.get(name)
            if current is not None and current.state() != STATE_STOPPED:
                raise CoreError("web.error.already_running", name=name)
            # 真正的 start() 必须留在锁里。它一返回状态就已经是 running 了，
            # 若放到锁外，两个并发的 /api/start 会双双看到 stopped、各自把
            # _runtimes[name] 覆盖成自己的那份，先起来的那个 java 进程就再也
            # 没人管得住——同一台服被拉起两次，端口和内存都要打架。
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
    "build_generic_plan",
    "ServerRuntime",
    "RuntimeManager",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "STATE_STOPPING",
]
