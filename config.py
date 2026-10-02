"""
配置管理（config.json）
"""

import copy
import os
import json
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "java_path": None,
    "interactive": True,
    "language": "",
    "curseforge_api_key": "",
    "download_threads": 16,
    "auto_start_server": "",
    "web": {
        "host": "127.0.0.1",
        "port": 8765,
        "token": "",
    },
}


def _default_config() -> dict[str, Any]:
    """返回一份独立的默认配置。

    用 dict(DEFAULT_CONFIG) 是浅拷贝，config["web"] 会直接指向模块级常量里
    那个 dict；谁要是就地改一下端口就把默认值改掉了，之后每次加载都会带着
    别人写进去的值。默认值里只要还有可变容器，就得深拷贝。
    """
    return copy.deepcopy(DEFAULT_CONFIG)


def load_config(storage_dir: str) -> dict[str, Any]:
    """加载 config.json。文件不存在则自动返回默认配置。"""
    path = os.path.join(storage_dir, "config.json")
    config = _default_config()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for k in DEFAULT_CONFIG:
                    if k in loaded:
                        config[k] = loaded[k]
        except (json.JSONDecodeError, OSError):
            pass
    # web 段可能被手改成非 dict，或者缺字段，这里补回默认结构
    web = config.get("web")
    if not isinstance(web, dict):
        web = {}
    merged_web = dict(DEFAULT_CONFIG["web"])
    merged_web.update(web)
    config["web"] = merged_web
    return config


def save_config(config: dict[str, Any], storage_dir: str) -> None:
    """将配置保存到 config.json。保留所有字段。"""
    merged = _default_config()
    merged.update(config)
    path = os.path.join(storage_dir, "config.json")
    os.makedirs(storage_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
