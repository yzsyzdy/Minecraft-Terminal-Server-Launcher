"""
Java 解析（不需要提问的那一条路）。

CLI 里可以弹菜单让人挑 JDK、可以问要不要顺手下一个。web 面板和 TUI 不能
停在那里等人输入，所以这里给出一条完全非交互的解析路径：

    显式配置 → 上次的选择 → 缓存的扫描结果 → 现场扫一遍

逐级回退，全都没有就抛 CoreError。检测、版本比较、兼容表这些判断仍然
复用 java_tools，只是把「打印」和「提问」都摘掉了。
"""

from __future__ import annotations

import os
from typing import Optional

from .errors import CoreError
from .report import Reporter, as_reporter
from java_tools import (
    JavaInfo,
    _java_major_version,
    _last_selected_path,
    detect_java_versions,
    get_java_requirement,
    load_java_list,
    save_java_list,
)


def _usable(java: JavaInfo) -> bool:
    return bool(java.path) and os.path.isfile(java.path)


def _sorted(javas: list[JavaInfo]) -> list[JavaInfo]:
    return sorted(javas, key=lambda j: _java_major_version(j.version), reverse=True)


def _pick(javas: list[JavaInfo], requirement: Optional[dict]) -> tuple[Optional[JavaInfo], bool]:
    """挑一个可用的 Java，返回 (JavaInfo, 是否满足 MC 版本要求)。

    要求已知时优先挑满足要求的，且版本越接近推荐值越好；一个都不满足就
    退回最高版本并标记 False，让调用方决定是警告还是拒绝。
    """
    candidates = _sorted([j for j in javas if _usable(j)])
    if not candidates:
        return None, False
    if requirement:
        minimum = _java_major_version(requirement["min"])
        recommended = _java_major_version(requirement["recommended"])
        compatible = [j for j in candidates if _java_major_version(j.version) >= minimum]
        if compatible:
            compatible.sort(key=lambda j: abs(_java_major_version(j.version) - recommended))
            return compatible[0], True
        return candidates[0], False
    return candidates[0], True


def resolve_java_for_launch(
    configured_path: Optional[str],
    storage_dir: str,
    mc_version: str = "",
    *,
    reporter: Optional[Reporter] = None,
) -> str:
    """解析出一个可以直接拿去启动服务端的 java 可执行文件路径。"""
    reporter = as_reporter(reporter)

    if configured_path:
        if os.path.isfile(configured_path):
            return configured_path
        raise CoreError("app.error_java_not_found", path=configured_path)

    last = _last_selected_path(storage_dir)
    if last and os.path.isfile(last):
        return last

    requirement = get_java_requirement(mc_version) if mc_version else None

    chosen, satisfied = _pick(load_java_list(storage_dir), requirement)
    if chosen is None:
        reporter.log("java.scanning")
        scanned = detect_java_versions()
        chosen, satisfied = _pick(scanned, requirement)
        if chosen is not None:
            save_java_list(scanned, storage_dir, chosen.path)

    if chosen is None:
        if requirement:
            raise CoreError("java.need_version", mc=mc_version, ver=requirement["min"])
        raise CoreError("java.none_found")

    if not satisfied and requirement:
        reporter.warn("java.compat.current", ver=chosen.version,
                      major=_java_major_version(chosen.version))
    return chosen.path


__all__ = ["resolve_java_for_launch"]
