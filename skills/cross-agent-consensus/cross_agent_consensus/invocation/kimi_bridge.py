"""Pass a CAC stdin prompt to Kimi headless mode without changing approved argv."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys


def kimi_executable() -> str:
    configured = os.environ.get("CAC_KIMI_EXECUTABLE")
    if configured:
        return configured
    executable = shutil.which("kimi")
    if executable is None:
        raise FileNotFoundError("Kimi executable was not found on PATH")
    return executable


def parser() -> argparse.ArgumentParser:
    command_parser = argparse.ArgumentParser(
        description="Read a CAC prompt from stdin and relay Kimi stream-json output."
    )
    command_parser.add_argument("--model")
    command_parser.add_argument("--session")
    return command_parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    prompt = sys.stdin.read()
    if not prompt.strip():
        print("Kimi bridge requires a non-empty prompt on stdin", file=sys.stderr)
        return 2
    try:
        command = [kimi_executable(), "--output-format", "stream-json"]
        if args.model:
            command.extend(["--model", args.model])
        if args.session:
            command.extend(["--session", args.session])
        command.extend(["--prompt", prompt])
        completed = subprocess.run(command, check=False)
    except OSError as exc:
        print(f"Kimi bridge failed to launch provider: {exc}", file=sys.stderr)
        return 127
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
