"""终端插件：在浏览器里操作一个真正的 cmd / PowerShell。

为什么需要插件：核心承诺零依赖，而「真终端」必须要平台 API（ConPTY）。
放在插件里，核心依然不带任何第三方依赖；不装这个插件，面板只是少一个标签页。

会话模型：
  - 一个进程一份会话池，key 是会话 id；同一时间只保留 config 里允许的个数
  - 每个会话有自己的读线程，把输出攒进环形缓冲 + 递增序号
  - 前端用 `/output?since=N` 拉增量，只拿新的那一段，断线重连不会重复
  - 写输入走 `/input`，尺寸变化走 `/resize`

为什么用轮询而不是 WebSocket：面板的整套东西都走普通 HTTP + JSON，
引入 WS 要动 Server 的结构；终端一次拉几十 KB，2 Hz 轮询完全够用，
而且和现有 token 鉴权天然兼容。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque

# 插件目录在 sys.path 里才能 import 同目录的 conpty
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import conpty  # noqa: E402  （同目录模块，必须在 path 调整之后导入）

# 每个会话的读线程睡多久。50 ms 对交互足够，也不会空转吃 CPU。
_POLL_SECONDS = 0.05
# 环形缓冲保留的最大字节数：够回看一屏多的滚动，再多就只是占内存。
_MAX_BUFFER = 256 * 1024
# 单次最多返回多少字节，避免一帧塞爆网络。
_MAX_CHUNK = 32 * 1024
# 空闲多久自动关掉会话（秒）。浏览器关掉标签页后没人来取输出，留着是浪费。
_IDLE_TIMEOUT = 900


class TerminalSession:
    """一个 ConPTY 会话 + 它的输出缓冲。"""

    def __init__(self, sid: str, shell: str, cols: int, rows: int,
                 cwd: str) -> None:
        self.id = sid
        self.pty = conpty.ConPtySession(shell, cols, rows, cwd)
        self.pty.start()
        self.shell = shell
        self.created_at = time.time()
        self.last_seen = time.time()
        self.seq = 0
        self._buffer = deque()          # [(seq, bytes)]
        self._buffer_bytes = 0
        self._lock = threading.Lock()
        self._stop = False
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    # -- 读线程 ----------------------------------------------------------

    def _pump(self) -> None:
        while not self._stop and self.pty.alive:
            try:
                chunk = self.pty.read()
            except OSError:
                break
            if chunk:
                with self._lock:
                    self.seq += 1
                    self._buffer.append((self.seq, chunk))
                    self._buffer_bytes += len(chunk)
                    while (self._buffer_bytes > _MAX_BUFFER
                           and len(self._buffer) > 1):
                        _seq, old = self._buffer.popleft()
                        self._buffer_bytes -= len(old)
            else:
                time.sleep(_POLL_SECONDS)
        # 进程退出：把退出信息推进去，前端好显示
        with self._lock:
            self.seq += 1
            note = "\r\n\x1b[33m[process exited]\x1b[m\r\n"
            self._buffer.append((self.seq, note.encode("utf-8")))

    # -- 对外 ---------------------------------------------------------

    def pull(self, since: int) -> dict:
        """取 since 之后的新输出。返回 {seq, chunks: [[seq, base64]], alive}。"""
        self.last_seen = time.time()
        import base64
        with self._lock:
            fresh = [(s, c) for s, c in self._buffer if s > since]
            # 一次最多给 _MAX_CHUNK 字节，剩下的下次再取
            picked, total = [], 0
            for s, c in fresh:
                if picked and total + len(c) > _MAX_CHUNK:
                    break
                picked.append((s, c))
                total += len(c)
            data = [[s, base64.b64encode(c).decode("ascii")] for s, c in picked]
            return {
                "seq": picked[-1][0] if picked else since,
                "chunks": data,
                "alive": self.pty.alive,
                "more": len(picked) < len(fresh),
            }

    def write(self, data: bytes) -> bool:
        self.last_seen = time.time()
        return self.pty.write(data)

    def resize(self, cols: int, rows: int) -> bool:
        self.last_seen = time.time()
        return self.pty.resize(cols, rows)

    def close(self) -> None:
        self._stop = True
        try:
            self.pty.close()
        except OSError:
            pass


class SessionPool:
    """会话池。一个面板进程一份。"""

    def __init__(self, project_dir: str) -> None:
        self.project_dir = project_dir
        self.sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def create(self, shell: str, cols: int, rows: int,
               cwd: str) -> TerminalSession:
        with self._lock:
            self._counter += 1
            sid = f"t{self._counter}"
            session = TerminalSession(sid, shell, cols, rows, cwd)
            self.sessions[sid] = session
            # 顺手清掉闲置的
            self._reap_locked()
            return session

    def get(self, sid: str) -> TerminalSession | None:
        with self._lock:
            return self.sessions.get(sid)

    def close(self, sid: str) -> bool:
        with self._lock:
            session = self.sessions.pop(sid, None)
        if session is None:
            return False
        session.close()
        return True

    def _reap_locked(self) -> None:
        now = time.time()
        dead = [sid for sid, s in self.sessions.items()
                if now - s.last_seen > _IDLE_TIMEOUT]
        for sid in dead:
            session = self.sessions.pop(sid)
            session.close()

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
        for session in sessions:
            session.close()


# ---------------------------------------------------------------------------
# HTTP 处理函数：签名 (query, body) -> (status, content_type, bytes)
# ---------------------------------------------------------------------------

_POOL: SessionPool | None = None
_POOL_LOCK = threading.Lock()


def _pool(project_dir: str) -> SessionPool:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = SessionPool(project_dir)
        return _POOL


def _json(payload: dict, status: int = 200) -> tuple[int, str, bytes]:
    return (status, "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _bad(message: str, status: int = 400) -> tuple[int, str, bytes]:
    return _json({"ok": False, "message": message}, status)


def _workdir(project_dir: str, server: str) -> str:
    """终端的工作目录。

    指定了实例就用那个实例目录——用户想在这个终端里跑服务器目录下的脚本时
    省得再 cd。不指定就用项目根目录。
    """
    server = (server or "").strip()
    if not server:
        return project_dir
    path = os.path.join(project_dir, "servers", server)
    return path if os.path.isdir(path) else project_dir


def make_handlers(project_dir: str) -> dict:
    pool = _pool(project_dir)

    def shells(query, body):
        return _json({"ok": True,
                      "supported": conpty.conpty_available(),
                      "shells": conpty.shell_choices(),
                      "sessions": [{"id": s.id, "shell": s.shell}
                                   for s in pool.sessions.values()]})

    def create(query, body):
        body = body or {}
        if not conpty.conpty_available():
            return _bad("当前系统不支持 ConPTY（需要 Windows 10 1809+）", 501)
        shell = str(body.get("shell") or "cmd")
        try:
            cols = int(body.get("cols") or 100)
            rows = int(body.get("rows") or 30)
        except (TypeError, ValueError):
            cols, rows = 100, 30
        cwd = _workdir(project_dir, str(body.get("server") or ""))
        try:
            session = pool.create(shell, cols, rows, cwd)
        except OSError as e:
            return _bad(f"启动终端失败: {e}", 500)
        return _json({"ok": True, "id": session.id, "shell": session.shell,
                      "cwd": cwd})

    def output(query, body):
        sid = str(query.get("id") or "")
        session = pool.get(sid)
        if session is None:
            return _bad("会话不存在", 404)
        try:
            since = int(query.get("since") or 0)
        except (TypeError, ValueError):
            since = 0
        return _json({"ok": True, **session.pull(since)})

    def write(query, body):
        sid = str((body or {}).get("id") or "")
        session = pool.get(sid)
        if session is None:
            return _bad("会话不存在", 404)
        data = str((body or {}).get("data") or "")
        if not session.write(data.encode("utf-8")):
            return _bad("写入失败（会话可能已结束）", 410)
        return _json({"ok": True})

    def resize(query, body):
        sid = str((body or {}).get("id") or "")
        session = pool.get(sid)
        if session is None:
            return _bad("会话不存在", 404)
        try:
            cols = int((body or {}).get("cols") or 0)
            rows = int((body or {}).get("rows") or 0)
        except (TypeError, ValueError):
            return _bad("尺寸不合法")
        return _json({"ok": session.resize(cols, rows)})

    def kill(query, body):
        sid = str((body or {}).get("id") or "")
        return _json({"ok": pool.close(sid)})

    return {
        "shells": shells,
        "create": create,
        "output": output,
        "input": write,
        "resize": resize,
        "kill": kill,
    }


def register(ctx) -> None:
    for path, handler in make_handlers(ctx.project_dir).items():
        ctx.route(path, handler)
