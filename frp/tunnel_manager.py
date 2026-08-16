"""
原版 frp (fatedier/frp) 隧道管理模块

与 OpenFrp 的区别:
  - OpenFrp: 隧道在云端管理，frpc 用 -u token -p proxy_id 启动
  - 原版 frp: 隧道写在本地 frpc.toml，frpc 用 -c frpc.toml 启动

支持的 frpc.toml 格式 (frp v0.52+ TOML 配置):

    serverAddr = "1.2.3.4"
    serverPort = 7000

    auth.token = "xxxxx"

    [[proxies]]
    name = "mc"
    type = "tcp"
    localIP = "127.0.0.1"
    localPort = 25565
    remotePort = 25566

零依赖实现: 自带极简 TOML 解析/生成器（仅覆盖本项目用到的子集）。
"""

import os
import platform
import re
import subprocess
import time
import zipfile
import tarfile
from typing import Any, Optional

from constants import USER_AGENT
from http_utils import fetch_json

# GitHub 原版 frp 仓库
FRP_REPO = "fatedier/frp"
FRP_API_LATEST = f"https://api.github.com/repos/{FRP_REPO}/releases/latest"
FRP_RELEASE_DL = f"https://github.com/{FRP_REPO}/releases/download"

# 国内镜像（用户自建 Gitee 仓库，优先使用；文件不带版本号）
FRP_GITEE_MIRROR = \
    "https://gitee.com/yzsyzdy/breeze_sever_tg/releases/download/files/frpc_windows_amd64.zip"

# 当前进程句柄（模块级单例）
_frpc_process: Optional[subprocess.Popen] = None


# ---------------------------------------------------------------------------
# 基础工具: 操作系统/架构检测
# ---------------------------------------------------------------------------

def _os_arch() -> tuple[str, str, str]:
    """检测当前系统与架构，返回 (os, arch, ext)。

    frp 发布文件名规则: frp_{version}_{os}_{arch}.zip / .tar.gz
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    os_map = {
        "windows": "windows", "linux": "linux", "darwin": "darwin",
    }
    arch_map = {
        "amd64": "amd64", "x86_64": "amd64", "x64": "amd64",
        "arm64": "arm64", "aarch64": "arm64",
        "i386": "386", "i686": "386", "x86": "386",
    }

    os_name = os_map.get(system, "linux")
    arch = arch_map.get(machine, "amd64")
    ext = ".zip" if os_name == "windows" else ".tar.gz"
    return os_name, arch, ext


def _frp_dir(project_dir: str) -> str:
    """frp 资源存放目录。"""
    return os.path.join(project_dir, "frp")


# ---------------------------------------------------------------------------
# 版本获取与下载 URL
# ---------------------------------------------------------------------------

def get_latest_frp_version() -> str:
    """从 GitHub API 获取原版 frp 最新版本号（如 '0.61.0'）。失败返回 ''。"""
    result = fetch_json(FRP_API_LATEST, timeout=10)
    if not result:
        return ""
    tag = result.get("tag_name", "")
    return tag.lstrip("v")


def get_frpc_download_urls(version: str) -> list[str]:
    """构造原版 frpc 下载 URL 列表（按优先级排序）。

    顺序:
      1. Gitee 国内镜像（用户自建，国内访问快）
      2. GitHub 官方 Releases（版本化的完整包）

    Gitee 镜像文件名不带版本号（frpc_windows_amd64.zip），
    GitHub 为 frp_{version}_{os}_{arch}.zip。
    """
    os_name, arch, ext = _os_arch()
    urls = []

    # Gitee 镜像仅在 windows/amd64 时可用（文件名固定）
    if os_name == "windows" and arch == "amd64":
        urls.append(FRP_GITEE_MIRROR)

    if version:
        file_name = f"frp_{version}_{os_name}_{arch}{ext}"
        urls.append(f"{FRP_RELEASE_DL}/v{version}/{file_name}")

    return urls


def get_frpc_archive_path(project_dir: str) -> str:
    """返回本地预期的 frpc 压缩包路径。"""
    os_name, arch, ext = _os_arch()
    return os.path.join(_frp_dir(project_dir), f"frp_{os_name}_{arch}{ext}")


def get_frpc_bin_path(project_dir: str) -> str:
    """返回解压后的 frpc 二进制路径。"""
    base = "frpc.exe" if platform.system().lower() == "windows" else "frpc"
    return os.path.join(_frp_dir(project_dir), base)


def get_frpc_toml_path(project_dir: str) -> str:
    """返回 frpc.toml 配置文件路径。"""
    return os.path.join(_frp_dir(project_dir), "frpc.toml")


# ---------------------------------------------------------------------------
# 下载与解压
# ---------------------------------------------------------------------------

def _download_file(url: str, target_path: str) -> bool:
    """下载单个文件（带进度条），成功后返回 True。

    校验: 拒绝 text/html 响应（防镜像返回错误页面）。
    复用 download_msl 的进度条实现，保持全项目风格一致。
    """
    from download_msl import download_file_with_progress

    # 先探一下响应头，识别 text/html 错误页（不实际下载）
    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as probe:
            ctype = probe.headers.get("Content-Type", "")
            if "text/html" in ctype:
                return False
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return False

    # 正式下载（带进度条）
    return download_file_with_progress(
        url, target_path, desc="frpc",
        headers={"User-Agent": USER_AGENT})


def _extract_frpc(archive_path: str, project_dir: str) -> bool:
    """从 zip/tar.gz 中提取 frpc 二进制到 frp 目录。

    frp 官方包内包含 frpc.exe / frps.exe / frpc.toml 等文件，
    我们只需要 frpc 二进制。找不到则返回 False。
    """
    target_dir = _frp_dir(project_dir)
    base_name = "frpc.exe" if platform.system().lower() == "windows" else "frpc"

    if archive_path.endswith(".zip"):
        try:
            with zipfile.ZipFile(archive_path) as zf:
                for name in zf.namelist():
                    if os.path.basename(name) == base_name:
                        with zf.open(name) as src, \
                                open(os.path.join(target_dir, base_name), "wb") as dst:
                            dst.write(src.read())
                        return os.path.isfile(os.path.join(target_dir, base_name))
        except (zipfile.BadZipFile, OSError):
            return False
    else:
        try:
            with tarfile.open(archive_path, "r:gz") as tf:
                for member in tf.getmembers():
                    if os.path.basename(member.name) == base_name and member.isfile():
                        src = tf.extractfile(member)
                        if src is None:
                            return False
                        with open(os.path.join(target_dir, base_name), "wb") as dst:
                            dst.write(src.read())
                        return os.path.isfile(os.path.join(target_dir, base_name))
        except (tarfile.TarError, OSError):
            return False
    return False


def download_frpc(project_dir: str, version: str = "") -> Optional[str]:
    """下载并解压原版 frpc。

    Args:
        project_dir: 项目目录
        version: 指定版本；为空时自动获取最新版

    Returns:
        解压后的 frpc 二进制路径；失败返回 None
    """
    if not version:
        print("  [frp] 正在获取最新版本...")
        version = get_latest_frp_version()
        if not version:
            print("  [frp] 获取版本失败（网络问题?）")
            return None

    urls = get_frpc_download_urls(version)
    if not urls:
        return None

    archive_path = get_frpc_archive_path(project_dir)
    os.makedirs(_frp_dir(project_dir), exist_ok=True)

    # 逐个尝试镜像源，一个失败就清理残片换下一个
    for i, url in enumerate(urls, 1):
        print()
        print(f"  [frp] 正在下载 frpc v{version} ...")
        print(f"  [frp] 源 {i}/{len(urls)}: {url}")
        if _download_file(url, archive_path):
            break
        print("  [frp] 该源下载失败，尝试下一个...")
        try:
            os.remove(archive_path)
        except OSError:
            pass
    else:
        print("  [frp] 所有源均下载失败")
        return None

    print("  [frp] 正在解压...")
    if not _extract_frpc(archive_path, project_dir):
        print("  [frp] 解压失败")
        try:
            os.remove(archive_path)
        except OSError:
            pass
        return None

    # 解压成功后清理压缩包
    try:
        os.remove(archive_path)
    except OSError:
        pass

    bin_path = get_frpc_bin_path(project_dir)
    print(f"  [frp] frpc 就绪: {bin_path}")
    return bin_path


# ---------------------------------------------------------------------------
# frpc.toml 极简 TOML 解析/生成
# ---------------------------------------------------------------------------

def _parse_toml(text: str) -> dict[str, Any]:
    """解析 frpc.toml 子集。

    支持:
      - 顶层键值: key = value
      - 带点号键: auth.token = "xxx"  →  {"auth": {"token": "xxx"}}
      - 数组表:   [[proxies]]          →  {"proxies": [{...}, ...]}

    值类型: 字符串 / 整数 / 布尔。
    """
    cfg: dict[str, Any] = {}
    current_proxy: Optional[dict] = None
    proxies: list[dict] = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[[") and line.endswith("]]"):
            if current_proxy is not None:
                proxies.append(current_proxy)
            current_proxy = {}
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # 解析值类型
        if value.startswith('"') and value.endswith('"'):
            parsed: Any = value[1:-1]
        elif value.lower() == "true":
            parsed = True
        elif value.lower() == "false":
            parsed = False
        else:
            try:
                parsed = int(value)
            except ValueError:
                parsed = value

        if current_proxy is not None:
            current_proxy[key] = parsed
        elif "." in key:
            section, _, subkey = key.partition(".")
            cfg.setdefault(section, {})[subkey] = parsed
        else:
            cfg[key] = parsed

    if current_proxy is not None:
        proxies.append(current_proxy)
    if proxies:
        cfg["proxies"] = proxies
    return cfg


def _fmt_toml_value(v: Any) -> str:
    """格式化 TOML 值。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return f'"{v}"'


def _build_toml(cfg: dict[str, Any]) -> str:
    """从配置字典生成 frpc.toml 文本。"""
    lines: list[str] = []

    # 顶层键（排除 proxies 和嵌套 section）
    for key, value in cfg.items():
        if key == "proxies":
            continue
        if isinstance(value, dict):
            for sub, sv in value.items():
                lines.append(f"{key}.{sub} = {_fmt_toml_value(sv)}")
        else:
            lines.append(f"{key} = {_fmt_toml_value(value)}")

    # proxies 数组表
    for proxy in cfg.get("proxies", []):
        lines.append("")
        lines.append("[[proxies]]")
        for key, value in proxy.items():
            lines.append(f"{key} = {_fmt_toml_value(value)}")

    return "\n".join(lines) + "\n"


def load_frp_config(project_dir: str) -> dict[str, Any]:
    """读取 frpc.toml；文件不存在返回默认结构。"""
    path = get_frpc_toml_path(project_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _parse_toml(f.read())
    except (OSError, FileNotFoundError):
        return {}


def save_frp_config(project_dir: str, cfg: dict[str, Any]) -> bool:
    """保存 frpc.toml。成功返回 True。"""
    path = get_frpc_toml_path(project_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(_build_toml(cfg))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 进程管理
# ---------------------------------------------------------------------------

def launch_frpc(project_dir: str) -> Optional[subprocess.Popen]:
    """启动原版 frpc（使用 frpc.toml 配置）。返回进程对象。"""
    global _frpc_process

    bin_path = get_frpc_bin_path(project_dir)
    toml_path = get_frpc_toml_path(project_dir)

    if not os.path.isfile(bin_path):
        print("  [frp] 未找到 frpc，正在下载...")
        new_bin = download_frpc(project_dir)
        if not new_bin or not os.path.isfile(new_bin):
            print("  [frp] frpc 获取失败")
            return None

    if not os.path.isfile(toml_path):
        print("  [frp] 未找到 frpc.toml 配置")
        return None

    cmd = [bin_path, "-c", toml_path]
    print(f"  [frp] 启动 frpc ...")
    print(f"  [frp] {' '.join(cmd)}")
    print()

    try:
        proc = subprocess.Popen(cmd, stdout=None, stderr=None)
        _frpc_process = proc
        time.sleep(2)
        return proc
    except OSError as e:
        print(f"  [frp] 启动失败: {e}")
        return None


def stop_frpc() -> None:
    """停止正在运行的 frpc 进程。"""
    global _frpc_process
    if _frpc_process and _frpc_process.poll() is None:
        print("  [frp] 停止 frpc ...")
        _frpc_process.terminate()
        try:
            _frpc_process.wait(timeout=5)
        except Exception:
            _frpc_process.kill()
    _frpc_process = None


def is_frpc_running() -> bool:
    """frpc 是否在运行。"""
    return _frpc_process is not None and _frpc_process.poll() is None
