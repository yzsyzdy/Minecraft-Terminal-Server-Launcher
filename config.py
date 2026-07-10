"""
配置管理（config.json）
"""

import os
import json
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "java_path": None,
    "interactive": True,
}


def load_config(storage_dir: str) -> dict[str, Any]:
    """加载 config.json。文件不存在则自动返回默认配置。"""
    path = os.path.join(storage_dir, "config.json")
    config = dict(DEFAULT_CONFIG)
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
    return config


def save_config(config: dict[str, Any], storage_dir: str) -> None:
    """保存配置到 config.json。"""
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    path = os.path.join(storage_dir, "config.json")
    os.makedirs(storage_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
