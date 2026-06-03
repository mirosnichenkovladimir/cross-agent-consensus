from __future__ import annotations

import argparse
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.capture import raw_payload_target_base


class CaptureTests(unittest.TestCase):
    def test_round_first_reviewer_raw_path_uses_actor_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "sample"
            (run / "rounds").mkdir(parents=True)
            args = argparse.Namespace(phase="reviewer", actor="Reviewer Codex", round="round-1")

            target = raw_payload_target_base(run, args)

        self.assertEqual(target, run / "rounds/round-001/raw/reviewers/reviewer-codex.out")

    def test_round_first_validator_raw_path_uses_validator_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = Path(tmp_name) / "sample"
            (run / "rounds").mkdir(parents=True)
            args = argparse.Namespace(
                phase="validator",
                actor="validator-local",
                validator_id="artifact_exists",
                round="round-1",
            )

            target = raw_payload_target_base(run, args)

        self.assertEqual(target, run / "rounds/round-001/raw/validators/artifact-exists.out")


if __name__ == "__main__":
    unittest.main()
