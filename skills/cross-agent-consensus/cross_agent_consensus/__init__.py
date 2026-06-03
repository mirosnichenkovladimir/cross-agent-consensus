"""Internal package for the cross-agent-consensus skill helper CLI."""

from __future__ import annotations

import re
from pathlib import Path


VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def skill_root() -> Path:
    """Return the installed skill package root."""

    return Path(__file__).resolve().parents[1]


def read_version() -> str:
    """Read and validate the installed skill package version."""

    version_path = skill_root() / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid cross-agent-consensus version: {version}")
    return version


__version__ = read_version()
