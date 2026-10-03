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
import subprocess
import time
from typing import Any, Optional

from constants import USER_AGENT
from frp import frpc_files
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
    实现在 frpc_files——两套隧道共用一份，免得架构表各写各的（原先这份不认
    armv7，ARM 设备上会去下 amd64 的包）。
    """
    return frpc_files.os_arch()


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
    base = frpc_files.default_binary_name()
    return os.path.join(_frp_dir(project_dir), base)


def get_frpc_toml_path(project_dir: str) -> str:
    """返回 frpc.toml 配置文件路径。"""
    return os.path.join(_frp_dir(project_dir), "frpc.toml")


# ---------------------------------------------------------------------------
# 下载与解压
# ---------------------------------------------------------------------------

def _download_file(url: str, target_path: str, on_progress=None) -> bool:
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
        headers={"User-Agent": USER_AGENT}, on_progress=on_progress)


def _make_executable(path: str) -> None:
    """兼容旧调用点；实现在 frpc_files（与 OpenFrp 共用一份）。"""
    frpc_files.make_executable(path)


def _extract_frpc(archive_path: str, project_dir: str, kind: str = "") -> bool:
    """从 zip/tar.gz 中提取 frpc 二进制到 frp 目录。

    frp 官方包内包含 frpc.exe / frps.exe / frpc.toml 等文件，
    我们只需要 frpc 二进制。找不到则返回 False。
    """
    return bool(frpc_files.extract_binary(archive_path, _frp_dir(project_dir),
                                          kind=kind))


# ---------------------------------------------------------------------------
# 手动安装：下载失败时让用户自己去 GitHub 拿
# ---------------------------------------------------------------------------

def releases_url(version: str = "") -> str:
    """原版 frp 的 GitHub 发布页。给了版本就指到那一个 tag。"""
    tag = ""
    if version:
        tag = f"/tag/v{str(version).lstrip('v')}"
    return f"https://github.com/{FRP_REPO}/releases{tag}"


def expected_asset_name(version: str = "") -> str:
    """用户该下哪个文件，例如 frp_0.61.0_windows_amd64.zip。

    不知道版本时用 <版本> 占位——手动下载的前提往往就是网络有问题，
    这时候再去问 GitHub 要版本号只会又卡一次。
    """
    return frpc_files.expected_asset_name("frp", version)


def _archive_kind(path: str) -> str:
    """兼容旧调用点；实现在 frpc_files（与 OpenFrp 共用一份）。"""
    return frpc_files.archive_kind(path)


def _looks_like_executable(path: str) -> bool:
    """兼容旧调用点；实现在 frpc_files（与 OpenFrp 共用一份）。"""
    return frpc_files.looks_like_executable(path)


def install_frpc_from_file(src_path: str, project_dir: str) -> Optional[str]:
    """把用户手动下载的文件装成 frpc，返回可执行文件路径。

    两种东西都认：
      - frp 官方发布的压缩包（zip / tar.gz）：解出里面的 frpc 二进制
      - 直接的 frpc 二进制：原样复制过去

    类型按内容判断，不看文件名——浏览器存下来的名字常常被改过。
    失败返回 None。识别与校验都是 frpc_files 那套（与 OpenFrp 共用）。
    """
    try:
        os.makedirs(_frp_dir(project_dir), exist_ok=True)
    except OSError:
        return None
    installed = frpc_files.install_from_file(src_path, _frp_dir(project_dir))
    return installed or None


def download_frpc(project_dir: str, version: str = "",
                  quiet: bool = False, on_progress=None) -> Optional[str]:
    """下载并解压原版 frpc。

    Args:
        project_dir: 项目目录
        version: 指定版本；为空时自动获取最新版
        quiet: 不往 stdout 说话，供网页面板这类没有终端的调用方使用
        on_progress: 进度回调，透传给下载函数

    Returns:
        解压后的 frpc 二进制路径；失败返回 None
    """
    def say(message: str = "") -> None:
        if not quiet:
            print(message)

    if not version:
        say("  [frp] 正在获取最新版本...")
        version = get_latest_frp_version()
        if not version:
            say("  [frp] 获取版本失败（网络问题?）")
            return None

    urls = get_frpc_download_urls(version)
    if not urls:
        return None

    archive_path = get_frpc_archive_path(project_dir)
    os.makedirs(_frp_dir(project_dir), exist_ok=True)

    # 逐个尝试镜像源，一个失败就清理残片换下一个
    for i, url in enumerate(urls, 1):
        say()
        say(f"  [frp] 正在下载 frpc v{version} ...")
        say(f"  [frp] 源 {i}/{len(urls)}: {url}")
        if _download_file(url, archive_path, on_progress=on_progress):
            break
        say("  [frp] 该源下载失败，尝试下一个...")
        try:
            os.remove(archive_path)
        except OSError:
            pass
    else:
        say("  [frp] 所有源均下载失败")
        return None

    say("  [frp] 正在解压...")
    if not frpc_files.extract_binary(archive_path, _frp_dir(project_dir)):
        say("  [frp] 解压失败")
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
    say(f"  [frp] frpc 就绪: {bin_path}")
    return bin_path


# ---------------------------------------------------------------------------
# frpc.toml 极简 TOML 解析/生成
# ---------------------------------------------------------------------------

def _parse_toml_scalar(text: str) -> Any:
    """解析一个 TOML 值（只覆盖本项目用到的子集：字符串/整数/浮点/布尔/数组）。"""
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_parse_toml_scalar(p) for p in _split_toml_array(inner)] if inner else []
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        body = text[1:-1]
        return (body.replace("\\\\", "\\").replace('\\"', '"')
                    .replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t"))
    if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    if text.lower() == "true":
        return True
    if text.lower() == "false":
        return False
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _split_toml_array(inner: str) -> list[str]:
    """按逗号切分数组内容，引号内的逗号不算分隔符。"""
    parts: list[str] = []
    buf: list[str] = []
    quote = ""
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch == ",":
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _assign_toml(target: dict, key: str, value: Any) -> None:
    """写入键值，支持 auth.token 这样的点号键。"""
    if "." in key:
        head, _, tail = key.partition(".")
        _assign_toml(target.setdefault(head.strip(), {}), tail, value)
    else:
        target[key] = value


def _parse_toml(text: str) -> dict[str, Any]:
    """解析 frpc.toml 子集。

    支持:
      - 顶层键值: key = value
      - 带点号键: auth.token = "xxx"  →  {"auth": {"token": "xxx"}}
      - 表头:     [log]              →  {"log": {...}}
      - 数组表:   [[proxies]]          →  {"proxies": [{...}, ...]}
      - 子表:     [proxies.transport]  →  挂到最后一个代理上

    值类型: 字符串 / 整数 / 浮点 / 布尔 / 数组。
    """
    cfg: dict[str, Any] = {}
    proxies: list[dict] = []
    current_proxy: Optional[dict] = None
    target: dict = cfg  # 当前键值该写进哪个表

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[[") and line.endswith("]]"):
            # 数组表：每遇到一次就新开一个元素
            name = line[2:-2].strip()
            current_proxy = {}
            if name == "proxies":
                proxies.append(current_proxy)
            else:
                cfg.setdefault(name, []).append(current_proxy)
            target = current_proxy
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            if name.startswith("proxies.") and current_proxy is not None:
                # [[proxies]] 之后紧跟的 [proxies.transport] 属于最后一个代理，
                # 不能当成顶层表，否则这些键会被抬到代理外面
                sub = current_proxy
                for part in name.split(".")[1:]:
                    sub = sub.setdefault(part.strip(), {})
                target = sub
                continue
            # 普通表：从这里开始键值归它，不再归上一个 [[proxies]]
            current_proxy = None
            sub = cfg
            for part in name.split("."):
                sub = sub.setdefault(part.strip(), {})
            target = sub
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        _assign_toml(target, key.strip(), _parse_toml_scalar(value))

    if proxies:
        cfg["proxies"] = proxies
    return cfg


def _fmt_toml_value(v: Any) -> str:
    """格式化 TOML 值。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt_toml_value(item) for item in v) + "]"
    return f'"{_escape_toml_string(str(v))}"'


def _escape_toml_string(value: str) -> str:
    """把字符串转义成 TOML 基本字符串的内容部分。

    不转义的话，值里只要出现引号或反斜杠，写出来的就不是合法 TOML。
    特别注意这条路径是「读一次配置原样写回」，用户根本没碰那个字段，
    frpc 却会因为配置解析失败直接退出。
    """
    return (value.replace("\\", "\\\\").replace('"', '\\"')
                 .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))


def _is_array_of_tables(value: Any) -> bool:
    """是不是 [[xxx]] 那种「表的数组」（非空的、元素全是字典的列表）。"""
    return (isinstance(value, (list, tuple)) and len(value) > 0
            and all(isinstance(item, dict) for item in value))


def _write_toml_table(lines: list[str], table: dict, prefix: str = "") -> None:
    """把一个表写出来。

    顺序有讲究：TOML 里子表与数组表必须排在同层标量之后，所以分三批写。

    数组表必须单独处理。以前只特判了 proxies，别的 [[xxx]] 会落到
    _fmt_toml_value 的兜底分支——那个分支对字典是 str() 加引号，于是手写的
    [[visitors]]（stcp/xtcp 要用）会被写成
        visitors = ["{'name': 'v1', ...}"]
    这既不是原来那张表、也不是合法 TOML，frpc 之后连整个配置都读不进去，
    而用户只是在面板里点了下保存。core/frp.py 明确承诺过「其余内容原样保留」。
    """
    scalars: list[tuple[str, Any]] = []
    nested: list[tuple[str, dict]] = []
    arrays: list[tuple[str, Any]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            nested.append((key, value))
        elif _is_array_of_tables(value):
            arrays.append((key, value))
        else:
            scalars.append((key, value))

    for key, value in scalars:
        lines.append(f"{key} = {_fmt_toml_value(value)}")
    for key, sub in nested:
        header = f"{prefix}.{key}" if prefix else key
        lines.append("")
        lines.append(f"[{header}]")
        _write_toml_table(lines, sub, header)
    for key, items in arrays:
        header = f"{prefix}.{key}" if prefix else key
        for item in items:
            lines.append("")
            lines.append(f"[[{header}]]")
            _write_toml_table(lines, item, header)


def _build_toml(cfg: dict[str, Any]) -> str:
    """从配置字典生成 frpc.toml 文本。

    proxies 不再单独特判：它就是一个数组表，和其它 [[xxx]] 走同一条路。
    单独特判的代价是——别的数组表全被写坏。
    """
    lines: list[str] = []
    _write_toml_table(lines, cfg)
    return "\n".join(lines).strip() + "\n"


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

def launch_frpc(project_dir: str, quiet: bool = False) -> Optional[subprocess.Popen]:
    """启动原版 frpc（使用 frpc.toml 配置）。返回进程对象。

    quiet 供网页面板这类没有终端的调用方使用。
    """
    global _frpc_process

    def say(message: str = "") -> None:
        if not quiet:
            print(message)

    # 已经在跑就直接返回，别再起一个：原来的句柄会被覆盖，先前那个 frpc
    # 谁也停不掉，只能一直占着远程端口
    if is_frpc_running():
        say("  [frp] frpc 已在运行，忽略本次启动")
        return _frpc_process

    bin_path = get_frpc_bin_path(project_dir)
    toml_path = get_frpc_toml_path(project_dir)

    if not os.path.isfile(bin_path):
        say("  [frp] 未找到 frpc，正在下载...")
        new_bin = download_frpc(project_dir, quiet=quiet)
        if not new_bin or not os.path.isfile(new_bin):
            say("  [frp] frpc 获取失败")
            return None

    if not os.path.isfile(toml_path):
        say("  [frp] 未找到 frpc.toml 配置")
        return None

    cmd = [bin_path, "-c", toml_path]
    say("  [frp] 启动 frpc ...")
    say(f"  [frp] {' '.join(cmd)}")
    say()

    try:
        proc = subprocess.Popen(cmd, stdout=None, stderr=None)
    except OSError as e:
        say(f"  [frp] 启动失败: {e}")
        return None

    time.sleep(2)
    # 配置有错时 frpc 会立刻退出，这时返回进程对象等于告诉调用方「启动成功」，
    # 而菜单里看状态仍是「未运行」
    if proc.poll() is not None:
        say(f"  [frp] frpc 启动后立即退出（退出码 {proc.returncode}），请检查 frpc.toml")
        return None

    _frpc_process = proc
    return proc


def stop_frpc(quiet: bool = False) -> None:
    """停止正在运行的 frpc 进程。"""
    global _frpc_process
    if _frpc_process and _frpc_process.poll() is None:
        if not quiet:
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
