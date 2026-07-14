"""Runtime checks for optional parser dependencies.

Only project-owned, allow-listed extras may be installed. Parser names from user
configuration are never converted into arbitrary package names.
"""
from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DependencyPolicy = Literal["prompt", "install", "fallback", "error"]


@dataclass(frozen=True)
class ParserDependency:
    parser: str
    module: str
    extra: str | None
    display_name: str
    model_notice: str | None = None


@dataclass(frozen=True)
class DependencyResult:
    available: bool
    attempted_install: bool = False
    installed: bool = False
    reason: str | None = None


PARSER_DEPENDENCIES: dict[str, ParserDependency] = {
    "pymupdf": ParserDependency("pymupdf", "pymupdf", None, "PyMuPDF"),
    "docling": ParserDependency(
        "docling",
        "docling",
        "docling",
        "Docling",
        "Docling may download model artifacts into its upstream cache on first use.",
    ),
    "mineru": ParserDependency(
        "mineru",
        "magic_pdf",
        "mineru",
        "MinerU (legacy magic-pdf adapter)",
        "MinerU may download large model artifacts on first use.",
    ),
    "marker": ParserDependency("marker", "marker", "marker", "Marker"),
    "docx": ParserDependency("docx", "docx", "documents", "python-docx"),
    "xlsx": ParserDependency("xlsx", "openpyxl", "documents", "openpyxl"),
    "markdown": ParserDependency(
        "markdown", "markdown_it", "documents", "markdown-it-py"
    ),
}


def parser_dependency(parser_name: str) -> ParserDependency | None:
    return PARSER_DEPENDENCIES.get(parser_name.strip().lower())


def ensure_parser_dependency(
    parser_name: str,
    policy: DependencyPolicy,
    *,
    confirm: Callable[[str], bool] | None = None,
) -> DependencyResult:
    """Check and, when authorized, install an optional parser extra."""
    dependency = parser_dependency(parser_name)
    if dependency is None:
        # Third-party parser plugins own their dependency lifecycle.
        return DependencyResult(available=True)
    if _module_available(dependency.module):
        return DependencyResult(available=True)
    if dependency.extra is None:
        return DependencyResult(
            available=False,
            reason=f"required dependency '{dependency.module}' is not installed",
        )

    should_install = policy == "install"
    if policy == "prompt":
        if confirm is not None:
            should_install = confirm(_install_prompt(dependency))
        elif _is_interactive():
            answer = input(_install_prompt(dependency)).strip().lower()
            should_install = answer in {"y", "yes"}
        else:
            return DependencyResult(
                available=False,
                reason=(
                    f"{dependency.display_name} is not installed; non-interactive "
                    "builds do not modify the environment (use --install-missing)"
                ),
            )

    if not should_install:
        return DependencyResult(
            available=False,
            reason=f"{dependency.display_name} is not installed",
        )

    command, target = _extra_install_command(dependency.extra)
    try:
        completed = subprocess.run(
            command,
            check=False,
        )
    except OSError as exc:
        return DependencyResult(
            available=False,
            attempted_install=True,
            reason=f"could not start pip: {exc}",
        )
    importlib.invalidate_caches()
    installed = completed.returncode == 0 and _module_available(dependency.module)
    return DependencyResult(
        available=installed,
        attempted_install=True,
        installed=installed,
        reason=None if installed else f"pip could not install {target}",
    )


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _install_prompt(dependency: ParserDependency) -> str:
    return (
        f"{dependency.display_name} is required but not installed. "
        f"Install the docgraph[{dependency.extra}] extra now? [y/N] "
    )


def _extra_install_command(extra: str) -> tuple[list[str], str]:
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        target = f"{source_root}[{extra}]"
        return [sys.executable, "-m", "pip", "install", "-e", target], target
    target = f"docgraph[{extra}]"
    return [sys.executable, "-m", "pip", "install", target], target
