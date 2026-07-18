#!/usr/bin/env python3
"""Kimi Code CLI fixture for connector conformance tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-V", "--version", action="store_true")
    parser.add_argument("-m", "--model")
    parser.add_argument("-p", "--prompt")
    parser.add_argument("--output-format")
    parser.add_argument("-S", "--session")
    args = parser.parse_args()
    if args.version:
        print("0.27.0")
        return 0
    mode = os.environ.get("FAKE_KIMI_MODE", "complete")
    if mode == "delay":
        time.sleep(60)
    if mode == "nonzero":
        print("fake Kimi authentication failed", file=sys.stderr)
        return 19
    if mode == "malformed":
        print("{not-json")
        return 0
    if args.output_format != "stream-json":
        print("stream-json is required", file=sys.stderr)
        return 2
    if not args.prompt:
        print("prompt is required", file=sys.stderr)
        return 2
    session_id = args.session or "fake-kimi-001"
    emit(
        {
            "role": "assistant",
            "content": (
                f"model={args.model or 'default'};session={session_id};"
                f"prompt={args.prompt}"
            ),
        }
    )
    if mode == "conflicting_sessions":
        emit(
            {
                "role": "meta",
                "type": "session.resume_hint",
                "session_id": "fake-kimi-002",
            }
        )
    if mode != "missing_session":
        emit(
            {
                "role": "meta",
                "type": "session.resume_hint",
                "session_id": session_id,
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
