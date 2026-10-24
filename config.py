"""
配置管理（config.json）
"""

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
        # 启动软件时顺带把面板也拉起来（后台线程，不挡住终端菜单）
        "auto_start": False,
        # 软件重启后，把上次退出时还在跑的实例再拉一遍
        "restore_running": True,
    },
    "ai": {
        # 面板里的 AI 助手。默认关着：没有 base_url/model/api_key 时它什么也
        # 做不了，而「默认关闭」也意味着升级上来的老配置不会突然开始往外发请求。
        "enabled": False,
        # OpenAI 兼容接口，填到 /v1 这一层，程序自己拼 /chat/completions
        "base_url": "",
        "api_key": "",
        "model": "",
    },
}


def _default_config() -> dict[str, Any]:
    """返回一份独立的默认配置。

    不能写成 dict(DEFAULT_CONFIG)：那是浅拷贝，config["web"] 会直接指向
    模块级常量里那个 dict，谁要是就地改一下端口就把默认值改掉了，之后每次
    加载都会带着别人写进去的值。所以可变容器要单独复制一份——比
    copy.deepcopy 快，也说清了到底复制了什么。
    **以后往 DEFAULT_CONFIG 里加可变值，记得在这里也复制一份。**
    """
    fresh = dict(DEFAULT_CONFIG)
    fresh["web"] = dict(DEFAULT_CONFIG["web"])
    fresh["ai"] = dict(DEFAULT_CONFIG["ai"])
    return fresh


def load_config(storage_dir: str) -> dict[str, Any]:
    """加载 config.json。文件不存在则自动返回默认配置。"""
    path = os.path.join(storage_dir, "config.json")
    config = _default_config()
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                # 全量合并，而不是只挑 DEFAULT_CONFIG 里认识的键。只认识已知键
                # 的话，用户自己加的顶层键、或更新版本写进去的设置，会在下一次
                # save_config（start.py 每次启动都会调）时被静默删掉——降级回
                # 旧版就等于丢设置。web 段里的自定义键一直是保住的，两边行为
                # 也因此统一了。
                config.update(loaded)
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
    """将配置保存到 config.json。保留所有字段（含本版不认识的）。"""
    merged = _default_config()
    merged.update(config)
    path = os.path.join(storage_dir, "config.json")
    text = json.dumps(merged, ensure_ascii=False, indent=2)
    os.makedirs(storage_dir, exist_ok=True)

    # 内容没变就别写。软件每次启动都会调一次 save_config，无脑重写既白费
    # 一次写盘，也会让文件 mtime 一直变（备份/同步工具会以为它总在改）。
    try:
        with open(path, "r", encoding="utf-8") as f:
            if f.read() == text:
                return
    except (OSError, UnicodeDecodeError):
        pass

    # 先写临时文件再 os.replace：open(path, "w") 是就地截断，写到一半崩了
    # （或断电）config.json 就是半截 JSON——load_config 只能整体退回默认值，
    # 用户的设置全没了。同分区上 os.replace 是原子的。
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
