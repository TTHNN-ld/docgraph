"""轻量 .env 加载器。不引入 python-dotenv 依赖（保持核心轻量）。

支持：
- 简单 KEY=VALUE 格式
- 注释 #
- 引号包裹（单/双引号）
- export 前缀（兼容 bash 风格）
- 跳过已设置的环境变量（不覆盖）

不支持（暂时）：
- 多行值
- 变量插值（${VAR}）
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_env_file(path: Path | str, override: bool = False) -> dict[str, str]:
    """读取一个 .env 文件，把变量灌入 os.environ。

    Args:
        path: .env 文件路径
        override: True 则覆盖已有环境变量；默认 False（尊重已设置的值）

    Returns:
        本次设置的变量字典（含已存在但被 override 的项）
    """
    p = Path(path)
    if not p.is_file():
        return {}

    set_vars: dict[str, str] = {}
    for raw in p.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        # 去引号
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        if not override and key in os.environ:
            continue
        os.environ[key] = val
        set_vars[key] = val
    return set_vars


def autoload_env(start: Path | None = None) -> dict[str, str]:
    """从 start 目录向上找 .env / .env.local 并加载。

    优先级：已存在环境变量 > 用户级 ~/.docgraph/.env.local > ~/.docgraph/.env >
    项目级 .env.local > .env（后加载不会覆盖已设置值）。
    """
    cur = (start or Path.cwd()).resolve()
    loaded: dict[str, str] = {}
    user_dir = Path.home() / ".docgraph"
    for fname in (".env.local", ".env"):
        p = user_dir / fname
        if p.is_file():
            loaded.update(load_env_file(p))
    for d in [cur, *cur.parents]:
        # 优先 .env.local（个人覆盖），再 .env（共享默认）
        for fname in (".env.local", ".env"):
            p = d / fname
            if p.is_file():
                loaded.update(load_env_file(p))
        # 项目根：见到 .docgraph/ 或 pyproject.toml 停下
        if (d / ".docgraph").is_dir() or (d / "pyproject.toml").is_file():
            break
    return loaded
