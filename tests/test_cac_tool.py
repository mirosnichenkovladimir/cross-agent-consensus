from __future__ import annotations

import json
import os
import subprocess
import shutil
import hashlib
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI = REPO_ROOT / "skills" / "cross-agent-consensus" / "scripts" / "consensus"


class ConsensusToolTests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
        cwd: Path | None = None,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        return subprocess.run(
            [str(CLI), *args],
            cwd=str(cwd or REPO_ROOT),
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            check=False,
        )

    def init_run(self, tmp: Path, *extra: str) -> Path:
        result = self.run_cli(
            "init",
            "--task",
            "Smoke CAC scripts",
            "--artifact-locator",
            "README.md",
            "--author",
            "author-codex",
            "--orchestrator",
            "orchestrator-codex",
            "--reviewer",
            "reviewer-codex",
            "--allow-reviewer-config-override",
            "--human-supervisor",
            "none",
            "--run-root",
            str(tmp / "runs"),
            *extra,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        runs = list((tmp / "runs").iterdir())
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].name, "smoke-cac-scripts-consensus-001")
        return runs[0]

    def write_round_batch(
        self,
        run: Path,
        round_number: int,
        mode: str,
        source_finding_ids: list[str] | None = None,
    ) -> None:
        round_path = run / "rounds" / f"round-{round_number:03d}"
        round_path.mkdir(parents=True, exist_ok=True)
        source_lines = ["source_finding_ids:"]
        for finding_id in source_finding_ids or []:
            source_lines.append(f"  - {finding_id}")
        if not source_finding_ids:
            source_lines = ["source_finding_ids: []"]
        (round_path / "round.md").write_text(
            "\n".join(
                [
                    f"# Round round-{round_number}",
                    "",
                    f"## ReviewBatch review-batch-round-{round_number}-{mode}",
                    "---",
                    "record_type: ReviewBatch",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    "actor_identity: orchestrator-codex",
                    'created_at: "2026-05-29T00:00:00Z"',
                    f"review_batch_id: review-batch-round-{round_number}-{mode}",
                    f"review_scope_id: review-scope-{run.name}",
                    f"review_mode: {mode}",
                    "target_artifact_version_id: v1",
                    *source_lines,
                    f"round_id: round-{round_number}",
                    f"round_path: rounds/round-{round_number:03d}",
                    "---",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def write_blocking_finding(self, run: Path, finding_id: str = "CXR-001") -> None:
        review_path = run / "rounds" / "round-001" / "reviews" / "reviewer-codex.md"
        review_path.write_text(
            "\n".join(
                [
                    "# Review round-1: reviewer-codex",
                    "",
                    f"## RawFinding raw-{finding_id}",
                    "---",
                    "record_type: RawFinding",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    "actor_identity: reviewer-codex",
                    'created_at: "2026-06-02T00:00:00Z"',
                    f"raw_finding_id: raw-{finding_id}",
                    "reviewer_identity: reviewer-codex",
                    "artifact_version_id: v1",
                    "review_batch_id: review-batch-round-1-fresh_review",
                    "location: README.md",
                    "claim: unresolved blocker",
                    "evidence: evidence",
                    "severity_or_materiality_claim: material",
                    "scope_classification: in_scope",
                    "blocking_status: blocking",
                    "suggested_fix_or_null: fix it",
                    "---",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        normalization_path = run / "rounds" / "round-001" / "normalization.md"
        normalization_path.write_text(
            "\n".join(
                [
                    "# Normalization",
                    "",
                    f"## NormalizationRecord normalization-{finding_id}",
                    "---",
                    "record_type: NormalizationRecord",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    "actor_identity: orchestrator-codex",
                    'created_at: "2026-06-02T00:00:00Z"',
                    f"normalization_record_id: normalization-{finding_id}",
                    f"source_raw_finding_ids: [raw-{finding_id}]",
                    "normalizer_identity: orchestrator-codex",
                    "classifier_identity: orchestrator-codex",
                    "materiality: material",
                    "scope_classification: in_scope",
                    "blocking_status: blocking",
                    "rationale: normalized",
                    f"canonical_finding_id: {finding_id}",
                    "---",
                    "",
                    f"## CanonicalFinding {finding_id}",
                    "---",
                    "record_type: CanonicalFinding",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    "actor_identity: orchestrator-codex",
                    'created_at: "2026-06-02T00:00:00Z"',
                    f"canonical_finding_id: {finding_id}",
                    "target_artifact_version_id: v1",
                    f"source_raw_finding_ids: [raw-{finding_id}]",
                    f"normalization_record_id: normalization-{finding_id}",
                    "materiality: material",
                    "materiality_status: undisputed",
                    "scope_classification: in_scope",
                    "blocking_status: blocking",
                    "lifecycle_state: open",
                    "claim: unresolved blocker",
                    "rationale_or_summary: normalized",
                    "clarification_pending: false",
                    "---",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def write_raw_and_canonical_finding(self, run: Path, tmp: Path) -> None:
        raw = tmp / "reviewer.out"
        raw.write_text("raw-finding-001: missing terminal condition\n", encoding="utf-8")
        result = self.run_cli(
            "capture",
            "--run",
            str(run),
            "--phase",
            "reviewer",
            "--actor",
            "reviewer-codex",
            "--review-batch",
            "review-batch-round-1-fresh_review",
            "--artifact-version",
            "v1",
            "--source-file",
            str(raw),
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

        review_file = run / "rounds" / "round-001" / "reviews" / "reviewer-codex.md"
        review_file.write_text(
            review_file.read_text(encoding="utf-8")
            + "\n".join(
                [
                    "",
                    "## RawFinding raw-finding-001",
                    "---",
                    "record_type: RawFinding",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    "actor_identity: reviewer-codex",
                    'created_at: "2026-05-29T00:00:00Z"',
                    "raw_finding_id: raw-finding-001",
                    "reviewer_identity: reviewer-codex",
                    "artifact_version_id: v1",
                    "review_batch_id: review-batch-round-1-fresh_review",
                    "location: specs/protocol.md",
                    "claim: Missing terminal condition.",
                    "evidence: The terminal condition is implicit.",
                    "severity_or_materiality_claim: blocker",
                    "scope_classification: in_scope",
                    "blocking_status: blocking",
                    "suggested_fix_or_null: Add explicit terminal condition.",
                    "---",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        (run / "rounds" / "round-001" / "normalization.md").write_text(
            "\n".join(
                [
                    "# Normalization",
                    "",
                    "## NormalizationRecord normalization-001",
                    "---",
                    "record_type: NormalizationRecord",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    "actor_identity: orchestrator-codex",
                    'created_at: "2026-05-29T00:00:00Z"',
                    "normalization_record_id: normalization-001",
                    "source_raw_finding_ids:",
                    "  - raw-finding-001",
                    "normalizer_identity: orchestrator-codex",
                    "classifier_identity: orchestrator-codex",
                    "materiality: material",
                    "scope_classification: in_scope",
                    "blocking_status: blocking",
                    "rationale: The claim affects consensus termination.",
                    "canonical_finding_id: canonical-finding-001",
                    "---",
                    "",
                    "## CanonicalFinding canonical-finding-001",
                    "---",
                    "record_type: CanonicalFinding",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    "actor_identity: orchestrator-codex",
                    'created_at: "2026-05-29T00:00:00Z"',
                    "canonical_finding_id: canonical-finding-001",
                    "target_artifact_version_id: v1",
                    "source_raw_finding_ids:",
                    "  - raw-finding-001",
                    "normalization_record_id: normalization-001",
                    "materiality: material",
                    "materiality_status: undisputed",
                    "scope_classification: in_scope",
                    "blocking_status: blocking",
                    "lifecycle_state: open",
                    "claim: Missing terminal condition.",
                    "rationale_or_summary: The claim affects consensus termination.",
                    "clarification_pending: false",
                    "---",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def write_still_valid_rereviews(self, run: Path, finding_id: str = "CXR-001") -> None:
        path = run / "rounds" / "round-002" / "rereviews" / "previous.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        sections = ["# Previous Re-Reviews", ""]
        for index in [1, 2]:
            sections.extend(
                [
                    f"## ReReviewDecision rereview-{index}",
                    "---",
                    "record_type: ReReviewDecision",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    "actor_identity: orchestrator-codex",
                    'created_at: "2026-06-02T00:00:00Z"',
                    f"re_review_decision_id: rereview-{index}",
                    f"canonical_finding_id: {finding_id}",
                    "reviewer_identity: reviewer-codex",
                    "decision: still_valid",
                    "rationale: still not fixed",
                    "artifact_version_id_or_null: v1",
                    "review_batch_id: review-batch-round-2-remediation_verification",
                    "---",
                    "",
                ]
            )
        path.write_text("\n".join(sections), encoding="utf-8")

    def write_completed_reviewer_session(self, run: Path, actor: str, round_path: str = "round-001") -> None:
        session = run / "rounds" / round_path / "agents" / actor / "session-001"
        session.mkdir(parents=True, exist_ok=True)
        (session / "invocation.json").write_text(
            json.dumps(
                {
                    "schema_version": "cross-agent-consensus-invocation-1",
                    "run_id": run.name,
                    "round_id": round_path,
                    "phase": "reviewer",
                    "actor_identity": actor,
                    "player_id": f"{actor}-cli",
                    "session_id": "session-001",
                }
            ),
            encoding="utf-8",
        )
        (session / "state.json").write_text(
            json.dumps({"schema_version": "cross-agent-consensus-state-1", "state": "completed"}),
            encoding="utf-8",
        )
        (session / "exit.json").write_text(
            json.dumps(
                {
                    "schema_version": "cross-agent-consensus-exit-1",
                    "final_state": "completed",
                    "exit_code_or_null": 0,
                }
            ),
            encoding="utf-8",
        )

    def write_reviewer_prompt(self, run: Path, actor: str = "reviewer-codex") -> Path:
        result = self.run_cli(
            "prompt",
            "--run",
            str(run),
            "--phase",
            "reviewer",
            "--actor",
            actor,
            "--round",
            "round-1",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return run / "rounds" / "round-001" / "prompts" / "reviewers" / f"{actor}.md"

    def test_version_command_prints_semver(self) -> None:
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertRegex(result.stdout.strip(), r"^\d+\.\d+\.\d+$")

    def test_managed_manifest_hashes_match_source_files(self) -> None:
        package_root = REPO_ROOT / "skills" / "cross-agent-consensus"
        manifest = json.loads((package_root / "managed-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["package"], "cross-agent-consensus")
        for item in manifest["managed_files"]:
            path = package_root / item["path"]
            self.assertTrue(path.is_file(), item["path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, item["sha256"], item["path"])

    def test_installed_skill_entrypoint_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            codex_home = tmp / "codex-home"
            home = tmp / "home"
            home.mkdir()

            env = os.environ.copy()
            env.update({"CODEX_HOME": str(codex_home), "HOME": str(home)})
            install = subprocess.run(
                [str(REPO_ROOT / "scripts" / "install-cac"), "--target", "codex"],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr + install.stdout)

            installed_cli = codex_home / "skills" / "cross-agent-consensus" / "scripts" / "consensus"

            version = subprocess.run(
                [str(installed_cli), "--version"],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(version.returncode, 0, version.stderr + version.stdout)
            self.assertRegex(version.stdout.strip(), r"^\d+\.\d+\.\d+$")

            config = subprocess.run(
                [str(installed_cli), "config", "show", "--json"],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(config.returncode, 0, config.stderr + config.stdout)

            run_root = tmp / "runs"
            init = subprocess.run(
                [
                    str(installed_cli),
                    "init",
                    "--task",
                    "Installed skill smoke",
                    "--profile",
                    "implementation-test",
                    "--artifact-locator",
                    "README.md",
                    "--author",
                    "author-codex",
                    "--orchestrator",
                    "orchestrator-codex",
                    "--reviewer",
                    "reviewer-codex",
                    "--allow-reviewer-config-override",
                    "--human-supervisor",
                    "none",
                    "--validator",
                    "smoke_validator",
                    "--run-root",
                    str(run_root),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(init.returncode, 0, init.stderr + init.stdout)
            run = run_root / "installed-skill-smoke-consensus-001"

            prompt = subprocess.run(
                [
                    str(installed_cli),
                    "prompt",
                    "--run",
                    str(run),
                    "--phase",
                    "reviewer",
                    "--actor",
                    "reviewer-codex",
                    "--round",
                    "round-1",
                ],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(prompt.returncode, 0, prompt.stderr + prompt.stdout)

            validate = subprocess.run(
                [str(installed_cli), "validate", "--run", str(run), "--pre-execution"],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(validate.returncode, 0, validate.stderr + validate.stdout)

            raw = tmp / "validator.out"
            raw.write_text("pass\n", encoding="utf-8")
            capture = subprocess.run(
                [
                    str(installed_cli),
                    "capture",
                    "--run",
                    str(run),
                    "--phase",
                    "validator",
                    "--actor",
                    "validator-local",
                    "--artifact-version",
                    "v1",
                    "--validator-id",
                    "smoke_validator",
                    "--result",
                    "pass",
                    "--source-file",
                    str(raw),
                ],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(capture.returncode, 0, capture.stderr + capture.stdout)

            terminate = subprocess.run(
                [
                    str(installed_cli),
                    "terminate",
                    "--run",
                    str(run),
                    "--terminal-condition",
                    "consensus_reached",
                    "--final-artifact-version",
                    "v1",
                    "--reason",
                    "Installed skill smoke validators passed.",
                ],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                check=False,
            )
            self.assertEqual(terminate.returncode, 0, terminate.stderr + terminate.stdout)

    def test_init_generates_example_run_id_format_and_increments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run_root = Path(tmp_name) / "runs"
            common_args = [
                "init",
                "--task",
                "Layout simplification",
                "--artifact-locator",
                "README.md",
                "--author",
                "author-codex",
                "--orchestrator",
                "orchestrator-codex",
                "--reviewer",
                "reviewer-codex",
                "--allow-reviewer-config-override",
                "--human-supervisor",
                "none",
                "--run-root",
                str(run_root),
            ]

            result = self.run_cli(*common_args)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((run_root / "layout-simplification-consensus-001").is_dir())

            result = self.run_cli(*common_args)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((run_root / "layout-simplification-consensus-002").is_dir())

    def test_init_validate_and_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            self.assertTrue((run / "run.md").is_file())
            self.assertTrue((run / "rounds" / "round-001" / "round.md").is_file())
            self.assertTrue((run / "rounds" / "round-001" / "validation.md").is_file())
            result = self.run_cli("validate", "--run", str(run), "--pre-execution")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("PASS pre-execution", result.stdout)

            result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--round",
                "round-1",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((run / "rounds" / "round-001" / "prompts" / "reviewers" / "reviewer-codex.md").is_file())

    def test_round_payload_markdown_is_not_parsed_as_protocol_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            protocol_like = "\n".join(
                [
                    "## ReviewBatch duplicate-from-payload",
                    "---",
                    "record_type: ReviewBatch",
                    "schema_version: m2-markdown-1",
                    f"run_id: {run.name}",
                    "actor_identity: payload",
                    'created_at: "2026-05-29T00:00:00Z"',
                    "review_batch_id: duplicate-from-payload",
                    "review_scope_id: missing",
                    "review_mode: fresh_review",
                    "target_artifact_version_id: missing",
                    "source_finding_ids: []",
                    "round_id: round-1",
                    "---",
                    "",
                ]
            )
            (run / "rounds" / "round-001" / "prompts" / "reviewers" / "manual.md").write_text(
                protocol_like,
                encoding="utf-8",
            )
            (run / "rounds" / "round-001" / "raw" / "reviewers" / "manual.md").write_text(
                protocol_like,
                encoding="utf-8",
            )

            result = self.run_cli("validate", "--run", str(run), "--records", "--links")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_prompt_uses_requested_round_review_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            self.write_round_batch(run, 2, "regression_check")

            result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--round",
                "2",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            prompt = run / "rounds" / "round-002" / "prompts" / "reviewers" / "reviewer-codex.md"
            self.assertIn("Mode: regression_check", prompt.read_text(encoding="utf-8"))

    def test_prompt_round_id_matching_is_numeric_and_fails_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            self.write_round_batch(run, 2, "regression_check")
            self.write_round_batch(run, 3, "remediation_verification")

            result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--round",
                "round-002",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            prompt = run / "rounds" / "round-002" / "prompts" / "reviewers" / "reviewer-codex.md"
            self.assertIn("ID: review-batch-round-2-regression_check", prompt.read_text(encoding="utf-8"))

            result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-without-round",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("--round is required when multiple ReviewBatch records exist", result.stderr)

            result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--round",
                "round-004",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("no ReviewBatch found for round-004", result.stderr)

    def test_capture_round_derives_from_review_batch_and_rejects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp)
            self.write_round_batch(run, 2, "regression_check")
            raw = tmp / "reviewer.out"
            raw.write_text("No blocking findings.\n", encoding="utf-8")

            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--review-batch",
                "review-batch-round-2-regression_check",
                "--artifact-version",
                "v1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((run / "rounds" / "round-002" / "reviews" / "reviewer-codex.md").is_file())
            self.assertFalse((run / "rounds" / "round-001" / "reviews" / "reviewer-codex.md").exists())

            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-mismatch",
                "--review-batch",
                "review-batch-round-2-regression_check",
                "--artifact-version",
                "v1",
                "--round",
                "round-1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("does not match ReviewBatch", result.stderr)

    def test_validation_rejects_review_record_in_wrong_round_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            self.write_round_batch(run, 2, "regression_check")
            misplaced = run / "rounds" / "round-001" / "reviews" / "misplaced.md"
            misplaced.write_text(
                "\n".join(
                    [
                        "# Review round-1: misplaced",
                        "",
                        "## RawReviewerOutput raw-output-round-1-misplaced",
                        "---",
                        "record_type: RawReviewerOutput",
                        "schema_version: m2-markdown-1",
                        f"run_id: {run.name}",
                        "actor_identity: orchestrator-capture-tool",
                        'created_at: "2026-05-29T00:00:00Z"',
                        "raw_output_id: raw-output-round-1-misplaced",
                        "reviewer_identity: reviewer-codex",
                        "review_batch_id: review-batch-round-2-regression_check",
                        "artifact_version_id: v1",
                        "raw_finding_ids: []",
                        "is_first_round_independent: true",
                        "---",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli("validate", "--run", str(run), "--links")
            self.assertEqual(result.returncode, 2)
            self.assertIn("does not match ReviewBatch review-batch-round-2-regression_check", result.stdout)

    def test_rereview_skeleton_stops_at_remediation_cap_and_records_escalation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            self.write_blocking_finding(run)
            self.write_round_batch(run, 2, "remediation_verification", source_finding_ids=["CXR-001"])
            self.write_still_valid_rereviews(run)

            result = self.run_cli(
                "rereview-skeleton",
                "--run",
                str(run),
                "--round",
                "round-2",
                "--review-batch",
                "review-batch-round-2-remediation_verification",
                "--reviewer",
                "reviewer-codex",
                "--artifact-version",
                "v1",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("reached max_remediation_rounds_per_finding=2", result.stderr)
            self.assertIn("EscalationRecord", (run / "escalations.md").read_text(encoding="utf-8"))
            self.assertFalse((run / "rounds" / "round-002" / "rereviews" / "reviewer-codex.md").exists())

    def test_rereview_prompt_stops_at_remediation_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            self.write_blocking_finding(run)
            self.write_round_batch(run, 2, "remediation_verification", source_finding_ids=["CXR-001"])
            self.write_still_valid_rereviews(run)

            result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "rereview",
                "--actor",
                "reviewer-codex",
                "--round",
                "round-2",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("reached max_remediation_rounds_per_finding=2", result.stderr)
            self.assertIn("EscalationRecord", (run / "escalations.md").read_text(encoding="utf-8"))
            self.assertFalse((run / "rounds" / "round-002" / "prompts" / "rereviews" / "reviewer-codex.md").exists())

    def test_escalated_to_human_termination_allows_pending_validators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            self.write_blocking_finding(run)
            self.write_round_batch(run, 2, "remediation_verification", source_finding_ids=["CXR-001"])
            self.write_still_valid_rereviews(run)
            prompt = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "rereview",
                "--actor",
                "reviewer-codex",
                "--round",
                "round-2",
            )
            self.assertEqual(prompt.returncode, 2, prompt.stderr + prompt.stdout)

            terminate = self.run_cli(
                "terminate",
                "--run",
                str(run),
                "--terminal-condition",
                "escalated_to_human",
                "--final-artifact-version",
                "v1",
                "--reason",
                "Remediation cap reached for CXR-001.",
            )

            self.assertEqual(terminate.returncode, 0, terminate.stderr + terminate.stdout)
            report = (run / "report.md").read_text(encoding="utf-8")
            self.assertTrue(report.startswith("# Report\n\n## Results"))
            self.assertIn("terminal_condition: escalated_to_human", report)
            self.assertIn("Agent Invocation Summary", report)

    def test_legacy_ledger_layout_still_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            (run / "init.md").write_text((run / "run.md").read_text(encoding="utf-8"), encoding="utf-8")
            (run / "review-batches.md").write_text(
                (run / "rounds" / "round-001" / "round.md").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            for path in [
                run / "reviews",
                run / "normalization",
                run / "author-responses",
                run / "rereviews",
                run / "payloads" / "prompts",
                run / "payloads" / "raw",
            ]:
                path.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(run / "rounds")
            (run / "run.md").unlink()

            result = self.run_cli("validate", "--run", str(run), "--pre-execution")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("PASS pre-execution", result.stdout)

    def test_init_rejects_participant_collision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            result = self.run_cli(
                "init",
                "--task",
                "Bad participants",
                "--artifact-locator",
                "README.md",
                "--author",
                "same-agent",
                "--orchestrator",
                "same-agent",
                "--reviewer",
                "reviewer-codex",
                "--allow-reviewer-config-override",
                "--run-root",
                str(Path(tmp_name) / "runs"),
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("distinct", result.stderr)

    def test_config_show_loads_project_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project = Path(tmp_name) / "project"
            project.mkdir()
            (project / ".git").mkdir()
            (project / ".cross-agent-consensus.yaml").write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-1",
                        "defaults:",
                        "  run_root: configured-runs",
                        "participants:",
                        "  orchestrator: orchestrator-project",
                        "  author: author-project",
                        "  reviewers:",
                        "    - reviewer-project",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli("config", "show", "--json", "--cwd", str(project))
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["effective"]["defaults"]["run_root"], "configured-runs")
            self.assertEqual(payload["effective"]["participants"]["reviewers"], ["reviewer-project"])
            self.assertTrue(any(source["layer"] == "project" and source["present"] for source in payload["sources"]))

    def test_installed_defaults_define_codex_primary_and_codex_claude_reviewers(self) -> None:
        result = self.run_cli("config", "show", "--json", "--no-config")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["effective"]["participants"]["orchestrator"], "codex-orchestrator")
        self.assertEqual(payload["effective"]["participants"]["author"], "codex-primary")
        self.assertEqual(payload["effective"]["participants"]["reviewers"], ["codex", "claude"])
        self.assertEqual(payload["effective"]["participants"]["human_supervisor"], "none")
        self.assertEqual(payload["effective"]["reviewer_clis"]["codex"]["command"], ["codex", "exec", "--json", "-"])
        self.assertEqual(
            payload["effective"]["reviewer_clis"]["claude"]["command"],
            ["claude", "-p", "--verbose", "--output-format", "stream-json", "--include-partial-messages"],
        )

    def test_review_focus_does_not_replace_configured_reviewers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            result = self.run_cli(
                "init",
                "--task",
                "Implementation review",
                "--artifact-locator",
                "README.md",
                "--review-focus",
                "publication safety",
                "--review-focus",
                "API surface",
                "--run-root",
                str(tmp / "runs"),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run = tmp / "runs" / "implementation-review-consensus-001"
            run_text = (run / "run.md").read_text(encoding="utf-8")
            round_text = (run / "rounds" / "round-001" / "round.md").read_text(encoding="utf-8")
            self.assertIn("reviewer_identities:\n  - codex\n  - claude", run_text)
            self.assertIn("review_focus:\n  - publication safety\n  - API surface", round_text)
            self.assertIn("Review focus values are prompt lenses only", round_text)

    def test_user_local_config_overrides_installed_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            user_config = Path(tmp_name) / "config.local.yaml"
            user_config.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-1",
                        "defaults:",
                        "  run_root: user-runs",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli(
                "config",
                "show",
                "--json",
                env={"CROSS_AGENT_CONSENSUS_CONFIG": str(user_config)},
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["effective"]["defaults"]["run_root"], "user-runs")
            self.assertTrue(any(source.get("note") == "from CROSS_AGENT_CONSENSUS_CONFIG" for source in payload["sources"]))

    def test_missing_env_config_path_fails_validation(self) -> None:
        result = self.run_cli(
            "config",
            "validate",
            env={"CROSS_AGENT_CONSENSUS_CONFIG": "/definitely/missing/cac.yaml"},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("CROSS_AGENT_CONSENSUS_CONFIG path not found", result.stdout)

    def test_config_validate_rejects_persistent_unattended_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            config = Path(tmp_name) / "bad.yaml"
            config.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-1",
                        "invocation:",
                        "  unattended_invocation:",
                        "    enabled: true",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli("config", "validate", "--config", str(config))
            self.assertEqual(result.returncode, 2)
            self.assertIn("persistent config must not enable unattended_invocation", result.stdout)

    def test_config_validate_rejects_secret_values_in_persistent_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            config = Path(tmp_name) / "bad.yaml"
            config.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-1",
                        "reviewer_clis:",
                        "  reviewer-a:",
                        "    command:",
                        "      - tool",
                        "    env:",
                        "      API_KEY: abc123",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli("config", "validate", "--config", str(config))
            self.assertEqual(result.returncode, 2)
            self.assertIn("env must be a list of environment variable names", result.stdout)
            self.assertIn("secret-looking key", result.stdout)

    def test_config_validate_rejects_invalid_round_limit_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            config = Path(tmp_name) / "bad.yaml"
            config.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-1",
                        "defaults:",
                        "  round_limits:",
                        "    max_fresh_review_rounds: many",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli("config", "validate", "--config", str(config))
            self.assertEqual(result.returncode, 2)
            self.assertIn("max_fresh_review_rounds must be an integer", result.stdout)

    def test_init_uses_config_defaults_and_records_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project = Path(tmp_name) / "project"
            project.mkdir()
            (project / ".git").mkdir()
            (project / "README.md").write_text("artifact\n", encoding="utf-8")
            (project / ".cross-agent-consensus.yaml").write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-1",
                        "defaults:",
                        "  run_root: configured-runs",
                        "participants:",
                        "  orchestrator: orchestrator-project",
                        "  author: author-project",
                        "  reviewers:",
                        "    - reviewer-project",
                        "  human_supervisor: none",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli("init", "--task", "Configured run", "--artifact-locator", "README.md", cwd=project)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run = project / "configured-runs" / "configured-run-consensus-001"
            run_text = (run / "run.md").read_text(encoding="utf-8")
            self.assertIn("## ConfigResolution config-resolution-configured-run-consensus-001", run_text)
            self.assertIn("participants.reviewers:", run_text)
            self.assertIn("source_layer: project", run_text)
            self.assertIn("author_identity: author-project", run_text)

    def test_cli_reviewers_require_explicit_config_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project = Path(tmp_name) / "project"
            project.mkdir()
            (project / ".git").mkdir()
            (project / "README.md").write_text("artifact\n", encoding="utf-8")
            (project / ".cross-agent-consensus.yaml").write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-1",
                        "participants:",
                        "  orchestrator: orchestrator-project",
                        "  author: author-project",
                        "  reviewers:",
                        "    - reviewer-project",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli(
                "init",
                "--task",
                "Reviewer override",
                "--artifact-locator",
                "README.md",
                "--reviewer",
                "reviewer-cli-a",
                "--reviewer",
                "reviewer-cli-b",
                cwd=project,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("--reviewer would replace configured participants.reviewers", result.stderr)
            self.assertIn("--allow-reviewer-config-override", result.stderr)

            result = self.run_cli(
                "init",
                "--task",
                "Reviewer override",
                "--artifact-locator",
                "README.md",
                "--reviewer",
                "reviewer-cli-a",
                "--reviewer",
                "reviewer-cli-b",
                "--allow-reviewer-config-override",
                cwd=project,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run_text = (project / "runs" / "reviewer-override-consensus-001" / "run.md").read_text(encoding="utf-8")
            self.assertIn("reviewer_identities:\n  - reviewer-cli-a\n  - reviewer-cli-b", run_text)
            self.assertNotIn("reviewer-project", run_text.split("## Participants", 1)[1])
            self.assertIn("accepted reviewer config override", run_text)

    def test_task_file_config_overrides_project_and_cli_overrides_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project = Path(tmp_name) / "project"
            project.mkdir()
            (project / ".git").mkdir()
            (project / "README.md").write_text("artifact\n", encoding="utf-8")
            (project / ".cross-agent-consensus.yaml").write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-config-1",
                        "participants:",
                        "  orchestrator: orchestrator-project",
                        "  author: author-project",
                        "  reviewers:",
                        "    - reviewer-project",
                    ]
                ),
                encoding="utf-8",
            )
            task_file = project / "task.yaml"
            task_file.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-task-1",
                        "task:",
                        "  objective: Task file run",
                        "  artifact_locator: README.md",
                        "config:",
                        "  participants:",
                        "    orchestrator: orchestrator-task",
                        "    author: author-task",
                        "    reviewers:",
                        "      - reviewer-task",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli("init", "--task-file", str(task_file), "--author", "author-cli", cwd=project)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run_text = (project / "runs" / "task-file-run-consensus-001" / "run.md").read_text(encoding="utf-8")
            self.assertIn("orchestrator_identity: orchestrator-task", run_text)
            self.assertIn("author_identity: author-cli", run_text)
            self.assertIn("reviewer_identities:\n  - reviewer-task", run_text)

    def test_task_file_unattended_invocation_requires_scope_and_populates_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            project = Path(tmp_name) / "project"
            project.mkdir()
            (project / ".git").mkdir()
            (project / "README.md").write_text("artifact\n", encoding="utf-8")
            task_file = project / "task.yaml"
            task_file.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-task-1",
                        "task:",
                        "  objective: Task unattended run",
                        "  artifact_locator: README.md",
                        "config:",
                        "  participants:",
                        "    orchestrator: orchestrator-task",
                        "    author: author-task",
                        "    reviewers:",
                        "      - reviewer-task",
                        "  invocation:",
                        "    unattended_invocation:",
                        "      enabled: true",
                        "      scope:",
                        "        - reviewer-cli-only",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_cli("init", "--task-file", str(task_file), cwd=project)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run_text = (project / "runs" / "task-unattended-run-consensus-001" / "run.md").read_text(encoding="utf-8")
            self.assertIn("unattended_invocation:\n  enabled: true\n  scope:\n    - reviewer-cli-only", run_text)

            bad_task = project / "bad-task.yaml"
            bad_task.write_text(
                "\n".join(
                    [
                        "schema_version: cross-agent-consensus-task-1",
                        "task:",
                        "  objective: Bad task unattended run",
                        "  artifact_locator: README.md",
                        "config:",
                        "  participants:",
                        "    orchestrator: orchestrator-task",
                        "    author: author-task",
                        "    reviewers:",
                        "      - reviewer-task",
                        "  invocation:",
                        "    unattended_invocation:",
                        "      enabled: true",
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_cli("init", "--task-file", str(bad_task), cwd=project)
            self.assertEqual(result.returncode, 1)
            self.assertIn("unattended_invocation.scope", result.stderr)

    def test_config_setup_dry_run_outputs_safe_yaml(self) -> None:
        result = self.run_cli("config", "setup", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("schema_version: cross-agent-consensus-config-1", result.stdout)
        self.assertIn('      - "-"', result.stdout)
        self.assertNotIn("unattended_invocation", result.stdout)
        self.assertNotIn("token", result.stdout.lower())

    def test_capture_reviewer_preserves_raw_output_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp)
            raw = tmp / "reviewer.out"
            raw.write_text("No blocking findings.\n", encoding="utf-8")

            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--review-batch",
                "review-batch-round-1-fresh_review",
                "--artifact-version",
                "v1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((run / "rounds" / "round-001" / "reviews" / "reviewer-codex.md").is_file())

            result = self.run_cli("validate", "--run", str(run), "--records", "--links", "--reviewer-isolation")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_configured_cli_reviewer_output_requires_invoke_agent_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            result = self.run_cli(
                "init",
                "--task",
                "Configured CLI review",
                "--artifact-locator",
                "README.md",
                "--run-root",
                str(tmp / "runs"),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run = tmp / "runs" / "configured-cli-review-consensus-001"
            raw = tmp / "codex.out"
            raw.write_text("No blocking findings.\n", encoding="utf-8")

            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "codex",
                "--review-batch",
                "review-batch-round-1-fresh_review",
                "--artifact-version",
                "v1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            result = self.run_cli("validate", "--run", str(run), "--reviewer-isolation")
            self.assertEqual(result.returncode, 2)
            self.assertIn("CLI reviewer 'codex' has RawReviewerOutput", result.stdout)
            self.assertIn("without a completed invoke-agent session", result.stdout)

            self.write_completed_reviewer_session(run, "codex")
            result = self.run_cli("validate", "--run", str(run), "--reviewer-isolation")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_capture_allocates_unique_raw_payload_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp, "--profile", "implementation-test", "--validator", "smoke_validator")
            raw = tmp / "validator.out"
            raw.write_text("pass\n", encoding="utf-8")

            for _ in range(2):
                result = self.run_cli(
                    "capture",
                    "--run",
                    str(run),
                    "--phase",
                    "validator",
                    "--actor",
                    "validator-local",
                    "--artifact-version",
                    "v1",
                    "--validator-id",
                    "smoke_validator",
                    "--result",
                    "pass",
                    "--source-file",
                    str(raw),
                )
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            payloads = sorted((run / "rounds" / "round-001" / "raw" / "validators").glob("smoke-validator*.out"))
            self.assertEqual(len(payloads), 2)
            self.assertEqual(len({path.name for path in payloads}), 2)

    def test_conclusion_validation_batch_prompts_and_capture_are_batch_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp)
            self.write_raw_and_canonical_finding(run, tmp)

            result = self.run_cli(
                "conclusion-validation",
                "--run",
                str(run),
                "--round",
                "round-1",
                "--write-prompts",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn(
                "created conclusion validation batch: review-batch-round-1-scope_triage-conclusion_validation",
                result.stdout,
            )
            prompt = (
                run
                / "rounds"
                / "round-001"
                / "prompts"
                / "reviewers"
                / "review-batch-round-1-scope-triage-conclusion-validation"
                / "reviewer-codex.md"
            )
            self.assertTrue(prompt.is_file())
            prompt_text = prompt.read_text(encoding="utf-8")
            self.assertIn("## Conclusion Validation", prompt_text)
            self.assertIn("valid_blocker", prompt_text)
            self.assertIn("This is not a fresh review", prompt_text)
            self.assertIn("Every decision must include explanation or argumentation", prompt_text)
            self.assertIn("`agree` still requires rationale", prompt_text)
            self.assertIn("evidence_refs", prompt_text)
            self.assertIn("expected_reviewer_identities:", (run / "rounds" / "round-001" / "round.md").read_text(encoding="utf-8"))

            raw = tmp / "conclusion-validation.out"
            raw.write_text(
                "\n".join(
                    [
                        "canonical_finding_id: canonical-finding-001",
                        "reviewer_decision: agree",
                        "corrected_conclusion: null",
                        "needs_human_reason: null",
                        "rationale: The canonical evidence supports a valid blocker conclusion.",
                        "evidence_refs: [canonical.rationale_or_summary, raw-finding-001]",
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--review-batch",
                "review-batch-round-1-scope_triage-conclusion_validation",
                "--artifact-version",
                "v1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            review = (
                run
                / "rounds"
                / "round-001"
                / "reviews"
                / "reviewer-codex-review-batch-round-1-scope-triage-conclusion-validation.md"
            )
            self.assertTrue(review.is_file())
            self.assertIn("is_first_round_independent: false", review.read_text(encoding="utf-8"))
            self.assertTrue(
                (
                    run
                    / "rounds"
                    / "round-001"
                    / "raw"
                    / "reviewers"
                    / "review-batch-round-1-scope-triage-conclusion-validation"
                    / "reviewer-codex.out"
                ).is_file()
            )

            result = self.run_cli("validate", "--run", str(run), "--records", "--links", "--reviewer-isolation")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_prompt_can_resolve_round_from_review_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp)
            self.write_raw_and_canonical_finding(run, tmp)

            result = self.run_cli("conclusion-validation", "--run", str(run), "--round", "round-1")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--review-batch",
                "review-batch-round-1-scope_triage-conclusion_validation",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            prompt = (
                run
                / "rounds"
                / "round-001"
                / "prompts"
                / "reviewers"
                / "review-batch-round-1-scope-triage-conclusion-validation"
                / "reviewer-codex.md"
            )
            self.assertTrue(prompt.is_file())

    def test_response_skeleton_waits_for_conclusion_validation_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp)
            self.write_raw_and_canonical_finding(run, tmp)

            result = self.run_cli("conclusion-validation", "--run", str(run), "--round", "round-1")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            result = self.run_cli("response-skeleton", "--run", str(run), "--round", "round-1")
            self.assertEqual(result.returncode, 1)
            self.assertIn("AuthorResponse is blocked until conclusion-validation output is captured", result.stderr)

            result = self.run_cli("validate", "--run", str(run), "--records")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            raw = tmp / "conclusion-validation.out"
            raw.write_text(
                "\n".join(
                    [
                        "canonical_finding_id: canonical-finding-001",
                        "reviewer_decision: agree",
                        "rationale: The canonical evidence supports the proposed conclusion.",
                        "evidence_refs: [canonical.rationale_or_summary]",
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--review-batch",
                "review-batch-round-1-scope_triage-conclusion_validation",
                "--artifact-version",
                "v1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            result = self.run_cli("response-skeleton", "--run", str(run), "--round", "round-1")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            (run / "rounds" / "round-001" / "author-responses.md").write_text(
                "\n".join(
                    [
                        "# Author Responses round-1",
                        "",
                        "## AuthorResponse author-response-round-1-001",
                        "---",
                        "record_type: AuthorResponse",
                        "schema_version: m2-markdown-1",
                        f"run_id: {run.name}",
                        "actor_identity: author-codex",
                        'created_at: "2026-05-29T00:00:00Z"',
                        "author_response_id: author-response-round-1-001",
                        "canonical_finding_id: canonical-finding-001",
                        "response_type: accept",
                        "rationale: Will fix.",
                        "resulting_artifact_version_id_or_null: null",
                        "clarification_request_or_null: null",
                        "---",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_cli("validate", "--run", str(run), "--records")
            self.assertEqual(result.returncode, 2)
            self.assertIn("earlier than conclusion-validation output", result.stdout)

    def test_response_skeleton_waits_for_every_expected_conclusion_validation_reviewer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp, "--reviewer", "reviewer-claude")
            self.write_raw_and_canonical_finding(run, tmp)

            result = self.run_cli("conclusion-validation", "--run", str(run), "--round", "round-1", "--write-prompts")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            prompt = (
                run
                / "rounds"
                / "round-001"
                / "prompts"
                / "reviewers"
                / "review-batch-round-1-scope-triage-conclusion-validation"
                / "reviewer-claude.md"
            )
            self.assertTrue(prompt.is_file())

            raw = tmp / "conclusion-validation.out"
            raw.write_text(
                "\n".join(
                    [
                        "canonical_finding_id: canonical-finding-001",
                        "reviewer_decision: agree",
                        "rationale: The canonical evidence supports the proposed conclusion.",
                        "evidence_refs: [canonical.rationale_or_summary]",
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--review-batch",
                "review-batch-round-1-scope_triage-conclusion_validation",
                "--artifact-version",
                "v1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            result = self.run_cli("response-skeleton", "--run", str(run), "--round", "round-1")
            self.assertEqual(result.returncode, 1)
            self.assertIn("AuthorResponse is blocked until conclusion-validation output is captured", result.stderr)

            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-claude",
                "--review-batch",
                "review-batch-round-1-scope_triage-conclusion_validation",
                "--artifact-version",
                "v1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            result = self.run_cli("response-skeleton", "--run", str(run), "--round", "round-1")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_policy_skip_unblocks_conclusion_validation_response_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp)
            self.write_raw_and_canonical_finding(run, tmp)

            result = self.run_cli("conclusion-validation", "--run", str(run), "--round", "round-1")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            run_md = run / "run.md"
            run_md.write_text(
                run_md.read_text(encoding="utf-8").replace(
                    "waiver_authority_or_null: null\n",
                    "\n".join(
                        [
                            "waiver_authority_or_null: null",
                            "skipped_conclusion_validation_batch_ids:",
                            "  - review-batch-round-1-scope_triage-conclusion_validation",
                            "",
                        ]
                    ),
                ),
                encoding="utf-8",
            )

            result = self.run_cli("response-skeleton", "--run", str(run), "--round", "round-1")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            (run / "rounds" / "round-001" / "author-responses.md").write_text(
                "\n".join(
                    [
                        "# Author Responses round-1",
                        "",
                        "## AuthorResponse author-response-round-1-001",
                        "---",
                        "record_type: AuthorResponse",
                        "schema_version: m2-markdown-1",
                        f"run_id: {run.name}",
                        "actor_identity: author-codex",
                        'created_at: "2026-05-29T00:00:00Z"',
                        "author_response_id: author-response-round-1-001",
                        "canonical_finding_id: canonical-finding-001",
                        "response_type: accept",
                        "rationale: Policy skipped conclusion validation.",
                        "resulting_artifact_version_id_or_null: null",
                        "clarification_request_or_null: null",
                        "---",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            result = self.run_cli("validate", "--run", str(run), "--records")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_validator_capture_allows_explicit_round_with_multiple_batches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp, "--profile", "implementation-test", "--validator", "smoke_validator")
            self.write_raw_and_canonical_finding(run, tmp)

            result = self.run_cli("conclusion-validation", "--run", str(run), "--round", "round-1")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            raw = tmp / "validator.out"
            raw.write_text("pass\n", encoding="utf-8")
            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "validator",
                "--round",
                "round-1",
                "--actor",
                "validator-local",
                "--artifact-version",
                "v1",
                "--validator-id",
                "smoke_validator",
                "--result",
                "pass",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_duplicate_reviewer_capture_rejected_for_same_review_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp)
            self.write_raw_and_canonical_finding(run, tmp)

            raw = tmp / "duplicate-reviewer.out"
            raw.write_text("duplicate raw output\n", encoding="utf-8")
            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--review-batch",
                "review-batch-round-1-fresh_review",
                "--artifact-version",
                "v1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("reviewer output already captured", result.stderr)

    def test_new_artifact_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name))
            result = self.run_cli(
                "new-artifact",
                "--run",
                str(run),
                "--artifact-version",
                "v1",
                "--content-locator",
                "README.md",
                "--produced-by",
                "author-codex",
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("refusing to overwrite", result.stderr)

    def test_invocation_ready_requires_recorded_actor_and_raw_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt = run / "rounds" / "round-001" / "prompts" / "reviewers" / "manual.md"
            prompt.write_text("review prompt\n", encoding="utf-8")

            result = self.run_cli(
                "invocation-ready",
                "--run",
                str(run),
                "--actor",
                "reviewer-codex",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer.out"),
                "--command",
                sys.executable,
                "--version",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("invocation ready", result.stdout)

    def test_capture_and_invocation_ready_help_explain_no_telemetry_boundary(self) -> None:
        capture = self.run_cli("capture", "--help")
        self.assertEqual(capture.returncode, 0, capture.stderr + capture.stdout)
        self.assertIn("does not start, supervise, or monitor", capture.stdout)
        self.assertIn("does not create rounds/<round>/agents/<actor>/session-* telemetry", capture.stdout)
        self.assertIn("Use invoke-agent", capture.stdout)

        ready = self.run_cli("invocation-ready", "--help")
        self.assertEqual(ready.returncode, 0, ready.stderr + ready.stdout)
        self.assertIn("does not start or monitor", ready.stdout)
        self.assertIn("Use invoke-agent for supervised execution", ready.stdout)

    def test_agent_status_explains_direct_capture_has_no_monitored_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp, "--unattended-invocation", "--unattended-scope", "test")
            raw = tmp / "reviewer.out"
            raw.write_text("review output\n", encoding="utf-8")

            capture = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--review-batch",
                "review-batch-round-1-fresh_review",
                "--artifact-version",
                "v1",
                "--source-file",
                str(raw),
            )
            self.assertEqual(capture.returncode, 0, capture.stderr + capture.stdout)
            self.assertTrue((run / "rounds" / "round-001" / "reviews" / "reviewer-codex.md").is_file())
            self.assertFalse((run / "rounds" / "round-001" / "agents" / "reviewer-codex").exists())

            status = self.run_cli(
                "agent-status",
                "--run",
                str(run),
                "--actor",
                "reviewer-codex",
            )
            self.assertEqual(status.returncode, 2)
            self.assertIn("No monitored agent session exists", status.stderr)
            self.assertIn("captured directly with consensus capture", status.stderr)
            self.assertIn("use invoke-agent next time", status.stderr)

            status_json = self.run_cli(
                "agent-status",
                "--run",
                str(run),
                "--actor",
                "reviewer-codex",
                "--json",
            )
            self.assertEqual(status_json.returncode, 2)
            payload = json.loads(status_json.stdout)
            self.assertEqual(payload["schema_version"], "cross-agent-consensus-agent-status-1")
            self.assertEqual(payload["state"], "missing")
            self.assertIn("No monitored agent session exists", payload["message"])

    def test_invocation_ready_requires_all_same_round_reviewer_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            result = self.run_cli(
                "init",
                "--task",
                "Prompt completeness",
                "--artifact-locator",
                "README.md",
                "--author",
                "author-codex",
                "--orchestrator",
                "orchestrator-codex",
                "--reviewer",
                "reviewer-a",
                "--reviewer",
                "reviewer-b",
                "--allow-reviewer-config-override",
                "--human-supervisor",
                "none",
                "--run-root",
                str(tmp / "runs"),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run = tmp / "runs" / "prompt-completeness-consensus-001"

            result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-a",
                "--round",
                "round-1",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            prompt = run / "rounds" / "round-001" / "prompts" / "reviewers" / "reviewer-a.md"

            result = self.run_cli(
                "invocation-ready",
                "--run",
                str(run),
                "--actor",
                "reviewer-a",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-a.out"),
                "--approved",
                "--command",
                sys.executable,
                "--version",
            )
            self.assertEqual(result.returncode, 3)
            self.assertIn("reviewer-b", result.stderr)

    def test_invocation_ready_fails_closed_for_incomplete_or_unsafe_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            (run / "rounds" / "round-001" / "round.md").unlink()
            prompt = run / "rounds" / "round-001" / "prompts" / "reviewers" / "reviewer-draft.md"
            prompt.write_text("draft prompt\n", encoding="utf-8")

            result = self.run_cli(
                "invocation-ready",
                "--run",
                str(run),
                "--actor",
                "reviewer-codex",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(run / "not-payload.out"),
            )
            self.assertEqual(result.returncode, 3)
            self.assertIn("pre-execution", result.stderr)
            self.assertIn("draft prompts", result.stderr)
            self.assertIn("raw-output payload", result.stderr)
            self.assertIn("explicit runtime command", result.stderr)

    def test_players_probe_reports_generic_cli_capabilities(self) -> None:
        result = self.run_cli(
            "players",
            "probe",
            "--player",
            "generic-cli",
            "--json",
            "--command",
            "--",
            sys.executable,
            "--version",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], "cross-agent-consensus-player-capabilities-1")
        self.assertTrue(payload["executable"])
        self.assertEqual(payload["prompt_transports"], ["stdin"])
        self.assertEqual(payload["output_modes"], ["raw_stdout"])

    def test_invoke_agent_generic_cli_writes_session_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt = self.write_reviewer_prompt(run)
            raw_output = run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-codex.out"

            result = self.run_cli(
                "invoke-agent",
                "--run",
                str(run),
                "--round",
                "round-1",
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--player",
                "generic-cli",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(raw_output),
                "--command",
                "--",
                sys.executable,
                "-c",
                "import sys; prompt=sys.stdin.read(); print('final:' + prompt.splitlines()[0])",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            session = run / "rounds" / "round-001" / "agents" / "reviewer-codex" / "session-001"
            self.assertTrue(session.is_dir())
            invocation = json.loads((session / "invocation.json").read_text(encoding="utf-8"))
            self.assertEqual(invocation["schema_version"], "cross-agent-consensus-invocation-1")
            self.assertEqual(invocation["round_id"], "round-001")
            command = json.loads((session / "command.json").read_text(encoding="utf-8"))
            self.assertEqual(command["schema_version"], "cross-agent-consensus-command-1")
            self.assertEqual(command["prompt_transport"], "stdin")
            state = json.loads((session / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["state"], "completed")
            self.assertIsInstance(state["process_identity"], dict)
            self.assertEqual(state["process_identity"]["pid"], state["pid"])
            exit_payload = json.loads((session / "exit.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_payload["final_state"], "completed")
            self.assertEqual(exit_payload["exit_code_or_null"], 0)
            self.assertIn("final:# Cross-Agent Consensus Reviewer Prompt", (session / "final-output.md").read_text())
            self.assertIn("final:# Cross-Agent Consensus Reviewer Prompt", raw_output.read_text(encoding="utf-8"))
            agent_log = [json.loads(line) for line in (session / "agent.log").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(entry["native_type"] == "stdout_text" for entry in agent_log))
            events = [
                json.loads(line)["type"]
                for line in (session / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("prepared", events)
            self.assertIn("started", events)
            self.assertIn("stdout", events)
            self.assertIn("completed", events)

            status = self.run_cli(
                "agent-status",
                "--run",
                str(run),
                "--actor",
                "reviewer-codex",
                "--json",
            )
            self.assertEqual(status.returncode, 0, status.stderr + status.stdout)
            status_payload = json.loads(status.stdout)
            self.assertEqual(status_payload["schema_version"], "cross-agent-consensus-agent-status-1")
            self.assertEqual(status_payload["state"], "completed")
            self.assertEqual(status_payload["exit"]["final_state"], "completed")

    def test_invoke_agent_rejects_secret_argv_and_records_failed_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt = self.write_reviewer_prompt(run)
            raw_output = run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-codex.out"

            result = self.run_cli(
                "invoke-agent",
                "--run",
                str(run),
                "--round",
                "round-1",
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--player",
                "generic-cli",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(raw_output),
                "--approved",
                "--command",
                sys.executable,
                "--api-key=abc123",
            )
            self.assertEqual(result.returncode, 3)
            self.assertIn("secret-looking", result.stderr)
            session = run / "rounds" / "round-001" / "agents" / "reviewer-codex" / "session-001"
            command = json.loads((session / "command.json").read_text(encoding="utf-8"))
            self.assertEqual(command["argv"], [])
            self.assertTrue(command["argv_redacted"])
            self.assertIn("secret-looking", command["rejection_reason"])
            for path in session.rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"abc123", path.read_bytes(), str(path))
            state = json.loads((session / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["state"], "failed")
            self.assertIn("secret-looking", state["failure_reason_or_null"])
            exit_payload = json.loads((session / "exit.json").read_text(encoding="utf-8"))
            self.assertEqual(exit_payload["final_state"], "failed")
            events = [
                json.loads(line)["type"]
                for line in (session / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events, ["prepared", "failed"])

    def test_invoke_agent_rejects_prompt_from_other_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt_round_1 = self.write_reviewer_prompt(run)
            self.write_round_batch(run, 2, "regression_check")
            prompt_round_2_result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--round",
                "round-2",
            )
            self.assertEqual(prompt_round_2_result.returncode, 0, prompt_round_2_result.stderr + prompt_round_2_result.stdout)
            raw_output = run / "rounds" / "round-002" / "raw" / "reviewers" / "reviewer-codex.out"
            raw_output.parent.mkdir(parents=True, exist_ok=True)

            result = self.run_cli(
                "invoke-agent",
                "--run",
                str(run),
                "--round",
                "round-2",
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--player",
                "generic-cli",
                "--prompt",
                str(prompt_round_1),
                "--raw-output",
                str(raw_output),
                "--approved",
                "--command",
                "--",
                sys.executable,
                "-c",
                "import sys; sys.stdin.read(); print('must not run')",
            )
            self.assertEqual(result.returncode, 3)
            self.assertIn("--prompt must be under the selected round prompt directory", result.stderr)
            session = run / "rounds" / "round-002" / "agents" / "reviewer-codex" / "session-001"
            self.assertTrue(session.is_dir())
            state = json.loads((session / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["state"], "failed")
            self.assertFalse((session / "stdout.raw").is_file())

    def test_invoke_agent_rejects_raw_output_from_other_round(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            self.write_reviewer_prompt(run)
            self.write_round_batch(run, 2, "regression_check")
            prompt_round_2_result = self.run_cli(
                "prompt",
                "--run",
                str(run),
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--round",
                "round-2",
            )
            self.assertEqual(prompt_round_2_result.returncode, 0, prompt_round_2_result.stderr + prompt_round_2_result.stdout)
            prompt_round_2 = run / "rounds" / "round-002" / "prompts" / "reviewers" / "reviewer-codex.md"
            raw_output = run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-codex-cross-round.out"
            raw_output.parent.mkdir(parents=True, exist_ok=True)

            result = self.run_cli(
                "invoke-agent",
                "--run",
                str(run),
                "--round",
                "round-2",
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--player",
                "generic-cli",
                "--prompt",
                str(prompt_round_2),
                "--raw-output",
                str(raw_output),
                "--approved",
                "--command",
                "--",
                sys.executable,
                "-c",
                "import sys; sys.stdin.read(); print('must not run')",
            )
            self.assertEqual(result.returncode, 3)
            self.assertIn("--raw-output must be under the selected round raw-output directory", result.stderr)
            session = run / "rounds" / "round-002" / "agents" / "reviewer-codex" / "session-001"
            self.assertTrue(session.is_dir())
            state = json.loads((session / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["state"], "failed")
            self.assertFalse((session / "stdout.raw").is_file())

    def test_claude_cli_player_parses_stream_json_events_and_final_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt = self.write_reviewer_prompt(run)
            raw_output = run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-codex.out"
            script = (
                "import json,sys; sys.stdin.read(); "
                "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':'draft'}]}})); "
                "print(json.dumps({'type':'result','subtype':'success','result':'CLAUDE_FINAL'}))"
            )

            result = self.run_cli(
                "invoke-agent",
                "--run",
                str(run),
                "--round",
                "round-1",
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--player",
                "claude-cli",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(raw_output),
                "--approved",
                "--command",
                "--",
                sys.executable,
                "-c",
                script,
                "-p",
                "--verbose",
                "--output-format=stream-json",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            session = run / "rounds" / "round-001" / "agents" / "reviewer-codex" / "session-001"
            command = json.loads((session / "command.json").read_text(encoding="utf-8"))
            self.assertEqual(command["output_mode"], "stream_json")
            self.assertEqual((session / "final-output.md").read_text(encoding="utf-8"), "CLAUDE_FINAL\n")
            events = [json.loads(line) for line in (session / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            agent_events = [event for event in events if event["type"] == "agent_event"]
            self.assertEqual([event["native_type"] for event in agent_events], ["assistant", "result"])
            self.assertEqual([event["normalized_type"] for event in agent_events], ["message", "final"])
            agent_log = [json.loads(line) for line in (session / "agent.log").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([entry["native_type"] for entry in agent_log], ["assistant", "result"])
            self.assertEqual(agent_log[0]["native_event"]["message"]["content"][0]["text"], "draft")

    def test_claude_cli_player_rejects_stream_json_without_verbose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt = self.write_reviewer_prompt(run)
            raw_output = run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-codex.out"

            result = self.run_cli(
                "invoke-agent",
                "--run",
                str(run),
                "--round",
                "round-1",
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--player",
                "claude-cli",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(raw_output),
                "--approved",
                "--command",
                "--",
                sys.executable,
                "-c",
                "import sys; sys.stdin.read()",
                "-p",
                "--output-format=stream-json",
            )
            self.assertEqual(result.returncode, 3)
            self.assertIn("requires --verbose", result.stderr)

    def test_codex_cli_player_parses_json_events_and_message_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt = self.write_reviewer_prompt(run)
            raw_output = run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-codex.out"
            script = (
                "import json,sys; sys.stdin.read(); "
                "print(json.dumps({'msg':{'type':'task_started'}})); "
                "print(json.dumps({'type':'item.started','item':{'type':'command_execution','status':'in_progress','command':'true'}})); "
                "print(json.dumps({'type':'item.completed','item':{'type':'command_execution','status':'completed','command':'true','exit_code':0}})); "
                "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'CODEX_FINAL'}})); "
                "print(json.dumps({'type':'turn.completed'}))"
            )

            result = self.run_cli(
                "invoke-agent",
                "--run",
                str(run),
                "--round",
                "round-1",
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--player",
                "codex-cli",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(raw_output),
                "--approved",
                "--command",
                "--",
                sys.executable,
                "-c",
                script,
                "--json",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            session = run / "rounds" / "round-001" / "agents" / "reviewer-codex" / "session-001"
            command = json.loads((session / "command.json").read_text(encoding="utf-8"))
            self.assertEqual(command["output_mode"], "stream_json")
            self.assertEqual((session / "final-output.md").read_text(encoding="utf-8"), "CODEX_FINAL\n")
            events = [json.loads(line) for line in (session / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            native_types = [event["native_type"] for event in events if event["type"] == "agent_event"]
            self.assertEqual(native_types, ["task_started", "item.started", "item.completed", "item.completed", "turn.completed"])
            normalized_types = [event["normalized_type"] for event in events if event["type"] == "agent_event"]
            self.assertIn("tool_call", normalized_types)
            self.assertIn("tool_result", normalized_types)
            self.assertIn("message", normalized_types)
            self.assertEqual(normalized_types[-1], "final")
            agent_log = [json.loads(line) for line in (session / "agent.log").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [entry["native_type"] for entry in agent_log],
                ["task_started", "item.started", "item.completed", "item.completed", "turn.completed"],
            )

    def test_codex_cli_player_rejects_command_without_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt = self.write_reviewer_prompt(run)
            raw_output = run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-codex.out"

            result = self.run_cli(
                "invoke-agent",
                "--run",
                str(run),
                "--round",
                "round-1",
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--player",
                "codex-cli",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(raw_output),
                "--approved",
                "--command",
                "--",
                sys.executable,
                "-c",
                "import sys; sys.stdin.read()",
            )
            self.assertEqual(result.returncode, 3)
            self.assertIn("requires --json", result.stderr)

    def test_agent_status_observes_stale_process_and_cancel_stops_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt = self.write_reviewer_prompt(run)
            raw_output = run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-codex.out"
            child = subprocess.Popen(
                [
                    str(CLI),
                    "invoke-agent",
                    "--run",
                    str(run),
                    "--round",
                    "round-1",
                    "--phase",
                    "reviewer",
                    "--actor",
                    "reviewer-codex",
                    "--player",
                    "generic-cli",
                    "--prompt",
                    str(prompt),
                    "--raw-output",
                    str(raw_output),
                    "--approved",
                    "--idle-timeout-seconds",
                    "0.2",
                    "--stale-timeout-seconds",
                    "0.4",
                    "--heartbeat-interval-seconds",
                    "0.1",
                    "--command",
                    sys.executable,
                    "-c",
                    "import sys,time; sys.stdin.read(); time.sleep(20)",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            session = run / "rounds" / "round-001" / "agents" / "reviewer-codex" / "session-001"
            try:
                deadline = time.monotonic() + 5
                state_payload: dict[str, object] = {}
                while time.monotonic() < deadline:
                    if (session / "state.json").is_file():
                        status = self.run_cli(
                            "agent-status",
                            "--run",
                            str(run),
                            "--actor",
                            "reviewer-codex",
                            "--json",
                        )
                        self.assertEqual(status.returncode, 0, status.stderr + status.stdout)
                        state_payload = json.loads(status.stdout)
                        if state_payload.get("state") == "stale":
                            break
                    time.sleep(0.05)
                self.assertEqual(state_payload.get("state"), "stale")

                cancel = self.run_cli(
                    "agent-cancel",
                    "--run",
                    str(run),
                    "--actor",
                    "reviewer-codex",
                    "--reason",
                    "test cancellation",
                    "--grace-seconds",
                    "0.05",
                )
                self.assertEqual(cancel.returncode, 0, cancel.stderr + cancel.stdout)
                stdout, stderr = child.communicate(timeout=5)
                self.assertNotEqual(child.returncode, 0, stdout + stderr)
                final_state = json.loads((session / "state.json").read_text(encoding="utf-8"))
                self.assertEqual(final_state["state"], "cancelled")
                events = [
                    json.loads(line)["type"]
                    for line in (session / "events.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                self.assertIn("idle", events)
                self.assertIn("stale", events)
                self.assertIn("cancel_requested", events)
                self.assertIn("cancelled", events)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate(timeout=5)

    def test_agent_cancel_refuses_terminal_session_without_appending_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            run = self.init_run(Path(tmp_name), "--unattended-invocation", "--unattended-scope", "test")
            prompt = self.write_reviewer_prompt(run)
            raw_output = run / "rounds" / "round-001" / "raw" / "reviewers" / "reviewer-codex.out"

            result = self.run_cli(
                "invoke-agent",
                "--run",
                str(run),
                "--round",
                "round-1",
                "--phase",
                "reviewer",
                "--actor",
                "reviewer-codex",
                "--player",
                "generic-cli",
                "--prompt",
                str(prompt),
                "--raw-output",
                str(raw_output),
                "--approved",
                "--command",
                "--",
                sys.executable,
                "-c",
                "import sys; sys.stdin.read(); print('done')",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            cancel = self.run_cli(
                "agent-cancel",
                "--run",
                str(run),
                "--actor",
                "reviewer-codex",
                "--reason",
                "too late",
            )
            self.assertEqual(cancel.returncode, 2)
            self.assertIn("session already terminal", cancel.stderr)
            session = run / "rounds" / "round-001" / "agents" / "reviewer-codex" / "session-001"
            events = [
                json.loads(line)["type"]
                for line in (session / "events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[-1], "completed")
            self.assertNotIn("failed", events)

    def test_terminate_consensus_with_passed_validator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp, "--profile", "implementation-test", "--validator", "smoke_validator")
            raw = tmp / "validator.out"
            raw.write_text("pass\n", encoding="utf-8")

            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "validator",
                "--actor",
                "validator-local",
                "--artifact-version",
                "v1",
                "--validator-id",
                "smoke_validator",
                "--result",
                "pass",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            result = self.run_cli(
                "terminate",
                "--run",
                str(run),
                "--terminal-condition",
                "consensus_reached",
                "--final-artifact-version",
                "v1",
                "--reason",
                "All required validators passed and no unresolved blocking findings were recorded.",
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((run / "report.md").is_file())
            result = self.run_cli("validate", "--run", str(run), "--terminal")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_terminate_refuses_invalid_waived_validator_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            run = self.init_run(tmp, "--profile", "implementation-test", "--validator", "smoke_validator")
            raw = tmp / "validator.out"
            raw.write_text("waived\n", encoding="utf-8")

            result = self.run_cli(
                "capture",
                "--run",
                str(run),
                "--phase",
                "validator",
                "--actor",
                "validator-local",
                "--artifact-version",
                "v1",
                "--validator-id",
                "smoke_validator",
                "--result",
                "waived",
                "--source-file",
                str(raw),
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

            result = self.run_cli(
                "terminate",
                "--run",
                str(run),
                "--terminal-condition",
                "consensus_reached",
                "--final-artifact-version",
                "v1",
                "--reason",
                "Invalid waiver should block.",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("waived validator missing authority", result.stdout)
            self.assertFalse((run / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
