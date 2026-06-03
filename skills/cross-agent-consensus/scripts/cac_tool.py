#!/usr/bin/env python3
"""Compatibility entrypoint for historical cac_tool.py callers."""

from __future__ import annotations

import sys
from pathlib import Path


SKILL_ROOT_FOR_IMPORTS = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT_FOR_IMPORTS) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT_FOR_IMPORTS))

from cross_agent_consensus.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
