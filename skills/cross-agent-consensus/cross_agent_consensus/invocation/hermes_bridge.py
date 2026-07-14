"""Translate Hermes quiet-mode output into CAC JSONL events."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys


SESSION_LINE_RE = re.compile(r"^session_id:\s*(\S+)\s*$")


def hermes_executable() -> str:
    """Return the configured Hermes executable or fail before provider work starts."""

    test_override = os.environ.get("CAC_HERMES_EXECUTABLE")
    executable = test_override or shutil.which("hermes")
    if not executable:
        raise FileNotFoundError("Hermes executable was not found on PATH")
    return executable


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(
        description="Read a CAC prompt from stdin and emit Hermes JSONL events."
    )
    command_parser.add_argument("--resume")
    command_parser.add_argument("--model")
    command_parser.add_argument("--provider")
    command_parser.add_argument("--toolsets")
    command_parser.add_argument("--max-turns", type=int, default=90)
    command_parser.add_argument("--source", default="tool")
    command_parser.add_argument("--ignore-rules", action="store_true")
    return command_parser


def hermes_command(args: argparse.Namespace, prompt: str) -> list[str]:
    command = [
        hermes_executable(),
        "chat",
        "--query",
        prompt,
        "--quiet",
        "--source",
        args.source,
        "--max-turns",
        str(args.max_turns),
    ]
    if args.ignore_rules:
        command.append("--ignore-rules")
    for option, value in (
        ("--model", args.model),
        ("--provider", args.provider),
        ("--toolsets", args.toolsets),
        ("--resume", args.resume),
    ):
        if value:
            command.extend([option, value])
    return command


def session_id_from_stderr(stderr_text: str) -> str | None:
    session_ids = [
        match.group(1)
        for line in stderr_text.splitlines()
        if (match := SESSION_LINE_RE.fullmatch(line.strip())) is not None
    ]
    if not session_ids:
        return None
    if len(set(session_ids)) != 1:
        raise ValueError("Hermes emitted conflicting session identifiers")
    return session_ids[0]


def stderr_without_session_lines(stderr_text: str) -> str:
    retained = [
        line
        for line in stderr_text.splitlines()
        if SESSION_LINE_RE.fullmatch(line.strip()) is None
    ]
    return "\n".join(retained).strip()


def emit_json(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    prompt = sys.stdin.read()
    if not prompt.strip():
        print("Hermes bridge requires a non-empty prompt on stdin", file=sys.stderr)
        return 2
    try:
        command = hermes_command(args, prompt)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 127
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    provider_stderr = stderr_without_session_lines(completed.stderr)
    if provider_stderr:
        print(provider_stderr, file=sys.stderr, flush=True)
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout.rstrip(), file=sys.stderr, flush=True)
        return completed.returncode
    try:
        provider_session_id = session_id_from_stderr(completed.stderr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if provider_session_id:
        emit_json(
            {
                "type": "session.started",
                "session_id": provider_session_id,
                "resumed": args.resume is not None,
            }
        )
    emit_json(
        {
            "type": "result",
            "result": completed.stdout.rstrip(),
            "session_id": provider_session_id,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
