"""Self-diagnostic checks for the installed CAC skill package.

Confirms that the skill is present at each detected host install path
and that SKILL.md still carries the literal alias phrase the LLM routers
use to resolve `cac:` triggers.

Routing across Claude Code, Codex, Hermes, and Kimi Code is LLM-based — there is no
authoritative "trigger -> skill" registry to write into. This module's job is
to surface what we CAN check (install location + manifest integrity +
description-phrase presence) and to give the operator a deterministic
exit code per host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from cross_agent_consensus.io import atomic_write_text, eprint


REQUIRED_DESCRIPTION_PHRASE = "Invocation aliases: CAC, cac"

HOST_INSTALL_ROOTS: dict[str, list[Path]] = {
    # Per-host conventions. The first existing entry wins.
    "claude": [Path.home() / ".claude" / "skills" / "cross-agent-consensus"],
    "codex": [Path.home() / ".codex" / "skills" / "cross-agent-consensus"],
    "hermes": [Path.home() / ".hermes" / "skills" / "cross-agent-consensus"],
    "kimi": [Path.home() / ".kimi-code" / "skills" / "cross-agent-consensus"],
}

PROJECT_RULE_BEGIN = "<!-- cac:begin -->"
PROJECT_RULE_END = "<!-- cac:end -->"


@dataclass
class HostReport:
    host: str
    install_path: Path | None
    detected: bool
    healthy: bool
    messages: list[str] = field(default_factory=list)


def _existing_install(host: str, override_home: Path | None = None) -> Path | None:
    if override_home is not None:
        candidates = [override_home / candidate.relative_to(Path.home()) for candidate in HOST_INSTALL_ROOTS[host]]
    else:
        candidates = HOST_INSTALL_ROOTS[host]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _parse_frontmatter_description(skill_md_path: Path) -> str | None:
    text = skill_md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end_marker = text.find("\n---", 3)
    if end_marker == -1:
        return None
    frontmatter = text[3:end_marker]
    match = re.search(r'^description:\s*"([^"]*)"', frontmatter, flags=re.MULTILINE)
    if not match:
        match = re.search(r"^description:\s*'([^']*)'", frontmatter, flags=re.MULTILINE)
    if not match:
        match = re.search(r"^description:\s*(.+)$", frontmatter, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


INSTALLED_STATE_FILENAME = ".cross-agent-consensus-managed.json"


def _check_manifest_integrity(install_path: Path) -> list[str]:
    """Verify the installed package against the installer's state file.

    `scripts/install-cac` writes `.cross-agent-consensus-managed.json` after a
    successful install, recording the sha256 of each file as it was placed on
    disk. That is the integrity contract — re-hash each managed file in place
    and compare against `installed_sha256`.
    """
    state_path = install_path / INSTALLED_STATE_FILENAME
    if not state_path.is_file():
        return [f"{INSTALLED_STATE_FILENAME} not found at {install_path}"]
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{INSTALLED_STATE_FILENAME} failed to parse: {exc}"]
    messages: list[str] = []
    for item in state.get("managed_files", []):
        rel = item.get("path")
        declared = item.get("installed_sha256")
        if not rel or not declared:
            messages.append(f"state entry missing path/installed_sha256: {item}")
            continue
        target = install_path / rel
        if not target.is_file():
            messages.append(f"managed file missing on disk: {rel}")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != declared:
            messages.append(f"managed file hash mismatch: {rel}")
    return messages


def _check_hermes_enabled(install_path: Path) -> list[str]:
    """Best-effort Hermes-specific check; returns warnings list."""
    hermes_bin = shutil.which("hermes")
    if not hermes_bin:
        return []
    env = os.environ.copy()
    # Hermes renders ``skills list`` as a terminal-width-aware Rich table.
    # Narrow captured output truncates ``cross-agent-consensus`` and causes a
    # false missing-skill report.  Request a wide enabled-only view so the
    # exact installed name survives non-interactive capture.
    env["COLUMNS"] = "240"
    try:
        completed = subprocess.run(
            [hermes_bin, "skills", "list", "--enabled-only"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"hermes skills list failed: {exc}"]
    if completed.returncode != 0:
        return [f"hermes skills list exit {completed.returncode}: {completed.stderr.strip()}"]
    # Do not depend on table borders or column ordering. ``--enabled-only``
    # makes an exact-name match sufficient.
    matches = [line for line in completed.stdout.splitlines() if "cross-agent-consensus" in line]
    if not matches:
        return ["cross-agent-consensus is not listed as enabled by Hermes; run `hermes skills config`"]
    return []


def _check_host(host: str, override_home: Path | None) -> HostReport:
    install_path = _existing_install(host, override_home=override_home)
    if install_path is None:
        return HostReport(host=host, install_path=None, detected=False, healthy=False, messages=[])
    messages: list[str] = []
    skill_md = install_path / "SKILL.md"
    if not skill_md.is_file():
        messages.append("SKILL.md missing from installed skill directory")
    else:
        description = _parse_frontmatter_description(skill_md)
        if description is None:
            messages.append("SKILL.md frontmatter is missing the `description` field")
        elif REQUIRED_DESCRIPTION_PHRASE not in description:
            messages.append(
                f"SKILL.md description does not contain the routing phrase "
                f"'{REQUIRED_DESCRIPTION_PHRASE}'; LLM routing on `cac:` will be unreliable"
            )
    messages.extend(_check_manifest_integrity(install_path))
    if host == "hermes":
        messages.extend(_check_hermes_enabled(install_path))
    return HostReport(
        host=host,
        install_path=install_path,
        detected=True,
        healthy=not messages,
        messages=messages,
    )


def _print_report(reports: list[HostReport]) -> None:
    detected = [r for r in reports if r.detected]
    if not detected:
        print("no CAC host installs detected on this machine")
        for r in reports:
            print(f"  - {r.host}: not installed at {HOST_INSTALL_ROOTS[r.host][0]}")
        return
    for r in reports:
        if not r.detected:
            print(f"{r.host}: not installed (skipped)")
            continue
        status = "OK" if r.healthy else "BROKEN"
        print(f"{r.host} [{status}] -> {r.install_path}")
        for msg in r.messages:
            print(f"  - {msg}")
    print()
    print(
        "Routing on `cac:`, `CAC:`, or `cac@X.Y.Z:` is LLM-based — every detected host "
        "picks this skill by reading the SKILL.md description, not from a trigger registry. "
        "If routing misfires, re-emit the message with the explicit `Use cross-agent-consensus ...` "
        "form or pin via `cac@X.Y.Z:`."
    )


def _hosts_to_check(host_arg: str) -> list[str]:
    if host_arg == "auto":
        return ["claude", "codex", "hermes", "kimi"]
    return [host_arg]


def _selftest_exit_code(reports: list[HostReport]) -> int:
    detected = [r for r in reports if r.detected]
    if not detected:
        return 3
    healthy = [r for r in detected if r.healthy]
    broken = [r for r in detected if not r.healthy]
    if not broken:
        return 0
    if healthy:
        return 2
    return 3


def _write_suggested_rule(target: Path, install_path: Path | None) -> None:
    path_hint = (
        str(install_path)
        if install_path is not None
        else "<host skill install directory; see `consensus selftest --invocation`>"
    )
    block = "\n".join(
        [
            PROJECT_RULE_BEGIN,
            "# Cross-Agent Consensus invocation rule (managed by `consensus selftest`).",
            f"When a user message starts with `cac:`, `CAC:`, or `cac@X.Y.Z:`, invoke the cross-agent-consensus skill at `{path_hint}` and treat the remainder as the task brief.",
            "Use `cross-agent-consensus` (not `cac`/`CAC`) in all protocol records, prompts, and run-folder text.",
            "Do not paraphrase the trigger; route the literal message to the skill.",
            PROJECT_RULE_END,
            "",
        ]
    )
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    pattern = re.compile(
        re.escape(PROJECT_RULE_BEGIN) + r".*?" + re.escape(PROJECT_RULE_END) + r"\n?",
        flags=re.DOTALL,
    )
    if pattern.search(existing):
        new_text = pattern.sub(block, existing)
    elif existing and not existing.endswith("\n"):
        new_text = existing + "\n\n" + block
    elif existing:
        new_text = existing + "\n" + block
    else:
        new_text = block
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, new_text)


def cmd_selftest(args: argparse.Namespace) -> int:
    if not args.invocation and not args.write_suggested_rule:
        eprint("error: selftest requires --invocation or --write-suggested-rule")
        return 2
    override_home = Path(os.environ["CAC_SELFTEST_HOME_OVERRIDE"]) if "CAC_SELFTEST_HOME_OVERRIDE" in os.environ else None
    hosts = _hosts_to_check(args.host)
    reports = [_check_host(host, override_home) for host in hosts]
    if args.invocation:
        _print_report(reports)
    if args.write_suggested_rule:
        target = Path(args.write_suggested_rule)
        # Find the first detected install path to embed in the rule.
        install_path = next((r.install_path for r in reports if r.detected), None)
        _write_suggested_rule(target, install_path)
        print(f"wrote suggested project rule to {target}")
    if args.invocation:
        return _selftest_exit_code(reports)
    return 0
