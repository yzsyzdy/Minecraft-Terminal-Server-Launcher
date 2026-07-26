"""
HTTP 请求工具模块

提供统一的 HTTP GET/POST JSON 请求函数，
避免多个模块重复实现。
"""

import json
import urllib.request
import urllib.error
from typing import Any, Optional

from constants import USER_AGENT


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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            OSError, json.JSONDecodeError):
        return None