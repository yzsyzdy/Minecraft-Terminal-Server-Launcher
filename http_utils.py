"""
HTTP 请求工具模块

提供统一的 HTTP GET/POST JSON 请求函数，
避免多个模块重复实现。
"""

import json
import http.client
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Optional

from constants import USER_AGENT

# 凭据类请求头：跨主机重定向时必须丢掉。Referer 本身不是凭据，但它会告诉
# 新主机用户是从哪个 API 跳过来的，和 x-api-key 一起泄露等于把调用方身份
# 也交出去。
_CREDENTIAL_HEADERS = frozenset({
    "x-api-key", "authorization", "proxy-authorization", "cookie", "referer",
})


class _CredentialSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """跨主机重定向时丢掉凭据类请求头。

    urllib 默认把原始请求头原样复制到重定向后的请求上，所以 CurseForge 的
    x-api-key 会跟着一次 CDN 跳转被送到第三方主机，而最终落点从不检查、
    也不记录。这里在主机变化时把凭据摘掉；同主机重定向（绝大多数 CDN 跳转
    都是这种）不受影响。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        old_host = urllib.parse.urlsplit(req.full_url).netloc.lower()
        new_host = urllib.parse.urlsplit(newurl).netloc.lower()
        if old_host != new_host:
            for name in list(new_req.headers):
                if name.lower() in _CREDENTIAL_HEADERS:
                    del new_req.headers[name]
        return new_req


_OPENER = urllib.request.build_opener(_CredentialSafeRedirectHandler())


def open_url(req, timeout: int = 30):
    """统一的 urlopen：跟随重定向，但不把凭据头带过主机边界。

    所有需要发请求的地方都该走这里，而不是直接 urllib.request.urlopen。
    """
    return _OPENER.open(req, timeout=timeout)


def fetch_json(url: str, headers: Optional[dict] = None,
               data: Optional[dict] = None, timeout: int = 15) -> Optional[Any]:
    """
    HTTP GET/POST JSON 请求。

    Args:
        url: 请求 URL
        headers: 额外的请求头
        data: POST 的 JSON 数据（有数据时自动用 POST 方法）
        timeout: 超时秒数

    Returns:
        解析后的 JSON 数据，失败返回 None
    """
    req_headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)

    body = json.dumps(data).encode("utf-8") if data else None
    try:
        req = urllib.request.Request(
            url, data=body, headers=req_headers,
            method="POST" if data else "GET",
        )
        with open_url(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            OSError, json.JSONDecodeError):
        return None
    except (http.client.HTTPException, ValueError):
        # 拼错的 URL 是在发请求之前炸的，不属于上面任何一类：
        #   空格等控制字符 -> http.client.InvalidURL (HTTPException)
        #   非 ASCII 字符  -> UnicodeEncodeError (ValueError)
        # 不接住的话会一路冒泡到界面层；这里统一降级成「请求失败」。
        return None