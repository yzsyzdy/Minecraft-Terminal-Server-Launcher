"""
OpenFrp tunnel management module.

Provides token-based tunnel listing, creation, deletion, and editing.
Uses API 8 (token-based, simplest) for listing, and Header-based APIs for CRUD.
"""

import json
import os
import platform
import subprocess
import shutil
import urllib.request
import urllib.error
from typing import Any, Optional
from dataclasses import dataclass, field

from constants import USER_AGENT, OPENFRP_API, OPENFRP_SOFTWARE
from i18n import t


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TunnelInfo:
    """A single OpenFrp tunnel."""
    proxy_id: int
    name: str
    proxy_type: str
    node: str
    local_addr: str
    local_port: int
    remote_addr: str
    online: bool = False

    @staticmethod
    def from_token_api(item: dict, node_name: str) -> "TunnelInfo":
        """Parse from API 8 token-based list response."""
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
        """Parse from Header-based list response."""
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
    """An OpenFrp node."""
    node_id: int
    name: str
    classify: int
    status: int
    allow_port: Optional[str] = None
    need_realname: bool = False
    fully_loaded: bool = False
    protocol_support: dict = field(default_factory=lambda: {
        "tcp": True, "udp": True, "http": False, "https": False,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_json(url: str, data: Optional[dict] = None,
                headers: Optional[dict] = None, timeout: int = 15) -> Optional[Any]:
    """GET or POST JSON request. Returns parsed JSON or None."""
    req_headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    body = json.dumps(data).encode("utf-8") if data else None
    try:
        req = urllib.request.Request(url, data=body, headers=req_headers, method="POST" if data else "GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return None


def _os_arch() -> str:
    """Detect OS and architecture for frpc download."""
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
# Token-based APIs (no Authorization required)
# ---------------------------------------------------------------------------

def get_tunnels_by_token(token: str) -> Optional[list[tuple[str, list[TunnelInfo]]]]:
    """
    Fetch all tunnels using user token (API 8).

    Returns list of (node_name, [TunnelInfo]) grouped by node.
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
# Header-based APIs (require Authorization)
# ---------------------------------------------------------------------------

def get_node_list(auth_token: str) -> Optional[list[NodeInfo]]:
    """Fetch all nodes (API 6). Requires Authorization header."""
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
    """Create a new tunnel (API 4). Returns msg string on success."""
    url = f"{OPENFRP_API}/frp/api/newProxy"
    result = _fetch_json(url, data=params, headers={"Authorization": auth_token})
    if not result:
        return None
    return result.get("msg", "")


def delete_tunnel(auth_token: str, proxy_id: int) -> Optional[str]:
    """Delete a tunnel (API 5). Returns msg string on success."""
    url = f"{OPENFRP_API}/frp/api/removeProxy"
    result = _fetch_json(url, data={"proxy_id": proxy_id},
                         headers={"Authorization": auth_token})
    if not result:
        return None
    return result.get("msg", "")


def edit_tunnel(auth_token: str, params: dict) -> Optional[str]:
    """Edit an existing tunnel (API 7). Returns msg string on success."""
    url = f"{OPENFRP_API}/frp/api/editProxy"
    result = _fetch_json(url, data=params, headers={"Authorization": auth_token})
    if not result:
        return None
    return result.get("msg", "")


def get_tunnels_by_auth(auth_token: str) -> Optional[list[TunnelInfo]]:
    """Fetch all tunnels using Authorization (API 3)."""
    url = f"{OPENFRP_API}/frp/api/getUserProxies"
    result = _fetch_json(url, headers={"Authorization": auth_token}, timeout=10)
    if not result or not result.get("flag"):
        return None
    data = result.get("data") or {}
    raw_list = data.get("list") or []
    return [TunnelInfo.from_auth_api(item) for item in raw_list]


def get_user_info(auth_token: str) -> Optional[dict]:
    """Fetch user info (API 2). Returns data dict."""
    url = f"{OPENFRP_API}/frp/api/getUserInfo"
    result = _fetch_json(url, headers={"Authorization": auth_token})
    if not result or not result.get("flag"):
        return None
    return result.get("data")


def user_sign(auth_token: str) -> Optional[str]:
    """Daily sign-in (API 9). Returns msg. Auto-sign is against ToS."""
    url = f"{OPENFRP_API}/frp/api/userSign"
    result = _fetch_json(url, headers={"Authorization": auth_token})
    if not result:
        return None
    return result.get("msg", "")


# ---------------------------------------------------------------------------
# Frpc download
# ---------------------------------------------------------------------------

def get_frpc_release_info() -> Optional[dict[str, Any]]:
    """Fetch latest frpc version info and download sources."""
    result = _fetch_json(OPENFRP_SOFTWARE, timeout=10)
    if not result or not result.get("flag"):
        return None
    return result.get("data")


def get_frpc_download_urls() -> Optional[tuple[str, list[str], str]]:
    """
    Resolve all possible download URLs for the current system.
    Returns (version_full, [download_url, ...], file_name) or None.
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

    # 0) User-provided Gitee mirror (most reliable)
    gitee_url = (
        "https://gitee.com/yzsyzdy/breeze_sever_tg/releases/download/files/"
        f"{file_name}"
    )
    urls.append(gitee_url)

    # 1) Mirrors from OpenFrp API
    for src in sources:
        base = src.get("value", "")
        if base:
            url = f"{base}/{latest_full}/{file_name}"
            urls.append(url)

    # 2) GitHub release (most reliable fallback)
    #    Pattern: https://github.com/OpenFrp/frpc-release/releases/download/{tag}/{file}
    tag = latest_full or f"v{latest_ver}"
    github_url = (
        f"https://github.com/OpenFrp/frpc-release/releases/download/"
        f"{tag}/{file_name}"
    )
    urls.append(github_url)

    if not urls:
        return None
    return latest_full, urls, file_name


_FRPC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def get_frpc_local_path(project_dir: str) -> str:
    """Get the expected local frpc path."""
    _, _, file_name = _os_arch()
    return os.path.join(project_dir, "openfrp", file_name)


def _download_frpc_file(url: str, target_path: str) -> bool:
    """Download frpc binary with a browser-like User-Agent."""
    import urllib.request, urllib.error
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": _FRPC_USER_AGENT})
        with urllib.request.urlopen(req, timeout=300) as resp:
            # Check content-type early
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

        # Validate: zip must start with PK\x03\x04
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
    """
    Download the latest frpc for current platform.
    Tries all available download sources in order.
    Returns the local file path, or None on failure.
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
            # Empty file = failed silently
            try:
                os.remove(target_path)
            except OSError:
                pass
        print(f"  [OpenFrp] Mirror {i + 1} failed, trying next...")

    print("  [OpenFrp] " + t("app.error", msg="All download mirrors failed"))
    return None


_frpc_process: Optional[subprocess.Popen] = None


def launch_frpc(project_dir: str, server_cfg: dict) -> Optional[subprocess.Popen]:
    """
    Launch frpc as a subprocess for a server's tunnel.
    Returns the Popen object, or None on failure.
    """
    global _frpc_process
    import subprocess

    of_cfg = server_cfg.get("openfrp", {}) or {}
    token = of_cfg.get("token", "")
    proxy_id = of_cfg.get("proxy_id", 0)

    if not token or not proxy_id:
        print("  [OpenFrp] " + t("app.error", msg="token or proxy_id not configured"))
        return None

    frpc_dir = os.path.join(project_dir, "openfrp")
    base_name = "frpc.exe" if platform.system().lower() == "windows" else "frpc"
    frpc_bin = os.path.join(frpc_dir, base_name)
    frpc_archive = get_frpc_local_path(project_dir)  # e.g. frpc_windows_amd64.zip

    # Resolve frpc binary: check cache, extract, or download
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
            stdout=None,
            stderr=None,
        )
        _frpc_process = proc
        # Give frpc a moment to connect
        import time
        time.sleep(2)
        return proc
    except OSError as e:
        print(f"  [OpenFrp] " + t("app.error", msg=str(e)))
        return None


def stop_frpc() -> None:
    """Stop the running frpc process."""
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
    """Extract frpc from downloaded zip/tar.gz. Returns path to the binary."""
    import zipfile, tarfile
    target_dir = os.path.dirname(archive_path)
    base_name = "frpc.exe" if platform.system().lower() == "windows" else "frpc"
    expected = os.path.join(target_dir, base_name)

    # Helper: find any candidate binary in a directory tree
    def _find_binary(dirpath: str) -> str:
        for root, _dirs, files in os.walk(dirpath):
            for f in files:
                if f == base_name or f.lower() == base_name.lower():
                    return os.path.join(root, f)
                if "frpc" in f.lower() and (
                    f.endswith(".exe") or "." not in f
                ):
                    return os.path.join(root, f)
        return ""

    try:
        if archive_path.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                for m in zf.infolist():
                    if m.filename.endswith("frpc.exe") or m.filename.endswith("/frpc"):
                        zf.extract(m, target_dir)
                        extracted = os.path.join(target_dir, m.filename)
                        if extracted != expected:
                            shutil.move(extracted, expected)
                        return expected
                # No exact match — extract everything, then search
                zf.extractall(target_dir)
        elif archive_path.endswith(".tar.gz"):
            with tarfile.open(archive_path, "r:gz") as tf:
                for m in tf.getmembers():
                    if m.name.endswith("/frpc") or m.name == "frpc":
                        tf.extract(m, target_dir)
                        extracted = os.path.join(target_dir, m.name)
                        if extracted != expected:
                            shutil.move(extracted, expected)
                        return expected
                # No exact match — extract everything
                tf.extractall(target_dir)
    except (zipfile.BadZipFile, tarfile.TarError, OSError):
        return ""  # signal caller that extraction failed

    # If we get here, no exact match was found — search for any frpc binary
    found = _find_binary(target_dir)
    if found and found != expected:
        try:
            shutil.move(found, expected)
            return expected
        except OSError:
            return found
    return found or expected
