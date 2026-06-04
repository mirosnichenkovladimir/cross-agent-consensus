from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"


class DocsConsistencyTests(unittest.TestCase):
    """SKILL.md and templates must not contradict each other on delivery rules."""

    def test_templates_prompts_does_not_ban_automation_unconditionally(self) -> None:
        """templates/prompts.md must not contain the unconditional ban that
        contradicts SKILL.md §M2 Boundary."""
        text = (PACKAGE_ROOT / "templates" / "prompts.md").read_text(encoding="utf-8")
        self.assertNotIn(
            "Do not automatically invoke external runtimes from the M2 skill",
            text,
            "templates/prompts.md still asserts an unconditional ban on automation; "
            "the supervised-CLI path in SKILL.md §M2 Boundary is the authoritative rule.",
        )

    def test_skill_description_contains_invocation_alias_phrase(self) -> None:
        """selftest --invocation depends on this exact phrase appearing in SKILL.md
        frontmatter description; any rewording must update the selftest in lockstep."""
        text = (PACKAGE_ROOT / "SKILL.md").read_text(encoding="utf-8")
        # Phrase MUST appear exactly once, inside the frontmatter description.
        matches = re.findall(r"Invocation aliases: CAC, cac", text)
        self.assertEqual(
            len(matches), 1,
            "SKILL.md must contain exactly one 'Invocation aliases: CAC, cac' phrase "
            "(used by `consensus selftest --invocation`).",
        )

    def test_skill_quick_path_section_present(self) -> None:
        """SKILL.md ships a copy-pasteable Quick Path for new operators."""
        text = (PACKAGE_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Quick Path (first 30 seconds)", text)
        # The Quick Path must include the canonical lifecycle commands and ship with
        # the required-flag set documented in the Helper Required Flags section.
        for required in [
            "scripts/consensus init",
            "scripts/consensus prompt --run",
            "scripts/consensus invocation-ready --run",
            "scripts/consensus invoke-agent --run",
            "scripts/consensus capture --run",
            "scripts/consensus normalize --run",
            "scripts/consensus terminate --run",
        ]:
            self.assertIn(required, text, f"Quick Path missing canonical command: {required}")


if __name__ == "__main__":
    unittest.main()
