"""frpc 二进制文件的共用处理。

原版 frp 与 OpenFrp 两套隧道各自维护了一份同样的逻辑，而且已经跑偏：

  - 架构映射不一致：OpenFrp 那份认 armv7/armv7l/arm，原版那份不认，
    于是 ARM 设备上原版 frp 会去下 amd64 的包；
  - 只有原版那份会校验「解出来的东西像不像本平台的可执行文件」，OpenFrp
    那份会把用户传错的 txt 也当成 frpc 装好，面板显示「已就绪」，
    真正启动时才发现跑不起来；
  - 解压策略也不同：原版只挑出 frpc 那一个文件，OpenFrp 是「找不到就整包
    解压再递归搜索」。

同一个问题要修两遍、还容易只修一边，所以把「识别平台 / 判断压缩包 /
安全提取 / 校验可执行」这几件事统一到这里。

下载源、版本查询与进程启停仍然各管各的：两边的发布仓库（fatedier/frp 与
OpenFrp/frpc-release）和启动参数（配置文件 vs `-u/-p`）本来就不同。
"""

from __future__ import annotations

import os
import platform
import shutil
import tarfile
import zipfile
from typing import Optional

# 平台名 -> 发布包里的名字
_OS_MAP = {
    "windows": "windows",
    "linux": "linux",
    "darwin": "darwin",
}

# 机器架构 -> 发布包里的名字。armv7 这类必须认出来，否则会去下 amd64 的包。
_ARCH_MAP = {
    "amd64": "amd64", "x86_64": "amd64", "x64": "amd64",
    "arm64": "arm64", "aarch64": "arm64",
    "i386": "386", "i686": "386", "x86": "386",
    "armv7": "arm", "armv7l": "arm", "arm": "arm",
}


def os_arch() -> tuple[str, str, str]:
    """检测当前系统与架构，返回 (os_name, arch, ext)。

    ext 是发布包的压缩后缀：Windows 是 .zip，其它平台是 .tar.gz。
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    os_name = _OS_MAP.get(system, "linux")
    arch = _ARCH_MAP.get(machine, "amd64")
    ext = ".zip" if os_name == "windows" else ".tar.gz"
    return os_name, arch, ext


def default_binary_name() -> str:
    """本平台上 frpc 可执行文件叫什么。"""
    return "frpc.exe" if platform.system().lower() == "windows" else "frpc"


def make_executable(path: str) -> None:
    """非 Windows 上补可执行位。

    用 open(..., "wb") 写出来的文件权限是 0644，之后 Popen 直接执行会
    Permission denied——隧道在 Linux/macOS 上下载完就再也起不来。
    """
    if platform.system().lower() == "windows":
        return
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass


def looks_like_executable(path: str) -> bool:
    """粗判一个文件是不是本平台的可执行文件。

    不瞅这一眼的话，用户传错文件（比如传了个 txt、或者拿错平台的包）也会被
    当成 frpc 装好，面板随后显示「已就绪」，要等真正启动时才发现跑不起来。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return False
    system = platform.system().lower()
    if system == "windows":
        return head[:2] == b"MZ"
    if system == "darwin":
        return head[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe",
                            b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",
                            b"\xca\xfe\xba\xbe")
    return head[:4] == b"\x7fELF"


def archive_kind(path: str) -> str:
    """按内容判断是不是压缩包，返回 'zip' / 'tar' / ''（当二进制）。

    不看后缀：用户从浏览器存下来的文件名五花八门，临时文件更是没有后缀。
    'tar' 涵盖所有 tar 家族（.tar / .tar.gz / .tar.bz2 / .tar.xz），
    解压时交给 tarfile 自己嗅探压缩方式。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(4)
    except OSError:
        return ""
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "tar"
    return ""


def is_target_member(name: str, base_name: str) -> bool:
    """压缩包里的这一项是不是我们要的那个二进制。

    目录项一律不算（zip 的目录项以 / 结尾）。
    """
    if name.endswith("/") or name.endswith("\\"):
        return False
    leaf = os.path.basename(name)
    if not leaf:
        return False
    low = leaf.lower()
    if low == base_name.lower():
        return True
    # 近似命名：有些包把它放在子目录里，或文件名带版本后缀
    return low.startswith("frpc") and (low.endswith(".exe") or "." not in low)


def _pick(candidates: list, base_name: str):
    """优先精确同名的那一个，否则取第一个。"""
    exact = [n for n in candidates
             if os.path.basename(n if isinstance(n, str) else n.name).lower()
             == base_name.lower()]
    return (exact or candidates)[0]


def _extract_from_zip(archive_path: str, dest: str, base_name: str) -> bool:
    try:
        with zipfile.ZipFile(archive_path) as zf:
            names = [n for n in zf.namelist()
                     if not n.endswith("/") and is_target_member(n, base_name)]
            if not names:
                return False
            with zf.open(_pick(names, base_name)) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            return True
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return False


def _extract_from_tar(archive_path: str, dest: str, base_name: str) -> bool:
    try:
        # "r:*" 让 tarfile 自己认压缩方式（gz/bz2/xz/未压缩都能处理）
        with tarfile.open(archive_path, "r:*") as tf:
            members = [m for m in tf.getmembers()
                       if m.isfile() and is_target_member(m.name, base_name)]
            if not members:
                return False
            src = tf.extractfile(_pick(members, base_name))
            if src is None:
                return False
            with src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)
            return True
    except (tarfile.TarError, OSError):
        return False


def extract_binary(archive_path: str, dest_dir: str, base_name: str = "",
                   kind: str = "") -> str:
    """从压缩包里挑出 frpc 二进制解到 dest_dir，返回它的路径。

    只挑那一个文件，不做整包解压——解压目录是用户的数据目录，往里倒一整个
    第三方包既没必要也不安全。

    解出来的东西必须像本平台的可执行文件，否则删掉并返回空串。
    kind 为空时按内容嗅探（调用方也可以显式指定）。
    """
    base_name = base_name or default_binary_name()
    if not kind:
        kind = archive_kind(archive_path)
    if not kind:
        kind = "zip" if archive_path.lower().endswith(".zip") else "tar"
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except OSError:
        return ""
    dest = os.path.join(dest_dir, base_name)

    ok = (_extract_from_zip(archive_path, dest, base_name) if kind == "zip"
          else _extract_from_tar(archive_path, dest, base_name))
    if not ok:
        return ""
    make_executable(dest)
    if not looks_like_executable(dest):
        remove_quietly(dest)
        return ""
    return dest


def remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def install_from_file(src_path: str, dest_dir: str,
                      base_name: str = "") -> str:
    """把用户手动下载的文件装成 frpc，返回可执行文件路径；失败返回空串。

    两种东西都认：
      - 发布压缩包（zip / tar.gz）：解出里面的 frpc 二进制
      - 直接的 frpc 二进制：校验过就原样复制过去

    类型按内容判断，不看文件名——浏览器存下来的名字常常被改过。
    """
    src = os.path.abspath(src_path)
    if not os.path.isfile(src):
        return ""
    base_name = base_name or default_binary_name()

    kind = archive_kind(src)
    if kind:
        return extract_binary(src, dest_dir, base_name=base_name, kind=kind)

    # 当成二进制收下，但要像是个可执行文件：内容能不能跑由用户负责，
    # 「这是不是可执行文件」我们还是看一眼
    if not looks_like_executable(src):
        return ""
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except OSError:
        return ""
    dest = os.path.join(dest_dir, base_name)
    try:
        shutil.copy2(src, dest)
    except OSError:
        return ""
    make_executable(dest)
    return dest if os.path.isfile(dest) else ""


def expected_asset_name(prefix: str, version: str = "",
                        placeholder: str = "<版本>") -> str:
    """用户该手动去下哪个文件。

    prefix 是两条隧道各自的前缀（原版 frp 的包名里带版本号，OpenFrp 的不带）。
    version 为空时用占位符——手动下载的前提往往就是网络有问题，这时候再去
    问远端要版本号只会又卡一次。
    """
    os_name, arch, ext = os_arch()
    if version:
        ver = str(version).lstrip("v")
        return f"{prefix}_{ver}_{os_name}_{arch}{ext}"
    return f"{prefix}_{placeholder}_{os_name}_{arch}{ext}"


def find_binary(dir_path: str, base_name: str = "") -> str:
    """在目录里（含子目录）找一个 frpc 二进制；找不到返回空串。"""
    base_name = base_name or default_binary_name()
    for root, _dirs, files in os.walk(dir_path):
        for name in files:
            if is_target_member(name, base_name):
                return os.path.join(root, name)
    return ""


__all__ = [
    "os_arch",
    "default_binary_name",
    "make_executable",
    "looks_like_executable",
    "archive_kind",
    "is_target_member",
    "extract_binary",
    "install_from_file",
    "expected_asset_name",
    "find_binary",
    "remove_quietly",
]
