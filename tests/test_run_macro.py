"""Tests for the ``consensus run`` macro (Feature 1 / Tier 2 #4).

Covers the truth table from DESIGN.md (R2), actor resolution from real records
(R5), the prompt-finalization gate, readiness blockers, OperatorApproval
stamping under both mechanisms, per-actor failure isolation (R8), and the
manual-fallback printer round-tripping through argparse (R4).
"""

from __future__ import annotations

import argparse
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.init import build_init_files  # noqa: E402
from cross_agent_consensus.layout import required_run_paths, round_dir  # noqa: E402
from cross_agent_consensus.records import parse_run_records, records_by_type  # noqa: E402
from cross_agent_consensus.run_macro import (  # noqa: E402
    ActorPlan,
    _emit_manual_fallback,
    _fallback_lines_for_plan,
    _resolve_actors,
    cmd_run,
)


def _build_init_args(tmp: Path, *, reviewers: list[str], unattended_scope=None) -> argparse.Namespace:
    return argparse.Namespace(
        run_root=str(tmp),
        profile="document-consensus",
        validator=[],
        orchestrator="orchestrator",
        author="author",
        reviewer=reviewers,
        artifact_locator="artifact.md",
        success_criterion=[],
        task="design retry-backoff",
        run_id=None,
        max_fresh_review_rounds=1,
        max_fresh_review_rounds_without_human_approval=2,
        max_remediation_rounds=2,
        material_by_default=[],
        non_blocking_by_default=[],
        escalation_policy="policy",
        waiver_authority=None,
        unattended_invocation=bool(unattended_scope),
        unattended_scope=unattended_scope or [],
        human_supervisor="none",
        review_objective=None,
        in_scope=[],
        out_of_scope=[],
        promotion_policy=None,
        review_focus=[],
        config_resolution=None,
    )


def _stage_run(tmp: Path, *, reviewers: list[str], unattended_scope=None) -> Path:
    """Build a real round-first run folder and write per-reviewer prompts.

    Returns the run root path. Does NOT add a ConfigResolution record — tests
    that need per-reviewer ``reviewer_clis`` commands add it explicitly.
    """

    args = _build_init_args(tmp, reviewers=reviewers, unattended_scope=unattended_scope)
    run_id = "sample-consensus-001"
    files = build_init_files(args, run_id, "2026-06-04T00:00:00Z")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    run = tmp / run_id
    for path in required_run_paths(run):
        if path.suffix:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text("", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
    # Add finalized per-reviewer prompts so _finalize_prompts is a no-op
    prompt_dir = round_dir(run, "round-1") / "prompts" / "reviewers"
    for reviewer in reviewers:
        (prompt_dir / f"{reviewer}.md").write_text(f"# Prompt for {reviewer}\n", encoding="utf-8")
    return run


def _append_config_resolution(run: Path, reviewer_commands: dict[str, list[str]]) -> None:
    """Append a minimal ConfigResolution record exposing per-reviewer commands."""

    from cross_agent_consensus.markdown_records import frontmatter

    effective = {
        f"reviewer_clis.{reviewer}.command": {"value": command, "source_layer": "test"}
        for reviewer, command in reviewer_commands.items()
    }
    data = {
        "record_type": "ConfigResolution",
        "schema_version": "m2-markdown-1",
        "run_id": run.name,
        "actor_identity": "orchestrator-config-tool",
        "created_at": "2026-06-04T00:00:00Z",
        "config_resolution_id": f"config-resolution-{run.name}",
        "config_schema_version": "v1",
        "sources": [],
        "effective_values": effective,
        "diagnostics": {"warnings": [], "errors": []},
        "redactions": [],
    }
    target = run / "run.md"
    existing = target.read_text(encoding="utf-8")
    block = "\n\n## ConfigResolution config-resolution-test\n" + frontmatter(data) + "\n"
    target.write_text(existing + block, encoding="utf-8")


def _run_args(run: Path, **overrides) -> argparse.Namespace:
    base = dict(
        run=str(run),
        round="round-001",
        phase="reviewer",
        actors=None,
        execute_reviewers=False,
        approved=False,
        sequential=False,
        cwd=".",
        idle_timeout_seconds=30.0,
        stale_timeout_seconds=60.0,
        heartbeat_interval_seconds=1.0,
        operator_identity=None,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _capture_run(args: argparse.Namespace) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cmd_run(args)
    return rc, out.getvalue(), err.getvalue()


class ResolveActorsTests(unittest.TestCase):

    def test_reviewer_phase_uses_participants_reviewer_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex", "claude"])
            records = parse_run_records(run)
            actors = _resolve_actors(records, round_id="round-1", phase="reviewer", requested=None)
        self.assertEqual(sorted(actors), ["claude", "codex"])

    def test_validator_phase_uses_policy_required_validators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex"])
            records = parse_run_records(run)
            actors = _resolve_actors(records, round_id="round-1", phase="validator", requested=None)
        # document-consensus profile injects a default validator set
        self.assertGreater(len(actors), 0)

    def test_author_phase_uses_participants_author(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex"])
            records = parse_run_records(run)
            actors = _resolve_actors(records, round_id="round-1", phase="author", requested=None)
        self.assertEqual(actors, ["author"])

    def test_requested_override_short_circuits(self) -> None:
        actors = _resolve_actors([], round_id="round-1", phase="reviewer", requested=["explicit"])
        self.assertEqual(actors, ["explicit"])


class FallbackPrinterTests(unittest.TestCase):

    def _plan(self, runtime_command: list[str]) -> ActorPlan:
        return ActorPlan(
            actor="codex",
            player="codex-cli",
            phase="reviewer",
            round_id="round-001",
            prompt_path=Path("rounds/round-001/prompts/reviewers/codex.md"),
            raw_output_path=Path("rounds/round-001/raw/reviewers/codex.out"),
            cwd=".",
            runtime_command=runtime_command,
            idle_timeout_seconds=30.0,
            stale_timeout_seconds=60.0,
            heartbeat_interval_seconds=1.0,
            review_batch_id="review-batch-round-1-fresh_review",
            artifact_version_id="v1",
        )

    def test_fallback_round_trips_through_invoke_argparse(self) -> None:
        """R4: every printed fallback line parses through argparse without error."""
        from cross_agent_consensus.cli import build_parser
        from cross_agent_consensus.invocation.readiness import normalize_command_separator

        plan = self._plan(["codex", "exec", "--json", "-"])
        lines = _fallback_lines_for_plan(plan)
        self.assertEqual(len(lines), 1)
        body = lines[0].split(" → ", 1)[1].replace(" \\\n              ", " ")
        self.assertTrue(body.startswith("scripts/consensus invoke-agent"))
        raw_argv = body.split()[1:]  # drop "scripts/consensus"
        # The fallback omits --run because it's supplied by the operator; inject for the parser smoke
        argv = ["invoke-agent", "--run", "."] + raw_argv[1:]
        # main() runs argv through normalize_command_separator before parse — mirror that
        argv = normalize_command_separator(argv)
        parser = build_parser()
        parsed = parser.parse_args(argv)
        self.assertEqual(parsed.actor, "codex")
        self.assertEqual(parsed.player, "codex-cli")
        self.assertEqual(parsed.phase, "reviewer")
        self.assertTrue(parsed.approved)

    def test_fallback_emits_marker_when_no_runtime_command(self) -> None:
        plan = self._plan([])
        out = io.StringIO()
        with redirect_stdout(out):
            _emit_manual_fallback([plan])
        self.assertIn("REQUIRED: argv for the player CLI", out.getvalue())


class DryRunTests(unittest.TestCase):

    def test_dry_run_with_clean_readiness_exits_zero(self) -> None:
        """No --execute-reviewers + valid setup → dry-run prints plan and exits 0."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex", "claude"])
            _append_config_resolution(run, {"codex": ["codex", "exec", "--json", "-"], "claude": ["claude", "-p", "--verbose", "--output-format", "stream-json"]})
            rc, stdout, _stderr = _capture_run(_run_args(run))
        self.assertEqual(rc, 0, stdout)
        self.assertIn("manual fallback commands", stdout)
        self.assertIn("codex", stdout)
        self.assertIn("claude", stdout)

    def test_dry_run_with_missing_runtime_command_exits_3(self) -> None:
        """No runtime command for an actor → readiness blocker → dry-run exits 3."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex"])
            # no ConfigResolution → no runtime command
            rc, stdout, _stderr = _capture_run(_run_args(run))
        self.assertEqual(rc, 3, stdout)
        self.assertIn("no runtime command configured", stdout)


class ExecutionTests(unittest.TestCase):

    def test_execute_without_approved_exits_one_and_prints_manual(self) -> None:
        """Truth-table row: --approved=no → exits 1, prints manual commands."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex"])
            _append_config_resolution(run, {"codex": ["codex", "exec", "--json", "-"]})
            rc, stdout, stderr = _capture_run(_run_args(run, execute_reviewers=True, approved=False))
        self.assertEqual(rc, 1)
        self.assertIn("requires --approved", stderr)
        self.assertIn("manual fallback commands", stdout)

    def test_execute_approved_no_policy_stamps_cli_approved_flag(self) -> None:
        """Truth-table row: --approved=yes, no scoped policy → mechanism=cli_approved_flag."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex"])
            _append_config_resolution(run, {"codex": ["codex", "exec", "--json", "-"]})
            with (
                mock.patch("cross_agent_consensus.invocation.process_monitor.cmd_invoke_agent", return_value=0) as m_invoke,
                mock.patch("cross_agent_consensus.cli.cmd_capture", return_value=0) as m_capture,
            ):
                # invoke-agent is expected to write the raw-output mirror; simulate it
                def fake_invoke(args):
                    Path(args.raw_output).parent.mkdir(parents=True, exist_ok=True)
                    Path(args.raw_output).write_text("ok\n", encoding="utf-8")
                    return 0
                m_invoke.side_effect = fake_invoke
                rc, stdout, _stderr = _capture_run(_run_args(run, execute_reviewers=True, approved=True))
                self.assertEqual(rc, 0, stdout)
                approval_path = round_dir(run, "round-001") / "operator-approval.md"
                self.assertTrue(approval_path.is_file(), stdout)
                text = approval_path.read_text(encoding="utf-8")
                self.assertIn("mechanism: cli_approved_flag", text)
                self.assertEqual(m_invoke.call_count, 1)
                self.assertEqual(m_capture.call_count, 1)

    def test_execute_approved_with_scoped_policy_stamps_policy_unattended(self) -> None:
        """Truth-table row: --approved=yes, scoped policy matches → mechanism=policy_unattended."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(
                tmp,
                reviewers=["codex"],
                unattended_scope=["phase:reviewer", "actor:codex"],
            )
            _append_config_resolution(run, {"codex": ["codex", "exec", "--json", "-"]})
            with (
                mock.patch("cross_agent_consensus.invocation.process_monitor.cmd_invoke_agent") as m_invoke,
                mock.patch("cross_agent_consensus.cli.cmd_capture", return_value=0),
            ):
                def fake_invoke(args):
                    Path(args.raw_output).parent.mkdir(parents=True, exist_ok=True)
                    Path(args.raw_output).write_text("ok\n", encoding="utf-8")
                    return 0
                m_invoke.side_effect = fake_invoke
                rc, stdout, _stderr = _capture_run(
                    _run_args(run, execute_reviewers=True, approved=True)
                )
                self.assertEqual(rc, 0, stdout)
                approval_text = (round_dir(run, "round-001") / "operator-approval.md").read_text(encoding="utf-8")
                self.assertIn("mechanism: policy_unattended", approval_text)

    def test_failed_invoke_passes_no_append_record_to_capture(self) -> None:
        """R8: failed session → capture is called with no_append_record=True; siblings still run."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex", "claude"])
            _append_config_resolution(
                run,
                {"codex": ["codex", "exec", "--json", "-"], "claude": ["claude", "-p", "--verbose", "--output-format", "stream-json"]},
            )
            seen_no_append: dict[str, bool] = {}

            def fake_invoke(args):
                Path(args.raw_output).parent.mkdir(parents=True, exist_ok=True)
                Path(args.raw_output).write_text("partial\n", encoding="utf-8")
                # codex fails, claude succeeds
                return 1 if args.actor == "codex" else 0

            def fake_capture(args):
                seen_no_append[args.actor] = bool(args.no_append_record)
                return 0

            with (
                mock.patch(
                    "cross_agent_consensus.invocation.process_monitor.cmd_invoke_agent",
                    side_effect=fake_invoke,
                ),
                mock.patch("cross_agent_consensus.cli.cmd_capture", side_effect=fake_capture),
            ):
                rc, stdout, _stderr = _capture_run(
                    _run_args(run, execute_reviewers=True, approved=True, sequential=True)
                )
        self.assertEqual(rc, 1, stdout)  # overall failure because codex failed
        self.assertTrue(seen_no_append["codex"])  # failure → no_append_record=True
        self.assertFalse(seen_no_append["claude"])  # success → record appended

    def test_readiness_blocker_aborts_before_any_launch(self) -> None:
        """If invocation-ready fails for any actor, no launches happen."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex", "claude"])
            # Only codex has a runtime command; claude is missing → blocker
            _append_config_resolution(run, {"codex": ["codex", "exec", "--json", "-"]})
            with (
                mock.patch("cross_agent_consensus.invocation.process_monitor.cmd_invoke_agent") as m_invoke,
                mock.patch("cross_agent_consensus.cli.cmd_capture") as m_capture,
            ):
                rc, _stdout, stderr = _capture_run(
                    _run_args(run, execute_reviewers=True, approved=True)
                )
        self.assertEqual(rc, 3)
        self.assertIn("aborting before any launch", stderr)
        self.assertEqual(m_invoke.call_count, 0)
        self.assertEqual(m_capture.call_count, 0)


if __name__ == "__main__":
    unittest.main()
