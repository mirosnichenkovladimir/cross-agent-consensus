"""Markdown frontmatter parsing and rendering for CAC protocol records."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cross_agent_consensus.models import Record
from cross_agent_consensus.record_schema import FIELD_ALIASES, ID_FIELDS, KNOWN_RECORD_TYPES


SCALAR_NULLS = {"null", "~"}
RECORD_HEADING_RE = re.compile(
    r"^##\s+(?P<record_type>[A-Za-z][A-Za-z0-9]*)\s+(?P<record_id>\S+)\s*$",
    re.MULTILINE,
)


def count_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def is_list_item(line: str) -> bool:
    stripped = line.strip()
    return stripped == "-" or stripped.startswith("- ")


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value in {"[]", "[ ]"}:
        return []
    if value in {"{}"}:
        return {}
    lower = value.lower()
    if lower in SCALAR_NULLS:
        return None
    if lower == "true":
        return True
    if lower == "false":
        return False
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            return value
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part.strip()) for part in inner.split(",")]
    return value


def parse_list(lines: list[str], index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    i = index
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        current_indent = count_indent(line)
        if current_indent < indent:
            break
        stripped = line.strip()
        if current_indent != indent or not (stripped == "-" or stripped.startswith("- ")):
            break
        value = "" if stripped == "-" else stripped[2:].strip()
        if value:
            items.append(parse_scalar(value))
            i += 1
        else:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j >= len(lines) or count_indent(lines[j]) <= current_indent:
                items.append(None)
                i += 1
            elif is_list_item(lines[j]):
                child, i = parse_list(lines, j, count_indent(lines[j]))
                items.append(child)
            else:
                child, i = parse_mapping(lines, j, count_indent(lines[j]))
                items.append(child)
    return items, i


def parse_mapping(lines: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    data: dict[str, Any] = {}
    i = index
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        current_indent = count_indent(line)
        if current_indent < indent:
            break
        if current_indent > indent:
            i += 1
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            break
        if ":" not in stripped:
            i += 1
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            data[key] = parse_scalar(value)
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or count_indent(lines[j]) <= current_indent:
            data[key] = {}
            i += 1
        elif is_list_item(lines[j]):
            data[key], i = parse_list(lines, j, count_indent(lines[j]))
        else:
            data[key], i = parse_mapping(lines, j, count_indent(lines[j]))
    return data, i


def parse_yaml_subset(block: str) -> dict[str, Any]:
    lines = block.splitlines()
    data, _ = parse_mapping(lines, 0, 0)
    return data


def find_frontmatter_after(text: str, start: int) -> tuple[str, int, int] | None:
    first = re.search(r"^---\s*$", text[start:], re.MULTILINE)
    if not first:
        return None
    first_start = start + first.start()
    first_end = start + first.end()
    second = re.search(r"^---\s*$", text[first_end:], re.MULTILINE)
    if second is None:
        next_heading = RECORD_HEADING_RE.search(text, first_end)
        end = next_heading.start() if next_heading else len(text)
        return text[first_end:end], first_start, end
    second_start = first_end + second.start()
    second_end = first_end + second.end()
    return text[first_end:second_start], first_start, second_end


def _apply_field_aliases(record_type: str, data: dict[str, Any]) -> None:
    aliases = FIELD_ALIASES.get(record_type)
    if not aliases:
        return
    consumed: list[str] = []
    for old_key, new_key in aliases.items():
        if old_key in data and new_key not in data:
            data[new_key] = data.pop(old_key)
            consumed.append(old_key)
    if consumed:
        data["_aliases_consumed"] = consumed


def parse_records_from_file(path: Path) -> list[Record]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    records: list[Record] = []
    for match in RECORD_HEADING_RE.finditer(text):
        if match.group("record_type") not in KNOWN_RECORD_TYPES:
            continue
        record_frontmatter = find_frontmatter_after(text, match.end())
        if record_frontmatter is None:
            continue
        block, _, _ = record_frontmatter
        data = parse_yaml_subset(block)
        heading_line = text.count("\n", 0, match.start()) + 1
        record_type = data.get("record_type") or match.group("record_type")
        _apply_field_aliases(str(record_type), data)
        id_field = ID_FIELDS.get(str(record_type))
        record_id = data.get(id_field) if id_field else None
        if not record_id:
            record_id = match.group("record_id")
        records.append(Record(str(record_type), str(record_id), path, heading_line, data))
    if path.name.startswith("v") and path.parent.name == "artifacts":
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            second = re.search(r"^---\s*$", text[4:], re.MULTILINE)
            if second:
                end = 4 + second.start()
                data = parse_yaml_subset(text[4:end])
                _apply_field_aliases(str(data.get("record_type")), data)
                if data.get("record_type") == "ArtifactVersion":
                    records.append(
                        Record(
                            "ArtifactVersion",
                            str(data.get("artifact_version_id", path.stem)),
                            path,
                            1,
                            data,
                        )
                    )
    return records


def render_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    text = str(value)
    if not text or text.startswith((" ", "{", "[", "&", "*", "#", "!", "|", ">", "@", "`")):
        return '"' + text.replace('"', '\\"') + '"'
    if text in {"-", "?", ":"} or text.startswith(("- ", "? ", ": ")):
        return '"' + text.replace('"', '\\"') + '"'
    if ":" in text and not re.match(r"^[A-Za-z]:", text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def render_yaml(data: dict[str, Any], indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(render_yaml(value, indent + 2))
        elif isinstance(value, list):
            if not value:
                lines.append(f"{prefix}{key}: []")
            else:
                lines.append(f"{prefix}{key}:")
                for item in value:
                    if isinstance(item, dict):
                        lines.append(f"{prefix}  -")
                        lines.append(render_yaml(item, indent + 4))
                    else:
                        lines.append(f"{prefix}  - {render_scalar(item)}")
        else:
            lines.append(f"{prefix}{key}: {render_scalar(value)}")
    return "\n".join(lines)


def frontmatter(data: dict[str, Any]) -> str:
    return f"---\n{render_yaml(data)}\n---"
