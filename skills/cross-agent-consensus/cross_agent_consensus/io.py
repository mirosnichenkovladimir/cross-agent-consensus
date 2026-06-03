"""Low-level IO helpers for the cross-agent-consensus helper CLI."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def repo_root_from_skill() -> Path:
    return skill_root().parents[1]


def read_cac_version() -> str:
    path = skill_root() / "VERSION"
    if path.is_file():
        version = path.read_text(encoding="utf-8").strip()
    else:
        version = "0.3.0"
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid cross-agent-consensus version: {version}")
    return version


def eprint(message: str) -> None:
    print(message, file=os.sys.stderr)


def slugify(value: str, default: str = "artifact") -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    value = re.sub(r"-{2,}", "-", value)
    return value[:64].strip("-") or default


def safe_relative_path(value: str, field_name: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_name} must be a relative path inside the run/package")
    return path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_locator(locator: str, cwd: Path | None = None) -> str | None:
    path = Path(locator)
    if not path.is_absolute() and cwd is not None:
        path = cwd / path
    if path.is_file():
        return sha256_file(path)
    return None


def atomic_write_new(path: Path, content: str, mode: int | None = None) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        if mode is not None:
            tmp_path.chmod(mode)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(content)


def write_bytes_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as fh:
        fh.write(data)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def read_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(data, sort_keys=True) + "\n")


def compact_json_value(value: Any, *, max_string: int = 2000, max_items: int = 50) -> Any:
    if isinstance(value, str):
        if len(value) > max_string:
            return value[:max_string] + "...<truncated>"
        return value
    if isinstance(value, list):
        result = [compact_json_value(item, max_string=max_string, max_items=max_items) for item in value[:max_items]]
        if len(value) > max_items:
            result.append({"truncated_items": len(value) - max_items})
        return result
    if isinstance(value, dict):
        items = list(value.items())
        result = {}
        for key, item in items[:max_items]:
            key_text = str(key)
            if key_text.lower() in {"thinking", "signature"}:
                result[key_text] = "<redacted>"
            else:
                result[key_text] = compact_json_value(item, max_string=max_string, max_items=max_items)
        if len(items) > max_items:
            result["truncated_keys"] = len(items) - max_items
        return result
    return value


def content_text_from_message(message: dict[str, Any]) -> str | None:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
        if parts:
            return "".join(parts)
    return None
