from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.layout import (
    DEFAULT_LAYOUT,
    detect_run_layout,
    normalize_round_id,
    required_run_paths,
    round_dir,
    round_number,
)
from cross_agent_consensus.run_store import run_id_base_from_task


class RunLayoutTests(unittest.TestCase):
    def test_round_aliases_are_normalized_to_numeric_paths(self) -> None:
        run = Path("runs/example")

        # All input forms canonicalize to the short `round-N` id. The on-disk
        # directory format remains zero-padded via round_dir.
        self.assertEqual(normalize_round_id(None), "round-1")
        self.assertEqual(normalize_round_id("2"), "round-2")
        self.assertEqual(normalize_round_id("round-2"), "round-2")
        self.assertEqual(normalize_round_id("round-002"), "round-2")
        self.assertEqual(round_number("round-002"), 2)
        self.assertEqual(round_number("round-2"), 2)
        self.assertEqual(round_number("2"), 2)
        self.assertEqual(round_dir(run, "2"), run / "rounds" / "round-002")
        self.assertEqual(round_dir(run, "round-002"), run / "rounds" / "round-002")

    def test_required_paths_support_round_first_and_legacy_layouts(self) -> None:
        run = Path("runs/example")

        round_first = required_run_paths(run, DEFAULT_LAYOUT)
        legacy = required_run_paths(run, "ledger")

        self.assertIn(run / "run.md", round_first)
        self.assertIn(run / "rounds" / "round-001" / "round.md", round_first)
        self.assertIn(run / "init.md", legacy)
        self.assertIn(run / "review-batches.md", legacy)

    def test_run_id_base_preserves_existing_consensus_suffix(self) -> None:
        self.assertEqual(run_id_base_from_task("Layout simplification"), "layout-simplification-consensus")
        self.assertEqual(run_id_base_from_task("Layout simplification consensus"), "layout-simplification-consensus")

    def test_detect_run_layout_defaults_to_legacy_without_round_first_markers(self) -> None:
        self.assertEqual(detect_run_layout(Path("does-not-exist")), "ledger")


if __name__ == "__main__":
    unittest.main()
