"""Installed package version.

The distribution metadata in ``pyproject.toml`` is the single version source.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("docgraph-core")
except PackageNotFoundError:  # pragma: no cover - source imported without installation
    __version__ = "0+unknown"
