"""Tests for ``policy_allows_unattended_scoped`` (R1).

The legacy ``policy_allows_unattended`` is enabled-only; the scoped variant
must fail closed on missing scope, malformed tokens, and unknown keys.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.readiness import (  # noqa: E402
    policy_allows_unattended_scoped,
)
from cross_agent_consensus.models import Record  # noqa: E402


def _policy_record(data: dict) -> Record:
    return Record(
        record_type="Policy",
        record_id="policy-test",
        path=Path("/tmp/policy.md"),
        heading_line=1,
        data={"record_type": "Policy", **data},
    )


def _scoped(records, **ctx) -> bool:
    return policy_allows_unattended_scoped(
        records,
        run_id=ctx.get("run_id", "run-1"),
        round_id=ctx.get("round_id", "round-001"),
        phase=ctx.get("phase", "reviewer"),
        actor=ctx.get("actor", "codex"),
    )


class ScopedPolicyTests(unittest.TestCase):

    def test_missing_policy_returns_false(self) -> None:
        self.assertFalse(_scoped([]))

    def test_missing_unattended_returns_false(self) -> None:
        self.assertFalse(_scoped([_policy_record({})]))

    def test_bare_true_is_global(self) -> None:
        """Bare ``unattended_invocation: true`` allows any scope (matches docs)."""
        records = [_policy_record({"unattended_invocation": True})]
        self.assertTrue(_scoped(records, phase="reviewer", actor="codex"))
        self.assertTrue(_scoped(records, phase="validator", actor="anyone"))

    def test_dict_without_enabled_returns_false(self) -> None:
        records = [_policy_record({"unattended_invocation": {"scope": ["phase:reviewer"]}})]
        self.assertFalse(_scoped(records))

    def test_dict_enabled_without_scope_returns_false(self) -> None:
        """Fail-closed: dict form REQUIRES a scope (R1)."""
        records = [_policy_record({"unattended_invocation": {"enabled": True}})]
        self.assertFalse(_scoped(records))

    def test_list_scope_matches(self) -> None:
        records = [
            _policy_record(
                {"unattended_invocation": {"enabled": True, "scope": ["phase:reviewer", "actor:codex"]}}
            )
        ]
        self.assertTrue(_scoped(records, phase="reviewer", actor="codex"))
        self.assertFalse(_scoped(records, phase="reviewer", actor="claude"))
        self.assertFalse(_scoped(records, phase="validator", actor="codex"))

    def test_list_scope_multi_value_for_same_key(self) -> None:
        records = [
            _policy_record(
                {"unattended_invocation": {"enabled": True, "scope": ["actor:codex", "actor:claude"]}}
            )
        ]
        self.assertTrue(_scoped(records, actor="codex"))
        self.assertTrue(_scoped(records, actor="claude"))
        self.assertFalse(_scoped(records, actor="hermes"))

    def test_list_scope_malformed_token_fails_closed(self) -> None:
        records = [_policy_record({"unattended_invocation": {"enabled": True, "scope": ["no-colon-token"]}})]
        self.assertFalse(_scoped(records))

    def test_list_scope_unknown_key_fails_closed(self) -> None:
        records = [_policy_record({"unattended_invocation": {"enabled": True, "scope": ["nonsense:value"]}})]
        self.assertFalse(_scoped(records))

    def test_dict_scope_matches(self) -> None:
        records = [
            _policy_record(
                {
                    "unattended_invocation": {
                        "enabled": True,
                        "scope": {"phases": ["reviewer"], "actors": ["codex", "claude"]},
                    }
                }
            )
        ]
        self.assertTrue(_scoped(records, phase="reviewer", actor="codex"))
        self.assertTrue(_scoped(records, phase="reviewer", actor="claude"))
        self.assertFalse(_scoped(records, phase="reviewer", actor="hermes"))
        self.assertFalse(_scoped(records, phase="validator", actor="codex"))

    def test_dict_scope_unknown_key_fails_closed(self) -> None:
        records = [
            _policy_record(
                {"unattended_invocation": {"enabled": True, "scope": {"nonsense": ["whatever"]}}}
            )
        ]
        self.assertFalse(_scoped(records))


if __name__ == "__main__":
    unittest.main()
