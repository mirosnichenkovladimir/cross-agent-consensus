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
    _build_plan,
    _emit_manual_fallback,
    _fallback_lines_for_plan,
    _invocation_profile_for_actor,
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
        artifact_locator=str(tmp / "artifact.md"),
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
    (tmp / "artifact.md").write_text("reviewed artifact\n", encoding="utf-8")
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
        "schema_version": "m2-markdown-2",
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


def _append_batch_scoped_review(run: Path) -> tuple[str, Path, Path]:
    """Add a second same-round batch and return its expected evidence paths."""

    from cross_agent_consensus.markdown_records import frontmatter

    artifact_data = {
        "record_type": "ArtifactVersion",
        "schema_version": "m2-markdown-2",
        "run_id": run.name,
        "actor_identity": "author",
        "created_at": "2026-06-04T00:01:00Z",
        "artifact_version_id": "v2",
        "predecessor_id_or_null": "v1",
        "content_locator": "artifact-v2.md",
        "content_hash_or_null": None,
        "produced_by": "author",
    }
    (run / "artifacts" / "v2.md").write_text(
        frontmatter(artifact_data) + "\n\n# Artifact Version v2\n",
        encoding="utf-8",
    )
    (run / "artifact-v2.md").write_text("revised artifact\n", encoding="utf-8")
    batch_id = "review-batch-round-1-remediation_verification"
    batch_data = {
        "record_type": "ReviewBatch",
        "schema_version": "m2-markdown-2",
        "run_id": run.name,
        "actor_identity": "orchestrator",
        "created_at": "2026-06-04T00:02:00Z",
        "review_batch_id": batch_id,
        "review_scope_id": f"review-scope-{run.name}",
        "review_mode": "remediation_verification",
        "target_artifact_version_id": "v2",
        "source_finding_ids": ["normalized-finding-001"],
        "round_id": "round-1",
        "round_path": "rounds/round-001",
        "expected_reviewer_identities": ["codex"],
    }
    round_path = round_dir(run, "round-1") / "round.md"
    round_path.write_text(
        round_path.read_text(encoding="utf-8")
        + f"\n\n## ReviewBatch {batch_id}\n"
        + frontmatter(batch_data)
        + "\n",
        encoding="utf-8",
    )
    batch_component = "review-batch-round-1-remediation-verification"
    prompt = round_dir(run, "round-1") / "prompts" / "reviewers" / batch_component / "codex.md"
    raw_output = round_dir(run, "round-1") / "raw" / "reviewers" / batch_component / "codex.out"
    return batch_id, prompt, raw_output


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

    def test_reviewer_phase_matches_padded_batch_round_and_expected_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _stage_run(Path(tmp_name), reviewers=["codex", "claude"])
            round_record = round_dir(run, "round-1") / "round.md"
            round_record.write_text(
                round_record.read_text(encoding="utf-8")
                .replace("round_id: round-1", "round_id: round-001")
                .replace("source_finding_ids: []", "source_finding_ids: []\nexpected_reviewer_identities: [codex]"),
                encoding="utf-8",
            )

            actors = _resolve_actors(
                parse_run_records(run),
                round_id="round-1",
                phase="reviewer",
                requested=None,
            )

        self.assertEqual(actors, ["codex"])

    def test_validator_phase_uses_participant_validator_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex"])
            run_md = run / "run.md"
            run_md.write_text(
                run_md.read_text(encoding="utf-8").replace(
                    "validator_identities: []",
                    "validator_identities:\n  - validator-cli",
                ),
                encoding="utf-8",
            )
            records = parse_run_records(run)
            actors = _resolve_actors(records, round_id="round-1", phase="validator", requested=None)
        self.assertEqual(actors, ["validator-cli"])

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


class LegacyProfileResolutionTests(unittest.TestCase):
    def test_legacy_codex_and_claude_commands_keep_structured_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _stage_run(Path(tmp_name), reviewers=["codex", "claude"])
            _append_config_resolution(
                run,
                {
                    "codex": ["codex", "exec", "--skip-git-repo-check", "--json", "-"],
                    "claude": ["claude", "-p", "--verbose", "--output-format", "stream-json"],
                },
            )
            records = parse_run_records(run)

            codex_profile = _invocation_profile_for_actor(records, "codex")
            claude_profile = _invocation_profile_for_actor(records, "claude")

        self.assertEqual(codex_profile[2], "codex-cli")
        self.assertEqual(claude_profile[2], "claude-cli")


class FallbackPrinterTests(unittest.TestCase):

    def _plan(self, runtime_command: list[str]) -> ActorPlan:
        return ActorPlan(
            actor="codex",
            participant_profile_id="reviewer-default",
            execution_profile_id="codex-reviewer-default",
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

        plan = self._plan(["codex", "exec", "--skip-git-repo-check", "--json", "-"])
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
            _append_config_resolution(run, {"codex": ["codex", "exec", "--skip-git-repo-check", "--json", "-"], "claude": ["claude", "-p", "--verbose", "--output-format", "stream-json"]})
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

    def test_rereview_plan_uses_batch_scoped_rereview_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _stage_run(Path(tmp_name), reviewers=["codex"])
            batch_id, _prompt, _raw = _append_batch_scoped_review(run)
            records = parse_run_records(run)

            plan = _build_plan(
                records,
                run,
                round_id="round-1",
                phase="rereview",
                actor="codex",
                cwd=str(run),
                idle_timeout_seconds=30.0,
                stale_timeout_seconds=60.0,
                heartbeat_interval_seconds=1.0,
            )

        batch_component = "review-batch-round-1-remediation-verification"
        self.assertEqual(plan.review_batch_id, batch_id)
        self.assertEqual(
            plan.prompt_path,
            round_dir(run, "round-1") / "prompts" / "rereviews" / batch_component / "codex.md",
        )
        self.assertEqual(
            plan.raw_output_path,
            round_dir(run, "round-1") / "raw" / "rereviews" / batch_component / "codex.out",
        )

    def test_padded_batch_round_id_is_selected_for_unpadded_round_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = _stage_run(Path(tmp_name), reviewers=["codex"])
            batch_id, _prompt, _raw = _append_batch_scoped_review(run)
            round_path = round_dir(run, "round-1") / "round.md"
            round_path.write_text(
                round_path.read_text(encoding="utf-8").replace(
                    "round_id: round-1\nround_path: rounds/round-001\nexpected_reviewer_identities:\n  - codex\n",
                    "round_id: round-001\nround_path: rounds/round-001\nexpected_reviewer_identities:\n  - codex\n",
                ),
                encoding="utf-8",
            )

            plan = _build_plan(
                parse_run_records(run),
                run,
                round_id="round-1",
                phase="reviewer",
                actor="codex",
                cwd=str(run),
                idle_timeout_seconds=30.0,
                stale_timeout_seconds=60.0,
                heartbeat_interval_seconds=1.0,
            )

        self.assertEqual(plan.review_batch_id, batch_id)
        self.assertEqual(plan.artifact_version_id, "v2")

    def test_second_same_round_batch_uses_batch_prompt_raw_path_and_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex"])
            _append_config_resolution(
                run,
                {"codex": ["codex", "exec", "--skip-git-repo-check", "--json", "-"]},
            )
            batch_id, expected_prompt, expected_raw_output = _append_batch_scoped_review(run)
            captured: dict[str, object] = {}

            def fake_invoke(args):
                captured["invoke"] = args
                captured["prompt_existed_at_invocation"] = Path(args.prompt).is_file()
                Path(args.raw_output).parent.mkdir(parents=True, exist_ok=True)
                Path(args.raw_output).write_text("ok\n", encoding="utf-8")
                return 0

            def fake_capture(args):
                captured["capture"] = args
                return 0

            with (
                mock.patch(
                    "cross_agent_consensus.invocation.process_monitor.cmd_invoke_agent",
                    side_effect=fake_invoke,
                ),
                mock.patch("cross_agent_consensus.capture.cmd_capture", side_effect=fake_capture),
            ):
                rc, stdout, _stderr = _capture_run(
                    _run_args(run, execute_reviewers=True, approved=True)
                )

        self.assertEqual(rc, 0, stdout)
        invoke_args = captured["invoke"]
        capture_args = captured["capture"]
        self.assertTrue(captured["prompt_existed_at_invocation"])
        self.assertEqual(Path(invoke_args.prompt), expected_prompt)
        self.assertEqual(Path(invoke_args.raw_output), expected_raw_output)
        self.assertEqual(capture_args.review_batch, batch_id)
        self.assertEqual(capture_args.artifact_version, "v2")

    def test_execute_without_approved_exits_one_and_prints_manual(self) -> None:
        """Truth-table row: --approved=no → exits 1, prints manual commands."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex"])
            _append_config_resolution(run, {"codex": ["codex", "exec", "--skip-git-repo-check", "--json", "-"]})
            rc, stdout, stderr = _capture_run(_run_args(run, execute_reviewers=True, approved=False))
        self.assertEqual(rc, 1)
        self.assertIn("requires --approved", stderr)
        self.assertIn("manual fallback commands", stdout)

    def test_execute_approved_no_policy_stamps_cli_approved_flag(self) -> None:
        """Truth-table row: --approved=yes, no scoped policy → mechanism=cli_approved_flag."""
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = _stage_run(tmp, reviewers=["codex"])
            _append_config_resolution(run, {"codex": ["codex", "exec", "--skip-git-repo-check", "--json", "-"]})
            with (
                mock.patch("cross_agent_consensus.invocation.process_monitor.cmd_invoke_agent", return_value=0) as m_invoke,
                mock.patch("cross_agent_consensus.capture.cmd_capture", return_value=0) as m_capture,
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
            _append_config_resolution(run, {"codex": ["codex", "exec", "--skip-git-repo-check", "--json", "-"]})
            with (
                mock.patch("cross_agent_consensus.invocation.process_monitor.cmd_invoke_agent") as m_invoke,
                mock.patch("cross_agent_consensus.capture.cmd_capture", return_value=0),
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
                {"codex": ["codex", "exec", "--skip-git-repo-check", "--json", "-"], "claude": ["claude", "-p", "--verbose", "--output-format", "stream-json"]},
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
                mock.patch("cross_agent_consensus.capture.cmd_capture", side_effect=fake_capture),
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
            _append_config_resolution(run, {"codex": ["codex", "exec", "--skip-git-repo-check", "--json", "-"]})
            with (
                mock.patch("cross_agent_consensus.invocation.process_monitor.cmd_invoke_agent") as m_invoke,
                mock.patch("cross_agent_consensus.capture.cmd_capture") as m_capture,
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
