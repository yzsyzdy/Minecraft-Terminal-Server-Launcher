"""Windows 桌面截帧与窗口操作（给「桌面控制」用）。

只用 ctypes：项目不引第三方依赖，而 Windows 上做这件事本身就只需要
user32 / gdi32 / dwmapi 几个调用。

三条路，按可靠程度排序：

  * `PrintWindow` + `PW_RENDERFULLCONTENT`：抓单个窗口，即使被别的窗口挡住
    也能拿到内容，Chrome / Edge / Electron 这类窗口也能正确出图。
  * `BitBlt` 从屏幕 DC 拷：`PrintWindow` 失败（某些 GPU 合成窗口）时兜底，
    但它抓的是「屏幕上真实显示的东西」，窗口被挡住就会抓到挡住它的窗口。
  * 全屏：和 BitBlt 一样，只是区域取整个虚拟屏幕。

只支持 Windows：其它平台直接返回 None，让上层显示「当前系统不支持」，
而不是假装截了一张黑图。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Optional

IS_WINDOWS = sys.platform == "win32"

# -- Win32 常量 -------------------------------------------------------------

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0
DWMWA_EXTENDED_FRAME_BOUNDS = 9
SW_RESTORE = 9
GA_ROOT = 2

_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_WS_VISIBLE = 0x10000000
_DWMWA_CLOAKED = 14


if IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _gdi32 = ctypes.windll.gdi32
    try:
        _dwmapi = ctypes.windll.dwmapi
    except OSError:                     # Server Core 之类没有 dwmapi
        _dwmapi = None
else:
    _user32 = _gdi32 = _dwmapi = None


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER),
                ("bmiColors", wintypes.DWORD * 3)]


# -- 窗口枚举 ---------------------------------------------------------------

def _window_title(hwnd) -> str:
    length = _user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    _user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _window_process(hwnd) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    # 进程名用 tasklist 太慢；查 ExeFile 的名字又要 OpenProcess。
    # 这里只返回 PID，上层需要名字时自己拼，别在这里压性能。
    return str(pid.value)


def _is_cloaked(hwnd) -> bool:
    """UWP / 被挂起的窗口处于「cloaked」状态，截出来是全黑。"""
    if _dwmapi is None:
        return False
    value = wintypes.DWORD()
    result = _dwmapi.DwmGetWindowAttribute(
        hwnd, _DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return bool(result == 0 and value.value)


def list_windows() -> list[dict]:
    """列出当前可截图的顶层窗口。

    只返回有标题、可见、不是工具窗、没被 cloak 的那些——任务栏、输入法
    候选框之类混进来只会让列表没法用。
    """
    if not IS_WINDOWS:
        return []
    found: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title.strip():
            return True
        style = _user32.GetWindowLongW(hwnd, -20)          # GWL_EXSTYLE
        if style & _WS_EX_TOOLWINDOW and not style & _WS_EX_APPWINDOW:
            return True
        if _is_cloaked(hwnd):
            return True
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 64 or height < 64:
            return True
        found.append({
            "hwnd": int(hwnd),
            "title": title,
            "pid": _window_process(hwnd),
            "width": width,
            "height": height,
        })
        return True

    _user32.EnumWindows(_callback, 0)
    found.sort(key=lambda item: item["title"].lower())
    return found


def find_window(hwnd: int) -> Optional[dict]:
    for window in list_windows():
        if window["hwnd"] == hwnd:
            return window
    return None


def window_rect(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """窗口在屏幕上的 (left, top, width, height)。

    优先用 DWM 的「扩展边框」：普通 GetWindowRect 会把不可见的阴影边距算进去，
    Windows 10/11 上能差出七八个像素，截出来多一圈黑边。
    """
    if not IS_WINDOWS:
        return None
    rect = wintypes.RECT()
    if _dwmapi is not None:
        try:
            if _dwmapi.DwmGetWindowAttribute(
                    hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                    ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width > 0 and height > 0:
                    return (rect.left, rect.top, width, height)
        except OSError:                 # pragma: no cover - 个别系统会抛
            pass
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return (rect.left, rect.top, width, height)


def focus_window(hwnd: int) -> bool:
    """把窗口提到前台。最小化的先还原，否则截出来是 0×0。"""
    if not IS_WINDOWS:
        return False
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    # 前台锁：不模拟一次 Alt 的话，前台窗口是别的进程时 SetForegroundWindow 会失败
    _user32.keybd_event(0x12, 0, 0, 0)          # VK_MENU down
    _user32.keybd_event(0x12, 0, 2, 0)          # VK_MENU up
    return bool(_user32.SetForegroundWindow(hwnd))


# -- 截帧 -------------------------------------------------------------------

def _dib_to_rgba(dib, width: int, height: int) -> bytes:
    """BITMAPINFOHEADER 取回来的是 BGRA、行序自下而上，转成 RGBA 自上而下。

    用整行切片而不是逐像素循环：1080p 一帧是 207 万像素，Python 里逐像素
    跑一遍要将近 2 秒（实测 1948 ms），切片只要 17 ms——实时看画面全靠这一步。
    """
    stride = width * 4
    src = bytes(dib)
    rows = []
    for row in range(height):
        start = (height - 1 - row) * stride
        line = src[start:start + stride]
        swapped = bytearray(stride)
        swapped[0::4] = line[2::4]      # R <- B
        swapped[1::4] = line[1::4]      # G
        swapped[2::4] = line[0::4]      # B <- R
        # BitBlt 的 alpha 通道常常是 0（没有真正的 alpha），直接当不透明处理
        swapped[3::4] = bytes([255]) * width
        rows.append(bytes(swapped))
    return b"".join(rows)


def _capture_dc(source_dc, x: int, y: int, width: int, height: int) -> Optional[bytes]:
    """从某个 DC 拷一块矩形出来，返回 RGBA。"""
    mem_dc = _gdi32.CreateCompatibleDC(source_dc)
    if not mem_dc:
        return None
    bitmap = _gdi32.CreateCompatibleBitmap(source_dc, width, height)
    if not bitmap:
        _gdi32.DeleteDC(mem_dc)
        return None
    old = _gdi32.SelectObject(mem_dc, bitmap)
    try:
        ok = _gdi32.BitBlt(mem_dc, 0, 0, width, height, source_dc, x, y,
                           SRCCOPY | CAPTUREBLT)
        if not ok:
            return None
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = height      # 正数 = 自下而上
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        buffer_size = width * height * 4
        buffer = ctypes.create_string_buffer(buffer_size)
        if not _gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer,
                                ctypes.byref(info), DIB_RGB_COLORS):
            return None
        return _dib_to_rgba(buffer.raw, width, height)
    finally:
        _gdi32.SelectObject(mem_dc, old)
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(mem_dc)


def capture_window(hwnd: int) -> Optional[tuple[int, int, bytes]]:
    """抓一个窗口，返回 (宽, 高, RGBA)。抓不到返回 None。"""
    if not IS_WINDOWS:
        return None
    rect = window_rect(hwnd)
    if not rect:
        return None
    _left, _top, width, height = rect
    if width <= 0 or height <= 0 or width > 10000 or height > 10000:
        return None

    window_dc = _user32.GetWindowDC(hwnd)
    if window_dc:
        mem_dc = _gdi32.CreateCompatibleDC(window_dc)
        bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height) if mem_dc else None
        if mem_dc and bitmap:
            old = _gdi32.SelectObject(mem_dc, bitmap)
            try:
                # PW_RENDERFULLCONTENT：让 DirectComposition 的窗口也吐内容
                ok = _user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
                if ok:
                    info = _BITMAPINFO()
                    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
                    info.bmiHeader.biWidth = width
                    info.bmiHeader.biHeight = height
                    info.bmiHeader.biPlanes = 1
                    info.bmiHeader.biBitCount = 32
                    info.bmiHeader.biCompression = BI_RGB
                    buffer = ctypes.create_string_buffer(width * height * 4)
                    if _gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer,
                                        ctypes.byref(info), DIB_RGB_COLORS):
                        return (width, height,
                                _dib_to_rgba(buffer.raw, width, height))
            finally:
                _gdi32.SelectObject(mem_dc, old)
                _gdi32.DeleteObject(bitmap)
                _gdi32.DeleteDC(mem_dc)
        if window_dc:
            _user32.ReleaseDC(hwnd, window_dc)

    # PrintWindow 失败（有些 GPU 合成的窗口就是不给）：退回屏幕拷贝
    screen_dc = _user32.GetDC(0)
    if not screen_dc:
        return None
    try:
        left, top, _w, _h = rect
        rgba = _capture_dc(screen_dc, left, top, width, height)
    finally:
        _user32.ReleaseDC(0, screen_dc)
    if not rgba:
        return None
    return (width, height, rgba)


def virtual_screen() -> tuple[int, int, int, int]:
    """整个虚拟屏幕（多显示器时是所有屏幕的外接矩形）的 (left, top, w, h)。"""
    if not IS_WINDOWS:
        return (0, 0, 0, 0)
    left = _user32.GetSystemMetrics(76)      # SM_XVIRTUALSCREEN
    top = _user32.GetSystemMetrics(77)
    width = _user32.GetSystemMetrics(78)
    height = _user32.GetSystemMetrics(79)
    return (left, top, width, height)


def capture_screen() -> Optional[tuple[int, int, bytes]]:
    if not IS_WINDOWS:
        return None
    screen_dc = _user32.GetDC(0)
    if not screen_dc:
        return None
    try:
        left, top, width, height = virtual_screen()
        if width <= 0 or height <= 0:
            return None
        rgba = _capture_dc(screen_dc, left, top, width, height)
    finally:
        _user32.ReleaseDC(0, screen_dc)
    if not rgba:
        return None
    return (width, height, rgba)


# -- 输入 -------------------------------------------------------------------

_MOUSEEVENTF = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
_KEYEVENTF_KEYUP = 0x0002


def _to_absolute(x: int, y: int) -> tuple[int, int]:
    """屏幕坐标 → SendInput 需要的 0..65535 归一化坐标。"""
    left, top, width, height = virtual_screen()
    if width <= 0 or height <= 0:
        return (0, 0)
    ax = int(((x - left) * 65535) / width)
    ay = int(((y - top) * 65535) / height)
    return (max(0, min(65535, ax)), max(0, min(65535, ay)))


def click(x: int, y: int, button: str = "left", double: bool = False) -> bool:
    """在屏幕坐标 (x, y) 点一下。坐标是相对整块虚拟屏幕的左上角。"""
    if not IS_WINDOWS:
        return False
    down_flag, up_flag = _MOUSEEVENTF.get(button, _MOUSEEVENTF["left"])
    ax, ay = _to_absolute(x, y)
    _user32.SetCursorPos(x, y)
    rounds = 2 if double else 1
    for _ in range(rounds):
        _user32.mouse_event(down_flag | 0x8000 | 0x4000, ax, ay, 0, 0)
        _user32.mouse_event(up_flag | 0x8000 | 0x4000, ax, ay, 0, 0)
    return True


def move_mouse(x: int, y: int) -> bool:
    if not IS_WINDOWS:
        return False
    return bool(_user32.SetCursorPos(x, y))


_VK = {
    "enter": 0x0D, "esc": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "up": 0x26, "down": 0x28,
    "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
}


def press_key(name: str) -> bool:
    """按一个命名键（enter / esc / tab / 方向键…）。"""
    if not IS_WINDOWS:
        return False
    code = _VK.get((name or "").strip().lower())
    if not code:
        return False
    _user32.keybd_event(code, 0, 0, 0)
    _user32.keybd_event(code, 0, _KEYEVENTF_KEYUP, 0)
    return True


def type_text(text: str) -> bool:
    """用剪贴板 + Ctrl+V 粘贴文本。

    逐字符 SendInput 要处理大小写、Unicode、输入法，中文根本没法用；
    剪贴板 + 粘贴是唯一在 Windows 上稳的办法。
    """
    if not IS_WINDOWS:
        return False
    text = str(text or "")
    if not text:
        return False
    previous = None
    try:
        previous = _clipboard_get()
        _clipboard_set(text)
    except OSError:
        pass
    vk_control, vk_v = 0x11, 0x56
    _user32.keybd_event(vk_control, 0, 0, 0)
    _user32.keybd_event(vk_v, 0, 0, 0)
    _user32.keybd_event(vk_v, 0, _KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(vk_control, 0, _KEYEVENTF_KEYUP, 0)
    if previous is not None:
        try:
            _clipboard_set(previous)
        except OSError:
            pass
    return True


_CF_UNICODETEXT = 13


def _clipboard_get() -> Optional[str]:
    if not _user32.OpenClipboard(0):
        return None
    try:
        handle = _user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return None
        ptr = ctypes.windll.kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            text = ctypes.wstring_at(ptr)
        finally:
            ctypes.windll.kernel32.GlobalUnlock(handle)
        return text
    finally:
        _user32.CloseClipboard()


def _clipboard_set(text: str) -> bool:
    if not _user32.OpenClipboard(0):
        return False
    try:
        _user32.EmptyClipboard()
        size = (len(text) + 1) * 2
        handle = ctypes.windll.kernel32.GlobalAlloc(0x0042, size)  # GMEM_MOVEABLE|GMEM_ZEROINIT
        if not handle:
            return False
        ptr = ctypes.windll.kernel32.GlobalLock(handle)
        if not ptr:
            return False
        try:
            ctypes.memmove(ptr, ctypes.create_unicode_buffer(text), size)
        finally:
            ctypes.windll.kernel32.GlobalUnlock(handle)
        return bool(_user32.SetClipboardData(_CF_UNICODETEXT, handle))
    finally:
        _user32.CloseClipboard()


__all__ = [
    "IS_WINDOWS", "list_windows", "find_window", "window_rect", "focus_window",
    "capture_window", "capture_screen", "virtual_screen",
    "click", "move_mouse", "press_key", "type_text",
]
