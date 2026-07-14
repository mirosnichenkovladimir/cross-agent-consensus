from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.invocation.selftest import (  # noqa: E402
    PROJECT_RULE_BEGIN,
    PROJECT_RULE_END,
    REQUIRED_DESCRIPTION_PHRASE,
    _check_hermes_enabled,
    _selftest_exit_code,
    cmd_selftest,
)


class _Args:
    def __init__(self, **kwargs):
        self.invocation = kwargs.get("invocation", False)
        self.host = kwargs.get("host", "auto")
        self.write_suggested_rule = kwargs.get("write_suggested_rule", None)


def _stage_install(home: Path, host: str, *, description: str | None = None, broken_manifest: bool = False) -> Path:
    """Stage a minimal CAC install at <home>/.<host>/skills/cross-agent-consensus."""
    install = home / f".{host}" / "skills" / "cross-agent-consensus"
    install.mkdir(parents=True)
    if description is None:
        description = f"Auditable cross-agent consensus. {REQUIRED_DESCRIPTION_PHRASE}."
    skill_md_lines = [
        "---",
        "name: cross-agent-consensus",
        f'description: "{description}"',
        "---",
        "",
        "# Cross-Agent Consensus",
        "",
    ]
    skill_md = install / "SKILL.md"
    skill_md.write_text("\n".join(skill_md_lines), encoding="utf-8")
    skill_md_source_hash = hashlib.sha256(skill_md.read_bytes()).hexdigest()
    skill_md_installed_hash = skill_md_source_hash if not broken_manifest else "0" * 64
    state = {
        "package": "cross-agent-consensus",
        "installed_at": "2026-01-01T00:00:00Z",
        "managed_files": [
            {
                "path": "SKILL.md",
                "installed_sha256": skill_md_installed_hash,
                "source_sha256": skill_md_source_hash,
            },
        ],
    }
    (install / ".cross-agent-consensus-managed.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )
    return install


class SelftestTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["CAC_SELFTEST_HOME_OVERRIDE"] = str(self.home)

    def tearDown(self):
        os.environ.pop("CAC_SELFTEST_HOME_OVERRIDE", None)
        self._tmp.cleanup()

    def _capture(self, args: _Args) -> tuple[int, str, str]:
        import io
        from contextlib import redirect_stderr, redirect_stdout

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cmd_selftest(args)
        return rc, out.getvalue(), err.getvalue()

    def test_selftest_with_no_install_returns_3(self) -> None:
        rc, _stdout, _stderr = self._capture(_Args(invocation=True, host="auto"))
        self.assertEqual(rc, 3)

    def test_selftest_with_healthy_claude_returns_0(self) -> None:
        _stage_install(self.home, "claude")
        rc, stdout, _err = self._capture(_Args(invocation=True, host="claude"))
        self.assertEqual(rc, 0, stdout)
        self.assertIn("[OK]", stdout)

    def test_selftest_missing_alias_phrase_returns_3(self) -> None:
        _stage_install(self.home, "claude", description="Cross-agent consensus only, nothing else.")
        rc, stdout, _err = self._capture(_Args(invocation=True, host="claude"))
        self.assertEqual(rc, 3)
        self.assertIn("Invocation aliases: CAC, cac", stdout)

    def test_selftest_manifest_hash_mismatch_returns_3(self) -> None:
        _stage_install(self.home, "claude", broken_manifest=True)
        rc, stdout, _err = self._capture(_Args(invocation=True, host="claude"))
        self.assertEqual(rc, 3)
        self.assertIn("hash mismatch", stdout)

    def test_selftest_mixed_healthy_and_broken_returns_2(self) -> None:
        """Healthy on one host, broken on another detected host → exit 2."""
        _stage_install(self.home, "claude")
        _stage_install(self.home, "codex", broken_manifest=True)
        rc, stdout, _err = self._capture(_Args(invocation=True, host="auto"))
        self.assertEqual(rc, 2, stdout)
        self.assertIn("[OK]", stdout)
        self.assertIn("[BROKEN]", stdout)

    def test_selftest_skips_undetected_hosts_silently(self) -> None:
        """When only one host is present, undetected hosts are reported as 'not installed'
        but do not flip the exit code from 0."""
        _stage_install(self.home, "claude")
        rc, stdout, _err = self._capture(_Args(invocation=True, host="auto"))
        self.assertEqual(rc, 0, stdout)
        self.assertIn("not installed (skipped)", stdout)

    @patch("cross_agent_consensus.invocation.selftest.shutil.which", return_value="/usr/bin/hermes")
    @patch("cross_agent_consensus.invocation.selftest.subprocess.run")
    def test_hermes_check_requests_wide_enabled_only_output(self, run, _which) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = "│ cross-agent-consensus │ local │ enabled │\n"
        run.return_value.stderr = ""

        self.assertEqual(_check_hermes_enabled(self.home), [])
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["/usr/bin/hermes", "skills", "list", "--enabled-only"])
        self.assertEqual(kwargs["env"]["COLUMNS"], "240")

    @patch("cross_agent_consensus.invocation.selftest.shutil.which", return_value="/usr/bin/hermes")
    @patch("cross_agent_consensus.invocation.selftest.subprocess.run")
    def test_hermes_check_reports_skill_missing_from_enabled_list(self, run, _which) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = "│ another-skill │ local │ enabled │\n"
        run.return_value.stderr = ""

        messages = _check_hermes_enabled(self.home)

        self.assertEqual(
            messages,
            ["cross-agent-consensus is not listed as enabled by Hermes; run `hermes skills config`"],
        )

    def test_write_suggested_rule_on_empty_file_is_idempotent(self) -> None:
        target = self.home / "project.rules.md"
        rc1, _o1, _e1 = self._capture(_Args(write_suggested_rule=str(target)))
        self.assertEqual(rc1, 0)
        before = target.read_text(encoding="utf-8")
        self.assertIn(PROJECT_RULE_BEGIN, before)
        self.assertIn(PROJECT_RULE_END, before)
        rc2, _o2, _e2 = self._capture(_Args(write_suggested_rule=str(target)))
        self.assertEqual(rc2, 0)
        self.assertEqual(target.read_text(encoding="utf-8"), before, "rule writer must be idempotent")

    def test_write_suggested_rule_preserves_user_content(self) -> None:
        target = self.home / "PROJECT.md"
        target.write_text("# Project rules\n\nDo not delete me.\n", encoding="utf-8")
        rc, _o, _e = self._capture(_Args(write_suggested_rule=str(target)))
        self.assertEqual(rc, 0)
        text = target.read_text(encoding="utf-8")
        self.assertIn("Do not delete me.", text)
        self.assertIn(PROJECT_RULE_BEGIN, text)

    def test_no_flags_returns_2(self) -> None:
        rc, _o, _e = self._capture(_Args())
        self.assertEqual(rc, 2)

    def test_exit_code_helper_classifies(self) -> None:
        from cross_agent_consensus.invocation.selftest import HostReport

        none = [HostReport("a", None, False, False)]
        self.assertEqual(_selftest_exit_code(none), 3)

        all_healthy = [HostReport("a", Path("/x"), True, True)]
        self.assertEqual(_selftest_exit_code(all_healthy), 0)

        broken_only = [HostReport("a", Path("/x"), True, False)]
        self.assertEqual(_selftest_exit_code(broken_only), 3)

        mixed = [HostReport("a", Path("/x"), True, True), HostReport("b", Path("/y"), True, False)]
        self.assertEqual(_selftest_exit_code(mixed), 2)


if __name__ == "__main__":
    unittest.main()
