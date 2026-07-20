#!/usr/bin/env python3
"""Deterministic provider process used by execution-attempt conformance tests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=[
            "raw",
            "structured",
            "malformed_stream",
            "missing_final_output",
            "partial_output",
            "missing_session_id",
            "stderr",
            "nonzero",
            "delay",
            "active_delay",
            "child_process",
            "orphan_child",
            "digest_mismatch",
            "draft_reviewer",
            "resumed",
            "kimi_429_loop",
        ],
    )
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--session-id", default="fake-session-001")
    parser.add_argument("--resume")
    parser.add_argument("-p", "--print", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--output-format")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--capture-input")
    parser.add_argument("--mutate-path")
    args = parser.parse_args()
    prompt = sys.stdin.read()
    if args.mutate_path:
        Path(args.mutate_path).write_text("provider mutation\n", encoding="utf-8")
    if args.capture_input:
        Path(args.capture_input).write_text(
            json.dumps({"argv": sys.argv[1:], "stdin": prompt}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.mode == "raw":
        print(f"prompt={prompt}")
    elif args.mode == "structured":
        print(
            json.dumps(
                {
                    "type": "thread.started",
                    "thread_id": args.session_id,
                    "session_id": args.session_id,
                }
            )
        )
        print(json.dumps({"type": "result", "result": f"reviewed:{prompt}"}))
    elif args.mode == "malformed_stream":
        print("{not-json")
    elif args.mode == "missing_final_output":
        print(json.dumps({"type": "heartbeat", "session_id": args.session_id}))
    elif args.mode == "partial_output":
        print(json.dumps({"type": "item.delta", "delta": "partial answer"}))
    elif args.mode == "missing_session_id":
        print(json.dumps({"type": "result", "result": "reviewed"}))
    elif args.mode == "stderr":
        print("provider warning", file=sys.stderr)
        print("reviewed")
    elif args.mode == "nonzero":
        print("provider rejected request", file=sys.stderr)
        return 23
    elif args.mode == "delay":
        time.sleep(args.delay_seconds)
        print("reviewed")
    elif args.mode == "active_delay":
        deadline = time.monotonic() + args.delay_seconds
        while time.monotonic() < deadline:
            print(json.dumps({"type": "heartbeat"}), flush=True)
            time.sleep(0.02)
        print("reviewed")
    elif args.mode == "child_process":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        print(json.dumps({"child_pid": child.pid}), flush=True)
        time.sleep(60)
    elif args.mode == "orphan_child":
        child_code = (
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(60)"
        )
        child = subprocess.Popen([sys.executable, "-c", child_code])
        print(json.dumps({"child_pid": child.pid}), flush=True)
        time.sleep(60)
    elif args.mode == "digest_mismatch":
        print(json.dumps({"type": "result", "result": "reviewed", "input_sha256": "0" * 64}))
    elif args.mode == "draft_reviewer":
        draft = json.dumps(
            {
                "kind": "reviewer_findings",
                "review_text": "No findings.",
                "findings": [],
            },
            sort_keys=True,
        )
        print(
            json.dumps(
                {
                    "type": "thread.started",
                    "thread_id": args.session_id,
                    "session_id": args.session_id,
                }
            )
        )
        print(json.dumps({"type": "result", "result": draft}))
    elif args.mode == "resumed":
        print(json.dumps({"type": "session", "session_id": args.session_id, "resumed": True}))
        print(json.dumps({"type": "result", "result": f"resumed:{prompt}"}))
    elif args.mode == "kimi_429_loop":
        for _ in range(3):
            print(
                json.dumps(
                    {
                        "type": "turn.step.retrying",
                        "status_code": 429,
                        "delay_ms": 45000,
                    }
                ),
                flush=True,
            )
            time.sleep(0.02)
        time.sleep(args.delay_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
