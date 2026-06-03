from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.process_monitor import (
    current_process_identity,
    process_exists,
    process_identity_matches,
)


class InvocationProcessTests(unittest.TestCase):
    def test_process_identity_matches_live_process_and_not_wrong_identity(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            deadline = time.monotonic() + 2
            identity = None
            while time.monotonic() < deadline:
                identity = current_process_identity(child.pid)
                if identity is not None:
                    break
                time.sleep(0.05)
            self.assertIsNotNone(identity)
            self.assertTrue(process_exists(child.pid))
            self.assertTrue(process_identity_matches(child.pid, identity))
            wrong_identity = dict(identity or {})
            wrong_identity["pid"] = child.pid + 1
            self.assertFalse(process_identity_matches(child.pid, wrong_identity))
        finally:
            child.terminate()
            child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
