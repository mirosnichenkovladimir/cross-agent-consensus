#!/usr/bin/env python3
"""Hermes quiet-mode fixture for connector conformance tests."""

from __future__ import annotations

import argparse
import os
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["chat"])
    parser.add_argument("-q", "--query", required=True)
    parser.add_argument("-Q", "--quiet", action="store_true")
    parser.add_argument("--source")
    parser.add_argument("--max-turns")
    parser.add_argument("--ignore-rules", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--provider")
    parser.add_argument("--toolsets")
    parser.add_argument("--resume")
    args = parser.parse_args()
    mode = os.environ.get("FAKE_HERMES_MODE", "complete")
    if mode == "delay":
        time.sleep(60)
    if mode == "nonzero":
        print("fake Hermes authentication failed", file=sys.stderr)
        return 19
    if mode == "conflicting_sessions":
        print("session_id: fake-hermes-001", file=sys.stderr)
        print("session_id: fake-hermes-002", file=sys.stderr)
    elif mode != "missing_session":
        provider_session_id = args.resume or "fake-hermes-001"
        if mode == "rotate_on_resume" and args.resume == "fake-hermes-001":
            provider_session_id = "fake-hermes-002"
        print(f"session_id: {provider_session_id}", file=sys.stderr)
    prefix = "resumed" if args.resume else "fresh"
    print(f"{prefix}-from-{args.resume or 'none'}:{args.query}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
