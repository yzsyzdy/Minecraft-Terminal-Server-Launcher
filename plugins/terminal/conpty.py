"""ConPTY 封装（Windows 伪控制台）。

放在插件里而不是 core/：这是「需要平台 API」的重活，核心保持零依赖。

为什么不用现成的 `pywinpty`：它是可选依赖，装不上时整个终端功能就没了；
而 ConPTY 本身只有十来个 API 调用，自己写反而少一层依赖和版本兼容问题。
以后要接 pywinpty 也只需改这一个文件。

三个容易踩的点，都在下面注释里标了：

1. CreatePseudoConsole 之后**必须关掉自己持有的读/写端**，否则读线程等不到 EOF
2. CreateProcess 的 `bInheritHandles` 必须是 True，PC 句柄靠继承传给子进程
3. STARTUPINFO 必须带 `STARTF_USESTDHANDLES`，否则子进程会沿用父进程的控制台，
   输出根本不会进管道（表现为「一个字节都读不到」）

Win10 1809+ / Win11 都支持。
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import threading
from typing import Optional

IS_WINDOWS = sys.platform == "win32"

# `ctypes.wintypes` 在非 Windows 上**不能导入**：它的 VARIANT_BOOL 用了
# `_type_ = "v"`，别的平台的 ctypes 不认识这个类型，import 直接抛 ValueError。
# 所以连它下面的结构体定义一起挡在守卫里（v1.1.42 就在这儿绊过一回）。
if IS_WINDOWS:                      # pragma: no branch - 平台分支
    from ctypes import wintypes

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES = 0x00000100
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
HANDLE_FLAG_INHERIT = 0x00000001
CREATE_UNICODE_ENVIRONMENT = 0x00000400

if IS_WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:
    _k32 = None


if IS_WINDOWS:
    class COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]


    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW),
                    ("lpAttributeList", ctypes.c_void_p)]


    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD),
                    ("lpSecurityDescriptor", ctypes.c_void_p),
                    ("bInheritHandle", wintypes.BOOL)]



def conpty_available() -> bool:
    """本机能不能用 ConPTY。"""
    if not IS_WINDOWS:
        return False
    try:
        return hasattr(_k32, "CreatePseudoConsole")
    except AttributeError:
        return False


def shell_choices() -> list[dict]:
    """这台机器上可选的 shell。

    pwsh（PowerShell 7）优先——它在 Windows Terminal 里体验最好，也支持真
    UTF-8；没有就退到 Windows PowerShell 5.1。cmd 永远都在。
    """
    found = []
    for exe, label in (("pwsh.exe", "PowerShell 7"),
                       ("powershell.exe", "Windows PowerShell"),
                       ("cmd.exe", "命令提示符")):
        path = shutil.which(exe)
        if path:
            found.append({"id": exe.replace(".exe", ""), "label": label,
                          "path": path})
    return found


def _pipe() -> tuple[int, int]:
    sa = SECURITY_ATTRIBUTES()
    sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    sa.bInheritHandle = True
    read, write = wintypes.HANDLE(), wintypes.HANDLE()
    if not _k32.CreatePipe(ctypes.byref(read), ctypes.byref(write),
                           ctypes.byref(sa), 0):
        raise OSError("CreatePipe 失败")
    return read.value, write.value


def _env_block() -> ctypes.c_void_p:
    """把当前环境变量打包成 CreateProcess 要的 UTF-16 块。"""
    parts = []
    for key, value in os.environ.items():
        # 环境块里不能有 '=' 开头的键（那是驱动器映射的特殊格式）
        if key.startswith("="):
            continue
        parts.append(f"{key}={value}")
    raw = b"\x00".join(part.encode("utf-16-le") for part in parts) + b"\x00\x00"
    buf = ctypes.create_string_buffer(raw)
    return ctypes.cast(buf, ctypes.c_void_p), buf


class ConPtySession:
    """一个挂着伪控制台的子进程。

    用法：
        s = ConPtySession("pwsh.exe", cols, rows)
        s.start()
        s.write(b"ls\\r")
        ... 读 s.read() ...
        s.resize(120, 40)
        s.close()
    """

    def __init__(self, shell: str = "cmd", cols: int = 120,
                 rows: int = 30, cwd: str = "") -> None:
        if not conpty_available():
            raise OSError("本机不支持 ConPTY（需要 Windows 10 1809 及以上）")
        self.shell = shell
        self.cols = max(20, min(400, int(cols)))
        self.rows = max(5, min(200, int(rows)))
        self.cwd = cwd or os.getcwd()
        # 句柄必须是 HANDLE 类型的 ctypes 值，不能存裸 int：
        # 64 位系统上句柄可能超过 2^31，传给未声明 argtypes 的 API 会抛
        # OverflowError（"int too long to convert"）
        self._hpc: Optional[wintypes.HANDLE] = None
        self._in_write: Optional[wintypes.HANDLE] = None
        self._out_read: Optional[wintypes.HANDLE] = None
        self._proc = PROCESS_INFORMATION()
        self._closed = False
        self._lock = threading.Lock()

    # -- 生命周期 ----------------------------------------------------------

    def start(self) -> None:
        in_read, in_write = _pipe()
        out_read, out_write = _pipe()

        _k32.CreatePseudoConsole.argtypes = [
            COORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE)]
        _k32.CreatePseudoConsole.restype = ctypes.c_long
        _k32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
        _k32.ClosePseudoConsole.restype = None
        _k32.SetHandleInformation.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        _k32.SetHandleInformation.restype = wintypes.BOOL
        hpc = wintypes.HANDLE()
        hr = _k32.CreatePseudoConsole(
            COORD(self.cols, self.rows), in_read, out_write, 0,
            ctypes.byref(hpc))
        if hr != 0:
            raise OSError(f"CreatePseudoConsole 失败: 0x{hr & 0xFFFFFFFF:08x}")

        # ★ 第 1 点：PC 已经持有这两端，必须关掉自己的副本
        _k32.CloseHandle(in_read)
        _k32.CloseHandle(out_write)
        self._hpc = hpc
        self._in_write = wintypes.HANDLE(in_write)
        self._out_read = wintypes.HANDLE(out_read)

        # 让 hpc 可继承（有些系统上默认不可继承）
        _k32.SetHandleInformation(hpc, HANDLE_FLAG_INHERIT,
                                  HANDLE_FLAG_INHERIT)

        _k32.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t)]
        _k32.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]

        need = ctypes.c_size_t()
        _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(need))
        attr_buf = ctypes.create_string_buffer(need.value)
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        _k32.InitializeProcThreadAttributeList(ctypes.byref(attr_buf), 1, 0,
                                               ctypes.byref(need))
        _k32.UpdateProcThreadAttribute(
            ctypes.byref(attr_buf), 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            hpc, ctypes.sizeof(wintypes.HANDLE), None, None)
        si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)

        # ★ 第 3 点：不带 STARTF_USESTDHANDLES，子进程会沿用父进程的控制台
        si.StartupInfo.dwFlags |= STARTF_USESTDHANDLES

        # 环境块传 None = 子进程继承本进程的环境。
        # 自己拼环境块踩过坑：PowerShell 对格式极其挑剔，缺一个变量就报
        # "加载托管的 Windows PowerShell 失败，返回错误 8009001d" 然后直接退出；
        # 继承环境则和从 MSTL 的控制台手敲一条命令完全等价。
        env_ptr, env_buf = None, None
        argv = _shell_argv(self.shell)
        # argv 也必须有明确的缓冲区生命周期：直接传 str 给 LPWSTR 参数时，
        # ctypes 会临时造一个缓冲，在调用返回前就可能被回收 —— 偶发报 87
        argv_buf = ctypes.create_unicode_buffer(argv)
        pi = PROCESS_INFORMATION()
        # ★ 第 2 点：bInheritHandles=True，PC 句柄靠继承传给子进程
        _k32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
            wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
        _k32.CreateProcessW.restype = wintypes.BOOL
        ok = _k32.CreateProcessW(
            None, argv_buf, None, None, True,
            EXTENDED_STARTUPINFO_PRESENT,
            None, self.cwd,
            ctypes.byref(si.StartupInfo), ctypes.byref(pi))
        # env_buf / attr_buf 必须活到 CreateProcess 返回之后再释放：
        # 提前回收会让内核读到已释放的内存，表现为偶发的 87（ERROR_INVALID_PARAMETER）
        del argv_buf
        if not ok:
            raise OSError(f"CreateProcess 失败: {ctypes.get_last_error()}")
        self._proc = pi

    @property
    def pid(self) -> int:
        return int(self._proc.dwProcessId or 0)

    @property
    def alive(self) -> bool:
        if self._closed or not self._proc.hProcess:
            return False
        code = wintypes.DWORD()
        ok = _k32.GetExitCodeProcess(self._proc.hProcess, ctypes.byref(code))
        # STILL_ACTIVE = 259
        return bool(ok and code.value == 259)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for handle in (self._in_write, self._out_read):
                try:
                    if handle is not None:
                        _k32.CloseHandle(handle)
                except (OSError, ctypes.ArgumentError):
                    pass
            try:
                if self._proc.hProcess:
                    _k32.TerminateProcess(self._proc.hProcess, 1)
            except OSError:
                pass
            try:
                if self._hpc is not None:
                    _k32.ClosePseudoConsole(self._hpc)
            except (OSError, ctypes.ArgumentError):
                pass
            for handle in (self._proc.hProcess, self._proc.hThread):
                try:
                    if handle:
                        _k32.CloseHandle(handle)
                except OSError:
                    pass
            self._hpc = self._in_write = self._out_read = None
            self._proc = PROCESS_INFORMATION()

    # -- 输入输出 ----------------------------------------------------------

    def write(self, data: bytes) -> bool:
        if self._closed or self._in_write is None or not data:
            return False
        written = wintypes.DWORD()
        try:
            ok = _k32.WriteFile(self._in_write, data, len(data),
                                ctypes.byref(written), None)
        except OSError:
            return False
        return bool(ok) and written.value > 0

    def read(self, size: int = 8192) -> bytes:
        """读一块输出；没有数据立刻返回空字节。

        **必须先 PeekNamedPipe 再 ReadFile**：ReadFile 是阻塞调用，管道里
        没数据时它会一直挂着（实测直接把读线程卡死，连 close 都没法执行）。
        Peek 只探「有多少字节可读」，有才真正去读。
        """
        if self._closed or self._out_read is None:
            return b""
        _k32.PeekNamedPipe.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD)]
        _k32.PeekNamedPipe.restype = wintypes.BOOL
        avail = wintypes.DWORD()
        try:
            ok = _k32.PeekNamedPipe(self._out_read, None, 0, None,
                                    ctypes.byref(avail), None)
        except OSError:
            return b""
        if not ok or avail.value == 0:
            return b""
        size = min(int(size), int(avail.value))
        buf = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        try:
            ok = _k32.ReadFile(self._out_read, buf, size,
                               ctypes.byref(read), None)
        except OSError:
            return b""
        if not ok or read.value == 0:
            return b""
        return buf.raw[:read.value]

    def resize(self, cols: int, rows: int) -> bool:
        """改窗口尺寸，TUI 程序（vim、htop）靠它重排。"""
        cols = max(20, min(400, int(cols)))
        rows = max(5, min(200, int(rows)))
        if self._closed or self._hpc is None:
            return False
        if cols == self.cols and rows == self.rows:
            return True
        _k32.ResizePseudoConsole.argtypes = [wintypes.HANDLE, COORD]
        _k32.ResizePseudoConsole.restype = ctypes.c_long
        hr = _k32.ResizePseudoConsole(self._hpc, COORD(cols, rows))
        if hr != 0:
            return False
        self.cols, self.rows = cols, rows
        return True


def _shell_argv(shell: str) -> str:
    """把 shell 标识变成实际的命令行。"""
    shell = (shell or "cmd").strip().lower()
    if shell in ("pwsh", "pwsh.exe", "powershell7"):
        path = shutil.which("pwsh.exe")
        return f'"{path}" -NoLogo' if path else "pwsh.exe -NoLogo"
    if shell in ("powershell", "powershell.exe", "ps"):
        return "powershell.exe -NoLogo"
    path = shutil.which("cmd.exe") or "cmd.exe"
    # /K 让窗口在命令跑完后仍可继续交互
    return f'"{path}" /K'


__all__ = ["ConPtySession", "conpty_available", "shell_choices"]
