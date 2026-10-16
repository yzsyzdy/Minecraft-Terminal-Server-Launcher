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

import os
import subprocess
import time
import urllib.parse
from typing import Any, Optional
from dataclasses import dataclass, field

from constants import USER_AGENT, OPENFRP_API, OPENFRP_SOFTWARE
from frp import frpc_files
from http_utils import fetch_json
from download_msl import download_file_with_progress
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
# HTTP 请求工具（使用 http_utils.fetch_json 统一实现）
# 不再重复实现 _fetch_json，详见 http_utils.py
# ---------------------------------------------------------------------------


def _os_arch() -> tuple[str, str, str]:
    """检测当前操作系统和架构，返回 (os, arch, file_name)。

    frpc 的发布文件命名规则: frpc_{os}_{arch}.zip / .tar.gz
    平台与架构的映射表在 frpc_files 里，与原版 frp 共用一份。
    """
    os_name, arch, ext = frpc_files.os_arch()
    return os_name, arch, f"frpc_{os_name}_{arch}{ext}"


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
    # token 要转义：里面出现 & 或 # 会把查询串截断，接口只会回一个
    # 「无法获取隧道列表」，看日志完全猜不到是 token 里的字符导致的
    url = (f"{OPENFRP_API}/api?action=getallproxies"
           f"&user={urllib.parse.quote(token, safe='')}")
    result = fetch_json(url, timeout=10)
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
    result = fetch_json(url, headers={"Authorization": auth_token}, timeout=10)
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
    """创建新隧道 (API 4)。成功返回服务端提示消息，失败返回 None。

    必须看 flag 而不是只看 msg：失败的响应同样带 msg，只判 msg 非空会把
    「隧道数量已达上限」这类错误当成创建成功打印出来。
    """
    url = f"{OPENFRP_API}/frp/api/newProxy"
    result = fetch_json(url, data=params, headers={"Authorization": auth_token})
    if not result or not result.get("flag"):
        return None
    return result.get("msg", "")


def delete_tunnel(auth_token: str, proxy_id: int) -> Optional[str]:
    """删除隧道 (API 5)。成功返回服务端提示消息，失败返回 None。"""
    url = f"{OPENFRP_API}/frp/api/removeProxy"
    result = fetch_json(url, data={"proxy_id": proxy_id},
                         headers={"Authorization": auth_token})
    if not result or not result.get("flag"):
        return None
    return result.get("msg", "")


# ---------------------------------------------------------------------------
# frpc 二进制下载
# frpc 是 OpenFrp 的客户端程序，负责建立隧道连接。
# 下载流程: API 获取版本 → 构造 URL → 逐一尝试多个 CDN 镜像
# ---------------------------------------------------------------------------

def get_frpc_release_info() -> Optional[dict[str, Any]]:
    """从 OpenFrp API 获取最新 frpc 版本信息和 CDN 源列表。"""
    result = fetch_json(OPENFRP_SOFTWARE, timeout=10)
    if not result or not result.get("flag"):
        return None
    return result.get("data")


def get_frpc_download_urls() -> Optional[tuple[str, list[str], str]]:
    """生成所有可用的 frpc 下载 URL。

    优先级: OpenFrp API 返回的官方 CDN 源，按 API 给出的顺序逐个尝试。
    没有第二梯队了——曾经的那个 GitHub 兜底仓库并不存在（见下）。

    Returns:
        (version_full, [url1, url2, ...], file_name)
    """
    data = get_frpc_release_info()
    if not data:
        return None

    latest_full = data.get("latest_full", "")
    sources = data.get("source") or []
    if not latest_full:
        return None

    _, _, file_name = _os_arch()
    urls = []

    # OpenFrp 官方 API 返回的 CDN 镜像列表（由 OpenFrp 官方维护）
    for src in sources:
        base = src.get("value", "")
        if base:
            url = f"{base}/{latest_full}/{file_name}"
            urls.append(url)

    # 这里曾经还有一个 GitHub 兜底：
    #   https://github.com/OpenFrp/frpc-release/releases/download/{tag}/{file_name}
    # 那个仓库并不存在（api.github.com/repos/OpenFrp/frpc-release → 404，
    # OpenFrp 组织下只有 Depoly_templates），所以每一次走到兜底都必然多花
    # 一次超时、拿一个 404。OpenFrp 的 frpc 只发在自己的 CDN 上，官方下载
    # 页见 OPENFRP_RELEASES_URL。

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


# OpenFrp 的 frpc 不在 GitHub 上发布（曾指向的 OpenFrp/frpc-release 是 404），
# 官方下载入口在控制台的下载页。自动下载全挂时把这个地址给用户。
OPENFRP_RELEASES_URL = "https://console.openfrp.net/download"


def expected_asset_name() -> str:
    """用户该从 GitHub 下哪个文件，例如 frpc_windows_amd64.zip。"""
    _, _, file_name = _os_arch()
    return file_name


def _download_frpc_file(url: str, target_path: str) -> bool:
    """下载单个 frpc 文件（使用公共下载函数 + frpc 专用校验）。

    校验点:
      1. 使用 Chrome User-Agent 避免 CDN 拦截
      2. 按内容嗅探：必须是本平台期望的压缩包，或者本身就是可执行文件
      3. 内容不对就删掉，不留残片

    第 2 点是后补的：以前只对 `.zip` 校验魔数，于是 Linux/macOS 上镜像回
    一个 HTTP 200 的 HTML 错误页（OneDrive 之类，正是本函数 docstring 声称
    已排除的情况）会被原样存成 `.tar.gz`；`download_frpc` 看到文件非空就
    当成功，解压时才失败，而这份垃圾会永久留在缓存里，每次启动都先重试它。
    Windows 上同样的页面会被正确拒绝，于是问题只在 Linux 出现。
    """
    headers = {"User-Agent": _FRPC_USER_AGENT}
    if not download_file_with_progress(url, target_path, desc="frpc", headers=headers):
        return False

    expected = "zip" if target_path.endswith(".zip") else "tar"
    if (frpc_files.archive_kind(target_path) != expected
            and not frpc_files.looks_like_executable(target_path)):
        print(f"\n  {t('openfrp.frpc_bad_download', name=os.path.basename(target_path))}")
        frpc_files.remove_quietly(target_path)
        return False

    return True


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

class FrpcManager:
    """frpc 进程管理器，封装生命周期管理，支持多实例追踪。"""

    _instance: Optional["FrpcManager"] = None
    _process: Optional[subprocess.Popen] = None

    @classmethod
    def get_instance(cls) -> "FrpcManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def launch(self, project_dir: str, server_cfg: dict) -> Optional[subprocess.Popen]:
        """启动 frpc 子进程。"""
        of_cfg = server_cfg.get("openfrp", {}) or {}
        token = of_cfg.get("token", "")
        proxy_id = of_cfg.get("proxy_id", 0)

        if not token or not proxy_id:
            print("  [OpenFrp] " + t("app.error", msg="token or proxy_id not configured"))
            return None

        # 已经在跑就别再起一个：旧句柄会被覆盖，先前的 frpc 谁也停不掉，
        # 只能一直占着远程端口
        if self._process is not None and self._process.poll() is None:
            print("  [OpenFrp] frpc already running, ignoring this launch")
            return self._process

        frpc_dir = os.path.join(project_dir, "openfrp")
        base_name = frpc_files.default_binary_name()
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
        # 不回显 token 片段：控制台会被截图、也会落进日志
        print(f"  [OpenFrp] {cmd[0]} -p {proxy_id}")
        print()

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=None,
                stderr=None,
            )
        except OSError as e:
            print(f"  [OpenFrp] " + t("app.error", msg=str(e)))
            return None

        time.sleep(2)
        # frpc 配置或 token 有问题时会立刻退出，这时返回进程对象等于告诉
        # 调用方「启动成功」，而隧道其实没通
        if proc.poll() is not None:
            print("  [OpenFrp] " + t("app.error", msg=(
                f"frpc exited immediately (code {proc.returncode}); "
                "please check the tunnel binding")))
            return None

        self._process = proc
        return proc

    def stop(self) -> None:
        """停止正在运行的 frpc 进程。"""
        proc = self._process
        self._process = None
        if proc is not None and proc.poll() is None:
            print("  [OpenFrp] Stopping frpc...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


# 保持向后兼容的全局函数
def launch_frpc(project_dir: str, server_cfg: dict) -> Optional[subprocess.Popen]:
    """启动 frpc 子进程（使用 FrpcManager 单例）。"""
    return FrpcManager.get_instance().launch(project_dir, server_cfg)


def stop_frpc() -> None:
    """停止正在运行的 frpc 进程（使用 FrpcManager 单例）。"""
    FrpcManager.get_instance().stop()


def maybe_auto_start_for_instance(project_dir: str, server_cfg: dict) -> bool:
    """实例配了「随实例自动开启隧道」就把 frpc 拉起来。

    这段策略原来只写在 start.py 的终端前台启动里，于是网页面板的「启动」
    与「随软件启动」两条路都不会开隧道：面板设置里那个勾（文案写的是
    「启动实例时自动开启隧道」）存得下、看得见，却从来不生效——用户在面板
    里点启动，服务器起来了，外面却连不进来，页面上也不会说任何话。

    终端、面板、开机自动启动三条路现在都走这一个函数。

    注意 launch() 里有 2 秒的等待、必要时还会去下载 frpc，所以网页面板那边
    要放到后台线程里调，别堵住 HTTP 响应。
    返回是否真的去启动了。
    """
    of_cfg = (server_cfg or {}).get("openfrp") or {}
    if not of_cfg.get("auto_start"):
        return False
    if not (of_cfg.get("token") and of_cfg.get("proxy_id")):
        print("  [OpenFrp] 已开启自动启动，但 token 或隧道 ID 没填，跳过。")
        return False

    print()
    if launch_frpc(project_dir, server_cfg) is None:
        print("  [警告] frpc 启动失败，隧道可能不可用")
    print()
    return True


def _extract_frpc_if_needed(archive_path: str, project_dir: str) -> str:
    """从 zip/tar.gz 中解出 frpc 二进制，返回它的路径；失败返回空串。

    实现在 frpc_files（与原版 frp 共用一份）。相比原来这里自己的实现，
    有两点变化：

      - 只挑出 frpc 那一个文件，不再「找不到就整包解压再递归搜索」。
        解压目录是用户的数据目录，往里倒一整个第三方包既没必要也不安全
        （原来那套还要再回头删掉多出来的文件）。档案内部按 basename 递归
        匹配，子目录里的 frpc 一样找得到。
      - 解出来的东西要像本平台的可执行文件，不像就删掉并失败——原来这里
        不校验，用户传个 txt 进来也会显示「已就绪」。
    """
    return frpc_files.extract_binary(archive_path, os.path.dirname(archive_path))
