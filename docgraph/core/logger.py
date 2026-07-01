"""轻量日志包装。M1 阶段不上 structlog，用 rich 直接输出即可。"""
from __future__ import annotations

import logging
import sys

from rich.console import Console
from rich.logging import RichHandler

_console = Console(stderr=True)


def get_logger(name: str = "docgraph") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = RichHandler(
        console=_console,
        show_time=False,
        show_path=False,
        markup=True,
        rich_tracebacks=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def set_level(level: str) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger("docgraph").setLevel(lvl)


def stdout_console() -> Console:
    return Console(file=sys.stdout)
