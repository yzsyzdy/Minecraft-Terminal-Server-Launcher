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
from . import runstate
from server_launcher import build_jvm_command, spawn_priority_process, PRIORITY_CHOICES
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

# 崩溃自动重启的退避：5 秒起、每次翻倍、封顶 60 秒。
#
# 为什么要退避而不是立刻拉起来：配置错的东西（jar 不匹配、Java 版本不对）
# 会让进程在几百毫秒内反复崩，不退避就是在拿 CPU 和磁盘换噪音。
_RESTART_BASE_DELAY = 5.0
_RESTART_MAX_DELAY = 60.0
# 「活得够久」的门槛：活过一分钟再崩，说明这次崩溃不是启动配置的问题，
# 重启预算应当重置。否则一台每天崩一次的服务器会在第五次之后永久放弃。
_RESTART_HEALTHY_SECONDS = 60.0


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
    # 进程优先级。以前 start() 里写死 "normal"，于是面板里设置的「低优先级」
    # 存得下、看得见，从面板（或随软件启动）拉起来时却从来没生效过——
    # 只有终端前台启动那条路会去读这个设置。
    priority: str = "normal"
    # 崩溃自动重启。策略放进 plan 而不是运行时现读配置：start() 拉起进程用的
    # 就是这份 plan，重启要沿用同一套参数（内存、jar、Java 路径）。过一会儿
    # 再去读一遍配置的话，「改了但还没保存」的实例会重启成另一个东西。
    auto_restart: bool = False
    restart_max: int = 5


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


def _priority_of(server_cfg: dict) -> str:
    """取实例的进程优先级，认不出来的值一律当 normal。"""
    value = str((server_cfg or {}).get("process_priority") or "normal").strip().lower()
    return value if value in PRIORITY_CHOICES else "normal"


def _restart_policy_of(server_cfg: dict) -> dict:
    """取实例的崩溃重启策略。

    配置来源是 .server.json，而那个文件是可以手改的：`"auto_restart": "false"`
    如果按「非空即真」认，就等于用户明确写了 false 却被打开。所以字符串按字面
    意思认，和 server_manager._as_bool 同一套口径（core 不能反向 import
    server_manager 的那个私有函数，这里就地写一份）。
    """
    cfg = server_cfg or {}
    raw = cfg.get("auto_restart")
    if isinstance(raw, bool):
        auto = raw
    elif isinstance(raw, (int, float)):
        auto = raw != 0
    else:
        auto = str(raw or "").strip().lower() in ("1", "true", "yes", "on", "y")
    try:
        # 0 表示关掉，写 1e9 就等于没有熔断，负数没有意义
        restart_max = max(0, min(20, int(cfg.get("restart_max"))))
    except (TypeError, ValueError):
        restart_max = 5
    return {"auto_restart": auto, "restart_max": restart_max}


def _global_java_path(project_dir: str) -> str:
    """config.json 里的全局 java_path。

    实例没写 java_path 时用它。终端前台启动一直是这么做的
    （start.py: server_cfg.get("java_path") or config.get("java_path")），
    而面板这条路原来只看实例自己的值，于是同一个全局设置两边行为不一致。
    load_config 自己会把缺失/损坏的配置降级成默认值，不需要再包一层。
    """
    if not project_dir:
        return ""
    from config import load_config
    return str(load_config(project_dir).get("java_path") or "")


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
        priority=_priority_of(server_cfg),
        **_restart_policy_of(server_cfg),
    )


EULA_HEADER = (
    "#By changing the setting below to TRUE you are indicating your "
    "agreement to our EULA (https://aka.ms/MinecraftEULA).\n"
)


def eula_accepted(server_dir: str) -> bool:
    """eula.txt 里是否写了 eula=true。文件不存在、读不出、没这一行都算没同意。"""
    path = os.path.join(server_dir, "eula.txt")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                text = line.strip()
                if text.startswith("#") or "=" not in text:
                    continue
                key, _, value = text.partition("=")
                if key.strip().lower() == "eula":
                    return value.strip().lower() in ("true", "1", "yes")
    except OSError:
        return False
    return False


def accept_eula(server_dir: str) -> None:
    """把 eula.txt 改写成 eula=true。没这个文件就新建一份，写法照上游。

    只动 eula 这一行：服务端自己写的注释和别的键都会保留下来，
    和「用户在终端里点了个同意」的结果一致。
    """
    os.makedirs(server_dir, exist_ok=True)
    path = os.path.join(server_dir, "eula.txt")
    lines: list[str] = []
    replaced = False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []

    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key and not line.strip().startswith("#") and key.lower() == "eula":
            lines[i] = "eula=true"
            replaced = True
    if not replaced:
        if not lines:
            lines = [EULA_HEADER.rstrip("\n")]
        lines.append("eula=true")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


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
        server_cfg.get("java_path") or _global_java_path(project_dir),
        project_dir,
        server_cfg.get("mc_version", ""),
        reporter=reporter,
    )

    jar_rel = server_cfg.get("jar", "server.jar")
    jar_abs = os.path.abspath(os.path.join(server_dir, jar_rel))
    if not os.path.isfile(jar_abs):
        raise CoreError("app.error_jar_missing", path=jar_abs)

    # 面板没办法像终端那样中途问一句「同意 EULA 吗」，所以先拦住再让前端
    # 弹框。放过去的话，java 会打一行 "You must agree to the EULA" 就退出，
    # 用户只能自己在文件管理器里找 eula.txt。
    if not eula_accepted(server_dir):
        raise CoreError("web.error.eula_required", name=name)

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
        priority=_priority_of(server_cfg),
        **_restart_policy_of(server_cfg),
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
        # 崩溃自动重启的状态。_stop_requested 用来区分「用户让我停」和
        # 「它自己崩了」——前者绝不能重启，否则点了停止的服务器过 5 秒又回来了。
        self._stop_requested = False
        self._restart_timer: Optional[threading.Timer] = None
        self._restart_count = 0
        self._restart_at: Optional[float] = None

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
            restart_at = self._restart_at
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
                "auto_restart": self.plan.auto_restart,
                "restart_max": self.plan.restart_max,
                "restart_count": self._restart_count,
                # 面板拿这个时间戳做倒计时；None 表示没有在等重启
                "restart_at": restart_at,
                "restart_pending": restart_at is not None,
            }

    # -- 控制 --------------------------------------------------------------

    def start(self) -> None:
        # 锁序规则（全文件唯一一条，改动前请先读）：事件回调最终会走到
        # RuntimeManager._broadcast 去取 manager 锁，而 RuntimeManager.start
        # 是「持 manager 锁 → 调 runtime.start() → 取 runtime 锁」。
        # 于是只要在持 runtime 锁的情况下发事件，就会出现
        #   A: 持 manager 锁，等 runtime 锁
        #   B: 持 runtime 锁，等 manager 锁
        # 的 ABBA 死锁——Web 面板用的是 ThreadingHTTPServer + daemon_threads，
        # 两个请求真并发，一次「启动」撞上一次「停止」就够把两个请求线程
        # 一起挂死（面板随后整个失去响应）。所以：**绝不在持 self._lock 时
        # 调用 _push / _emit / _emit_state / _note**。
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise CoreError("web.error.already_running", name=self.plan.name)
            # 手动启动等于「我要它现在跑」，之前排着的重启就没意义了
            self._stop_requested = False
            self._cancel_restart_locked()
            self._open_log()
            try:
                self._proc = spawn_priority_process(
                    self.plan.cmd, self.plan.workdir, self.plan.priority,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                )
            except OSError as e:
                # spawn 失败时 pump 线程根本没起来，_close_log 的唯一调用点
                # （_pump 的 finally）永远不会执行。不在这里补一次，日志句柄
                # 会一直留到进程结束 —— Windows 上就是实例目录「文件被占用」，
                # 删不掉，而且每次失败启动都泄漏一个。
                self._close_log()
                raise CoreError("web.error.spawn_failed", msg=str(e)) from e

            self._state = STATE_RUNNING
            self._exit_code = None
            self._started_at = time.time()
            pid = self._proc.pid
            self._reader = threading.Thread(
                target=self._pump, args=(self._proc,), daemon=True)
            self._reader.start()

        self._note("web.console.started", pid=pid)
        self._emit_state()

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
            reader = self._reader
            # 用户明确要停：关掉自动重启的念头。不设这一位的话，stop() 让进程
            # 以退出码 0 结束时虽然不会重启，但若 stop 之后进程被信号打断
            # （退出码非 0），面板里那台「已经停了」的服务器会自己站起来。
            self._stop_requested = True
            self._cancel_restart_locked()
            self._state = STATE_STOPPING
            try:
                if proc.stdin:
                    proc.stdin.write("stop\n")
                    proc.stdin.flush()
            except (OSError, ValueError):
                pass

        # 事件在锁外发，理由见 start() 顶部的锁序说明。
        self._note("web.console.stop_sent")
        self._emit_state()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        # 进程确实退出了，但状态还是 stopping：把状态改成 stopped、记下退出码
        # 的是 _pump 线程，它要先读完 stdout 里剩下的内容、再 proc.wait() 一遍。
        # 不等它，stop() 刚返回时 state() 就还是 "stopping"——面板显示成
        # 「正在停止」，调用方也拿不到退出码（实测约每六次复现一次）。
        # 读线程是 daemon 且此时进程已死、管道马上 EOF，所以给个上限就够，
        # 真有意外也不会把调用方挂在这里。注意必须在锁外 join：_pump 自己
        # 要拿这把锁才能把状态改成 stopped。
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=5.0)

    def kill(self) -> None:
        with self._lock:
            # kill 是「立刻弄死它」，同样是用户的明确意图，不许自动重启
            self._stop_requested = True
            self._cancel_restart_locked()
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    # -- 内部 --------------------------------------------------------------

    # -- 崩溃自动重启 ------------------------------------------------------

    def _cancel_restart_locked(self) -> None:
        """取消待执行的重启。调用方需持有锁。"""
        timer, self._restart_timer = self._restart_timer, None
        self._restart_at = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:      # pragma: no cover - cancel 本身不抛，纯防御
                pass

    def _maybe_restart(self, code: int) -> None:
        """进程退出后决定要不要再拉一次。调用方不持锁（在 _pump 里）。"""
        if not self.plan.auto_restart or self.plan.restart_max <= 0:
            return
        give_up = False
        delay = 0.0
        attempt = 0
        with self._lock:
            if self._stop_requested:
                self._restart_count = 0
                return
            if code == 0:
                # 退出码 0 = 正常关服（控制台里敲了 stop、或者插件把服务器关了）。
                # 这种情况重启等于跟用户对着干。
                self._restart_count = 0
                return
            lived = time.time() - (self._started_at or time.time())
            if lived >= _RESTART_HEALTHY_SECONDS:
                self._restart_count = 0
            if self._restart_count >= self.plan.restart_max:
                # 连续崩到上限：停下来，等下一次人工启动重新计预算
                give_up = True
                self._restart_count = 0
            else:
                self._restart_count += 1
                attempt = self._restart_count
                delay = min(_RESTART_MAX_DELAY,
                            _RESTART_BASE_DELAY * (2 ** (attempt - 1)))
                self._restart_at = time.time() + delay
        if give_up:
            self._note("web.console.restart_give_up", count=self.plan.restart_max)
            self._emit_state()
            return
        self._note("web.console.restart_scheduled", seconds=int(round(delay)),
                   attempt=attempt, max=self.plan.restart_max)
        self._emit_state()
        timer = threading.Timer(delay, self._restart_fire)
        timer.daemon = True
        with self._lock:
            self._restart_timer = timer
        timer.start()

    def _restart_fire(self) -> None:
        with self._lock:
            self._restart_timer = None
            self._restart_at = None
            if self._stop_requested:
                return
        self._note("web.console.restarting", attempt=self._restart_count)
        try:
            self.start()
        except CoreError:
            # 起不来通常和刚才崩掉是同一个原因（jar 不匹配、Java 路径没了）。
            # 这里不再排下一次：退避已经走完一轮，而原因就写在控制台上，
            # 继续重试只会把同一行错误刷满屏。
            with self._lock:
                self._restart_count = 0
            self._note("web.console.restart_failed")
            self._emit_state()

    def _pump(self, proc: subprocess.Popen) -> None:
        try:
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
            # 状态先摆正、再决定要不要重启：面板要先看到「它退了、退出码是多少」，
            # 然后才看到「5 秒后重启」。顺序反过来的话，前端会先收到重启通知，
            # 再去拿状态，读到的还是 running。
            self._maybe_restart(code)
        finally:
            self._close_pipes(proc)
            self._close_log()

    @staticmethod
    def _close_pipes(proc: subprocess.Popen) -> None:
        """进程结束后把管道关掉。

        self._proc 会一直持有这个 Popen 对象（面板是长期驻留的，运行时对象
        不会被回收），所以不显式关闭的话，每启动一次就攒下一对没释放的管道
        句柄——反复启停实例会一直涨。
        """
        for stream in (proc.stdout, proc.stdin):
            if stream is None:
                continue
            try:
                stream.close()
            except (OSError, ValueError):
                pass

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
        self._restored = False

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
        # 记住「这台是我拉起来的」：软件重启后据此再拉一遍。写在锁外，
        # 发盘失败不该把一次成功的启动变成异常
        runstate.remember(self.project_dir, name,
                          (runtime.snapshot() or {}).get("pid"))
        return runtime

    def start_by_name(self, servers_dir: str, name: str) -> ServerRuntime:
        server_cfg, server_dir = self.find(servers_dir, name)
        return self.start(server_cfg, server_dir)

    def restore_last(self, servers_dir: str) -> list[str]:
        """把上次退出时还在跑的实例重新拉起来。返回真正拉起来的实例名。

        每个进程只干一次：面板可能既是前台起的、又被 start.py 顺手拉了一把，
        两个入口都调到这里的话，不拦一下就会重复启动。

        跳过两种：已经在自己名下的（比如同时勾了「随软件启动」），和 pid 还
        活着的——后者说明上一轮的子进程没被带走，我们没有它的 stdin，再拉一遍
        只会撞端口。
        """
        if self._restored:
            return []
        self._restored = True

        started: list[str] = []
        for name, info in runstate.load(self.project_dir).items():
            current = self._runtimes.get(name)
            if current is not None and current.state() != STATE_STOPPED:
                continue
            if runstate.pid_alive((info or {}).get("pid")):
                runstate.forget(self.project_dir, name)
                continue
            try:
                self.start_by_name(servers_dir, name)
                started.append(name)
            except Exception:  # noqa: BLE001
                # 实例被删了、jar 没了、Java 环境变了……任何一种都不该让
                # 剩下的实例跟着不启动，也不该把死记录一直留在那里
                runstate.forget(self.project_dir, name)
        return started

    def get(self, name: str) -> Optional[ServerRuntime]:
        return self._runtimes.get(name)

    def require(self, name: str) -> ServerRuntime:
        runtime = self._runtimes.get(name)
        if runtime is None:
            raise CoreError("web.error.not_running", name=name)
        return runtime

    def stop(self, name: str) -> None:
        self.require(name).stop()
        # 用户主动停的，不该在下次启动时被拉回来
        runstate.forget(self.project_dir, name)

    def send(self, name: str, command: str) -> None:
        self.require(name).send(command)

    def state_of(self, name: str) -> Optional[dict]:
        runtime = self._runtimes.get(name)
        return runtime.snapshot() if runtime is not None else None

    def running(self) -> list[str]:
        """当前还没停下来的实例名。"""
        return [name for name, rt in list(self._runtimes.items())
                if rt.state() != STATE_STOPPED]

    def stop_all(self, timeout: float = 20.0) -> None:
        """把所有还在跑的实例停掉。进程退出时收尾用。

        故意不走 self.stop()：那会把实例从恢复记录里抹掉，而软件退出前还在
        跑的实例，正是下次启动该拉回来的那批。
        """
        for name in self.running():
            try:
                runtime = self._runtimes.get(name)
                if runtime is not None:
                    runtime.stop(timeout)
            except CoreError:
                pass
            except Exception:  # noqa: BLE001
                pass

    def histories(self) -> dict[str, list[Event]]:
        return {name: rt.history() for name, rt in self._runtimes.items()}


__all__ = [
    "LaunchPlan",
    "build_launch_plan",
    "build_generic_plan",
    "accept_eula",
    "eula_accepted",
    "ServerRuntime",
    "RuntimeManager",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "STATE_STOPPING",
]
