"""
OpenFrp tunnel management module.

OpenFrp (https://www.openfrp.net/) 是一个免费的内网穿透服务。
MSTL 通过它暴露本地 MC 服务器到公网，让外网玩家能直接连接。

架构分层:
  1. API 封装层 — 对 OpenFrp 各种 REST API 的 Python 封装
  2. 二进制管理层 — frpc 的下载、解压、启动、停止
  3. 服务器绑定层 — 在 menu.py 中将隧道配置写入 .server.json

OpenFrp 有两套认证体系:
  - 用户 Token (API 8): 查隧道列表，frpc 启动认证
  - Authorization (API 2/3/4/5/6/7): 创建/编辑/删除隧道
"""

import json
import os
import platform
import subprocess
import shutil
import time
import zipfile
import tarfile
import urllib.request
import urllib.error
from typing import Any, Optional
from dataclasses import dataclass, field

from constants import USER_AGENT, OPENFRP_API, OPENFRP_SOFTWARE
from i18n import t


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class TunnelInfo:
    """一条 OpenFrp 隧道的完整信息。

    注意: API 8 (token) 和 API 3 (Authorization) 返回的字段名不同，
    所以有两个静态工厂方法做转换。
    """
    proxy_id: int       # 隧道 ID，frpc -p 参数
    name: str           # 隧道名称
    proxy_type: str     # 协议类型: tcp / udp / http
    node: str           # 所在节点名称
    local_addr: str     # 本地地址 (通常 127.0.0.1)
    local_port: int     # 本地端口 (MC 默认 25565)
    remote_addr: str    # 远程连接地址 (玩家连这个)
    online: bool = False

    @staticmethod
    def from_token_api(item: dict, node_name: str) -> "TunnelInfo":
        """从 API 8 (token 列表) 响应解析。"""
        remote = item.get("remote", "")
        local = item.get("local", "")
        local_ip, local_port_str = (local.split(":") + [""])[:2]
        return TunnelInfo(
            proxy_id=item.get("id", 0),
            name=item.get("name", ""),
            proxy_type=item.get("type", "tcp"),
            node=node_name,
            local_addr=local_ip,
            local_port=int(local_port_str) if local_port_str else 0,
            remote_addr=remote,
        )

    @staticmethod
    def from_auth_api(item: dict) -> "TunnelInfo":
        """从 API 3 (Authorization 列表) 响应解析。"""
        local = item.get("localIp", "127.0.0.1")
        local_port = item.get("localPort", 25565)
        connect = item.get("connectAddress", "")
        return TunnelInfo(
            proxy_id=item.get("id", 0),
            name=item.get("proxyName", ""),
            proxy_type=item.get("proxyType", "tcp"),
            node=item.get("friendlyNode", "?"),
            local_addr=local,
            local_port=int(local_port) if local_port else 25565,
            remote_addr=connect,
            online=item.get("online", False),
        )


@dataclass
class NodeInfo:
    """OpenFrp 节点信息，用于创建隧道时选节点。"""
    node_id: int
    name: str
    classify: int          # 1=中国大陆 2=港澳台 3=海外
    status: int            # 200=正常
    allow_port: Optional[str] = None
    need_realname: bool = False   # 是否需实名认证才能使用
    fully_loaded: bool = False    # 是否满载
    protocol_support: dict = field(default_factory=lambda: {
        "tcp": True, "udp": True, "http": False, "https": False,
    })


# ---------------------------------------------------------------------------
# HTTP 请求工具
# ---------------------------------------------------------------------------

def _fetch_json(url: str, data: Optional[dict] = None,
                headers: Optional[dict] = None, timeout: int = 15) -> Optional[Any]:
    """通用的 JSON API 请求封装。

    同时支持 GET (data=None) 和 POST (data=dict)。
    所有 OpenFrp API 调用都走这个函数，统一 User-Agent 和超时。
    请求失败一律返回 None，不抛异常。
    """
    req_headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    body = json.dumps(data).encode("utf-8") if data else None
    try:
        req = urllib.request.Request(url, data=body, headers=req_headers,
                                     method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            OSError, json.JSONDecodeError) as e:
        return None


def _os_arch() -> tuple[str, str, str]:
    """检测当前操作系统和架构，返回 (os, arch, file_name)。

    frpc 的发布文件命名规则: frpc_{os}_{arch}.zip / .tar.gz
    兼容 amd64 / arm64 / 386 三种架构。
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    os_map = {
        "windows": "windows", "linux": "linux", "darwin": "darwin",
    }
    arch_map = {
        "amd64": "amd64", "x86_64": "amd64", "x64": "amd64",
        "i386": "386", "i686": "386", "x86": "386",
        "arm64": "arm64", "aarch64": "arm64",
        "armv7": "arm", "armv7l": "arm", "arm": "arm",
    }

    os_name = os_map.get(system, "linux")
    arch = arch_map.get(machine, "amd64")
    ext = ".zip" if os_name == "windows" else ".tar.gz"
    file_name = f"frpc_{os_name}_{arch}{ext}"
    return os_name, arch, file_name


# ---------------------------------------------------------------------------
# Token 认证 API (只需用户 Token，无需 Authorization)
# ---------------------------------------------------------------------------

def get_tunnels_by_token(token: str) -> Optional[list[tuple[str, list[TunnelInfo]]]]:
    """用用户 Token 获取所有隧道列表 (API 8)。

    这是最简单的查看方式——只需用户在控制台输入 token，
    不需要从面板复制复杂的 Authorization 字符串。

    Returns:
        按节点分组的隧道列表: [(node_name, [TunnelInfo, ...]), ...]
        失败返回 None。
    """
    url = f"{OPENFRP_API}/api?action=getallproxies&user={token}"
    result = _fetch_json(url, timeout=10)
    if not result or not result.get("success"):
        return None

    data = result.get("data") or []
    groups: list[tuple[str, list[TunnelInfo]]] = []
    for group in data:
        node_name = group.get("node", "?")
        proxies = group.get("proxies") or []
        tunnels = [TunnelInfo.from_token_api(p, node_name) for p in proxies]
        if tunnels:
            groups.append((node_name, tunnels))
    return groups


# ---------------------------------------------------------------------------
# Authorization 认证 API (需要从面板复制完整字符串)
# 这些 API 涉及隧道 CRUD，需要更高的权限
# ---------------------------------------------------------------------------

def get_node_list(auth_token: str) -> Optional[list[NodeInfo]]:
    """获取所有可用节点列表 (API 6)。

    创建隧道前需要先选节点，节点根据地理位置和负载状态过滤。
    """
    url = f"{OPENFRP_API}/frp/api/getNodeList"
    result = _fetch_json(url, headers={"Authorization": auth_token}, timeout=10)
    if not result or not result.get("flag"):
        return None
    data = result.get("data") or {}
    raw_list = data.get("list") or []
    nodes: list[NodeInfo] = []
    for item in raw_list:
        prot = item.get("protocolSupport") or {}
        nodes.append(NodeInfo(
            node_id=item.get("id", 0),
            name=item.get("name", "?"),
            classify=item.get("classify", 3),
            status=item.get("status", 0),
            allow_port=item.get("allowPort"),
            need_realname=item.get("needRealname", False),
            fully_loaded=item.get("fullyLoaded", False),
            protocol_support=prot,
        ))
    return nodes


def create_tunnel(auth_token: str, params: dict) -> Optional[str]:
    """创建新隧道 (API 4)。返回服务端提示消息。"""
    url = f"{OPENFRP_API}/frp/api/newProxy"
    result = _fetch_json(url, data=params, headers={"Authorization": auth_token})
    if not result:
        return None
    return result.get("msg", "")


def delete_tunnel(auth_token: str, proxy_id: int) -> Optional[str]:
    """删除隧道 (API 5)。"""
    url = f"{OPENFRP_API}/frp/api/removeProxy"
    result = _fetch_json(url, data={"proxy_id": proxy_id},
                         headers={"Authorization": auth_token})
    if not result:
        return None
    return result.get("msg", "")


def edit_tunnel(auth_token: str, params: dict) -> Optional[str]:
    """编辑隧道 (API 7)。"""
    url = f"{OPENFRP_API}/frp/api/editProxy"
    result = _fetch_json(url, data=params, headers={"Authorization": auth_token})
    if not result:
        return None
    return result.get("msg", "")


def get_tunnels_by_auth(auth_token: str) -> Optional[list[TunnelInfo]]:
    """用 Authorization 获取隧道列表 (API 3)。

    比 API 8 多返回 online 在线状态。
    """
    url = f"{OPENFRP_API}/frp/api/getUserProxies"
    result = _fetch_json(url, headers={"Authorization": auth_token}, timeout=10)
    if not result or not result.get("flag"):
        return None
    data = result.get("data") or {}
    raw_list = data.get("list") or []
    return [TunnelInfo.from_auth_api(item) for item in raw_list]


def get_user_info(auth_token: str) -> Optional[dict]:
    """获取用户信息 (API 2，含流量使用情况)。"""
    url = f"{OPENFRP_API}/frp/api/getUserInfo"
    result = _fetch_json(url, headers={"Authorization": auth_token})
    if not result or not result.get("flag"):
        return None
    return result.get("data")


def user_sign(auth_token: str) -> Optional[str]:
    """每日签到 (API 9)。

    注意: 按 OpenFrp 服务条款，自动签到不被允许，
    所以此函数仅用于手动签到，不做定时调用。
    """
    url = f"{OPENFRP_API}/frp/api/userSign"
    result = _fetch_json(url, headers={"Authorization": auth_token})
    if not result:
        return None
    return result.get("msg", "")


# ---------------------------------------------------------------------------
# frpc 二进制下载
# frpc 是 OpenFrp 的客户端程序，负责建立隧道连接。
# 下载流程: API 获取版本 → 构造 URL → 逐一尝试多个 CDN 镜像
# ---------------------------------------------------------------------------

def get_frpc_release_info() -> Optional[dict[str, Any]]:
    """从 OpenFrp API 获取最新 frpc 版本信息和 CDN 源列表。"""
    result = _fetch_json(OPENFRP_SOFTWARE, timeout=10)
    if not result or not result.get("flag"):
        return None
    return result.get("data")


def get_frpc_download_urls() -> Optional[tuple[str, list[str], str]]:
    """生成所有可用的 frpc 下载 URL。

    优先级:
      [0] Gitee 用户镜像 (最可靠，由用户自己维护)
      [1] OpenFrp API 返回的官方 CDN 源
      [2] GitHub Releases (最后兜底)

    Returns:
        (version_full, [url1, url2, ...], file_name)
    """
    data = get_frpc_release_info()
    if not data:
        return None

    latest_ver = data.get("latest_ver", "")
    latest_full = data.get("latest_full", "")
    sources = data.get("source") or []
    if not latest_full:
        return None

    _, _, file_name = _os_arch()
    urls = []

    # 用户维护的 Gitee 镜像，CDN 宕机时的救命稻草
    gitee_url = (
        "https://gitee.com/yzsyzdy/breeze_sever_tg/releases/download/files/"
        f"{file_name}"
    )
    urls.append(gitee_url)

    # OpenFrp 官方 API 返回的 CDN 镜像列表
    for src in sources:
        base = src.get("value", "")
        if base:
            url = f"{base}/{latest_full}/{file_name}"
            urls.append(url)

    # GitHub Releases 兜底（OpenFrp 的 frpc 在 GitHub 有发布）
    tag = latest_full or f"v{latest_ver}"
    github_url = (
        f"https://github.com/OpenFrp/frpc-release/releases/download/"
        f"{tag}/{file_name}"
    )
    urls.append(github_url)

    if not urls:
        return None
    return latest_full, urls, file_name


# OpenFrp 的 CDN 对一些非浏览器 User-Agent 返回 567，
# 所以 frpc 下载用 Chrome 的 UA 而不是 MSTL/1.0
_FRPC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def get_frpc_local_path(project_dir: str) -> str:
    """返回本地预期的 frpc 存档文件路径（不是解压后的 exe 路径）。"""
    _, _, file_name = _os_arch()
    return os.path.join(project_dir, "openfrp", file_name)


def _download_frpc_file(url: str, target_path: str) -> bool:
    """下载单个 frpc 文件（单线程 + 进度条 + 完整性校验）。

    校验点:
      1. Content-Type 排除 HTML（OneDrive 等误返回网页）
      2. zip 魔数 PK\\x03\\x04 确保文件合法
    """
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _FRPC_USER_AGENT})
        with urllib.request.urlopen(req, timeout=300) as resp:
            # 某些 CDN 误返回网页而不是文件（如 1dr.zyghit.cn 返回 OneDrive 页面）
            ct = resp.headers.get("Content-Type", "")
            if "text/html" in ct or "text/plain" in ct:
                print(f"\n  [错误] 服务器返回了 {ct}，不是文件下载链接")
                return False

            total = resp.length or 0
            downloaded = 0
            chunk_size = 8192
            os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
            with open(target_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded / total * 100
                        filled = int(30 * downloaded / total)
                        bar = "\u2588" * filled + "\u2591" * (30 - filled)
                        print(f"    frpc [{bar}] {pct:5.1f}%",
                              end="\r", flush=True)
                    else:
                        print(f"    frpc {downloaded / 1024 / 1024:.1f} MB...",
                              end="\r", flush=True)
        print()

        # zip 文件必须以 PK\x03\x04 开头
        if target_path.endswith(".zip"):
            with open(target_path, "rb") as f:
                magic = f.read(4)
            if magic != b"PK\x03\x04":
                print(f"  [错误] 下载的文件不是有效的 zip 文件")
                return False

        return True
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"\n  {t('app.error', msg=f'Download failed: {e}')}")
        return False


def download_frpc(project_dir: str) -> Optional[str]:
    """下载最新 frpc，依次尝试所有镜像源。

    策略: 逐个尝试，一个失败就清理残片换下一个。
    全部失败才放弃，返回 None。
    """
    info = get_frpc_download_urls()
    if not info:
        print("  [OpenFrp] " + t("app.error", msg="Failed to get frpc download info"))
        return None

    _, urls, file_name = info
    target_dir = os.path.join(project_dir, "openfrp")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, file_name)

    for i, url in enumerate(urls):
        print(f"  [OpenFrp] Downloading frpc {file_name} (mirror {i + 1}/{len(urls)}) ...")
        if _download_frpc_file(url, target_path):
            if os.path.getsize(target_path) > 0:
                return target_path
            try:
                os.remove(target_path)
            except OSError:
                pass
        print(f"  [OpenFrp] Mirror {i + 1} failed, trying next...")

    print("  [OpenFrp] " + t("app.error", msg="All download mirrors failed"))
    return None


# ---------------------------------------------------------------------------
# frpc 进程生命周期管理
# ---------------------------------------------------------------------------

_frpc_process: Optional[subprocess.Popen] = None


def launch_frpc(project_dir: str, server_cfg: dict) -> Optional[subprocess.Popen]:
    """启动 frpc 子进程。

    全流程:
      1. 检查 frpc.exe 是否存在
      2. 如果不存在但 .zip 存在 -> 解压
      3. 如果都不存在 -> 下载 -> 解压
      4. 启动 frpc -u {token} -p {proxy_id}
      5. 等 2 秒让 frpc 建立连接

    frpc 的 stdout/stderr 直接输出到控制台（继承父进程），
    方便用户看到连接状态和错误信息。
    """
    global _frpc_process

    of_cfg = server_cfg.get("openfrp", {}) or {}
    token = of_cfg.get("token", "")
    proxy_id = of_cfg.get("proxy_id", 0)

    if not token or not proxy_id:
        print("  [OpenFrp] " + t("app.error", msg="token or proxy_id not configured"))
        return None

    frpc_dir = os.path.join(project_dir, "openfrp")
    base_name = "frpc.exe" if platform.system().lower() == "windows" else "frpc"
    frpc_bin = os.path.join(frpc_dir, base_name)
    frpc_archive = get_frpc_local_path(project_dir)

    # 尝试获取 frpc 二进制文件: 缓存 → 解压 → 下载
    if not os.path.isfile(frpc_bin):
        if os.path.isfile(frpc_archive):
            print("  [OpenFrp] Extracting frpc...")
            result = _extract_frpc_if_needed(frpc_archive, project_dir)
            if os.path.isfile(result):
                frpc_bin = result
            else:
                print("  [OpenFrp] Archive corrupt, re-downloading...")
                try:
                    os.remove(frpc_archive)
                except OSError:
                    pass

    if not os.path.isfile(frpc_bin):
        print("  [OpenFrp] frpc not found, downloading...")
        new_path = download_frpc(project_dir)
        if new_path and os.path.isfile(new_path):
            result = _extract_frpc_if_needed(new_path, project_dir)
            if os.path.isfile(result):
                frpc_bin = result

    if not os.path.isfile(frpc_bin):
        print("  [OpenFrp] " + t("app.error", msg="failed to obtain frpc"))
        return None

    cmd = [frpc_bin, "-u", token, "-p", str(proxy_id)]
    print(f"  [OpenFrp] Starting frpc for tunnel #{proxy_id} ...")
    print(f"  [OpenFrp] {cmd[0]} -u {token[:6]}... -p {proxy_id}")
    print()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=None,  # 继承父进程控制台，用户能看到 frpc 输出
            stderr=None,
        )
        _frpc_process = proc
        time.sleep(2)  # 等 frpc 建立连接后再启动 MC 服务器
        return proc
    except OSError as e:
        print(f"  [OpenFrp] " + t("app.error", msg=str(e)))
        return None


def stop_frpc() -> None:
    """停止正在运行的 frpc 进程。

    通过全局变量 _frpc_process 追踪当前 frpc 实例。
    先尝试优雅 terminate，5 秒后强制 kill。
    """
    global _frpc_process
    if _frpc_process and _frpc_process.poll() is None:
        print("  [OpenFrp] Stopping frpc...")
        _frpc_process.terminate()
        try:
            _frpc_process.wait(timeout=5)
        except Exception:
            _frpc_process.kill()
        _frpc_process = None


def _extract_frpc_if_needed(archive_path: str, project_dir: str) -> str:
    """从 zip/tar.gz 中解压 frpc 二进制文件。

    解压策略:
      1. 先精确匹配 frpc.exe (Windows) 或 frpc (Unix)
      2. 找不到时全量解压，再递归搜索带 "frpc" 的可执行文件
      3. 最后清理多余文件，只保留 frpc 二进制

    Returns:
        解压后的二进制路径; 失败返回空字符串
    """
    target_dir = os.path.dirname(archive_path)
    base_name = "frpc.exe" if platform.system().lower() == "windows" else "frpc"
    expected = os.path.join(target_dir, base_name)

    def _find_binary(dirpath: str) -> str:
        """在目录树中递归搜索 frpc 可执行文件。"""
        for root, _dirs, files in os.walk(dirpath):
            for f in files:
                if f == base_name or f.lower() == base_name.lower():
                    return os.path.join(root, f)
                if "frpc" in f.lower() and (
                    f.endswith(".exe") or "." not in f
                ):
                    return os.path.join(root, f)
        return ""

    def _cleanup_extras(keep: str) -> None:
        """删除解压目录中除了 keep 和存档以外的所有文件。"""
        for root, dirs, files in os.walk(target_dir):
            for f in files:
                fp = os.path.join(root, f)
                if fp != keep and fp != archive_path:
                    try:
                        os.remove(fp)
                    except OSError:
                        pass
            for d in dirs:
                fp = os.path.join(root, d)
                try:
                    shutil.rmtree(fp)
                except OSError:
                    pass

    try:
        if archive_path.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                for m in zf.infolist():
                    if m.filename.endswith("frpc.exe") or m.filename.endswith("/frpc"):
                        zf.extract(m, target_dir)
                        extracted = os.path.join(target_dir, m.filename)
                        if extracted != expected:
                            shutil.move(extracted, expected)
                        _cleanup_extras(expected)
                        return expected
                # 精确匹配失败，全量解压后再搜索
                zf.extractall(target_dir)
        elif archive_path.endswith(".tar.gz"):
            with tarfile.open(archive_path, "r:gz") as tf:
                for m in tf.getmembers():
                    if m.name.endswith("/frpc") or m.name == "frpc":
                        tf.extract(m, target_dir)
                        extracted = os.path.join(target_dir, m.name)
                        if extracted != expected:
                            shutil.move(extracted, expected)
                        _cleanup_extras(expected)
                        return expected
                tf.extractall(target_dir)
    except (zipfile.BadZipFile, tarfile.TarError, OSError):
        return ""

    # 如果前两步都没找到，递归搜索整个目录
    found = _find_binary(target_dir)
    if found:
        if found != expected:
            try:
                shutil.move(found, expected)
            except OSError:
                pass
        _cleanup_extras(expected)
        return expected if os.path.isfile(expected) else found

    _cleanup_extras("")
    return ""
