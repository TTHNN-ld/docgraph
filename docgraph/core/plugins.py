"""插件发现与注册。

支持两种来源：
1. 内置组件（硬编码，bootstrap_builtins 注册）
2. 第三方包通过 entry_points 注册：
   [project.entry-points."docgraph.parsers"]
   myparser = "my_pkg:MyParser"

加载顺序：先内置，再 entry_points 覆盖/补充。
"""
from __future__ import annotations

import importlib.metadata as md
from dataclasses import dataclass

from docgraph.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class PluginInfo:
    """单个插件的元信息（用于 docgraph plugins ls）。"""
    group: str             # "docgraph.parsers" 等
    name: str              # entry_point name
    target: str            # "my_pkg.module:Class"
    dist: str | None = None
    version: str | None = None
    builtin: bool = False
    enabled: bool = True


_DISCOVERED: dict[str, list[PluginInfo]] = {}


# entry_point group → registry 模块路径
_GROUP_TO_REGISTRY = {
    "docgraph.parsers":    "docgraph.parsers.base",
    "docgraph.extractors": "docgraph.extractors.base",
    "docgraph.embeddings": "docgraph.embeddings.base",
    "docgraph.stores":     None,  # 无统一 registry，按 name 字符串走 config
    "docgraph.llm":        None,
}


def _registry_for(group: str):
    mod_path = _GROUP_TO_REGISTRY.get(group)
    if not mod_path:
        return None
    try:
        import importlib
        mod = importlib.import_module(mod_path)
        return getattr(mod, "registry", None)
    except Exception:
        return None


def discover_entry_points(*, disabled: set[str] | None = None) -> dict[str, list[PluginInfo]]:
    """扫描所有 entry_points，把第三方组件注册到对应 registry。

    Args:
        disabled: 用户在 config 中显式禁用的 entry-point 名集合（{group:name} 形式）

    Returns:
        每个 group 下发现的插件列表。
    """
    disabled = disabled or set()
    out: dict[str, list[PluginInfo]] = {}
    eps = md.entry_points()

    for group in _GROUP_TO_REGISTRY:
        registry = _registry_for(group)
        plugins: list[PluginInfo] = []

        # importlib.metadata 在 3.10+ 用 .select(group=...)
        try:
            group_eps = eps.select(group=group)
        except AttributeError:  # pragma: no cover
            group_eps = [ep for ep in eps if ep.group == group]  # type: ignore

        for ep in group_eps:
            key = f"{group}:{ep.name}"
            info = PluginInfo(
                group=group,
                name=ep.name,
                target=f"{ep.module}:{ep.attr}" if hasattr(ep, "attr") else ep.value,
                dist=_safe_dist_name(ep),
                version=_safe_dist_version(ep),
                enabled=key not in disabled,
            )
            plugins.append(info)

            if not info.enabled:
                log.info(f"[plugins] skip disabled: {key}")
                continue
            if registry is None:
                continue
            try:
                cls = ep.load()
                registry.register(cls)
            except Exception as e:
                log.warning(f"[plugins] failed to load {key}: {e}")

        out[group] = plugins

    global _DISCOVERED
    _DISCOVERED = out
    return out


def discovered() -> dict[str, list[PluginInfo]]:
    """返回最近一次 discover 的结果（用于 CLI 展示）。"""
    return dict(_DISCOVERED)


def mark_builtin(group: str, name: str, target: str) -> None:
    """把内置组件也加进 PluginInfo 列表，便于 docgraph plugins ls 一并展示。"""
    lst = _DISCOVERED.setdefault(group, [])
    if any(p.name == name for p in lst):
        return
    lst.append(
        PluginInfo(
            group=group, name=name, target=target,
            dist="docgraph", builtin=True, enabled=True,
        )
    )


def _safe_dist_name(ep) -> str | None:
    try:
        d = ep.dist  # type: ignore[attr-defined]
        return d.name if d else None
    except Exception:
        return None


def _safe_dist_version(ep) -> str | None:
    try:
        d = ep.dist  # type: ignore[attr-defined]
        return d.version if d else None
    except Exception:
        return None
