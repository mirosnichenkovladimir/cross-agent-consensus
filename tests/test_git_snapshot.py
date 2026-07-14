from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "skills" / "cross-agent-consensus"
sys.path.insert(0, str(PACKAGE_ROOT))

from cross_agent_consensus.git_snapshot import (
    cmd_snapshot_git,
    materialize_git_change_snapshot,
)
from cross_agent_consensus.draft_promotion import cmd_promote_draft
from cross_agent_consensus.records import parse_run_records, records_by_type
from cross_agent_consensus.validation import check_integrity
from test_integrity_audit import _stage_run


def git(repository: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)


def initialized_repository(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.email", "cac@example.test")
    git(repository, "config", "user.name", "CAC Test")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repository / "rename-me.txt").write_text("rename\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "base")
    return repository


class GitSnapshotTests(unittest.TestCase):
    def test_worktree_snapshot_materializes_staged_unstaged_rename_and_untracked_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repository = initialized_repository(tmp)
            run, _artifact = _stage_run(tmp)
            (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
            git(repository, "add", "staged.txt")
            git(repository, "mv", "rename-me.txt", "renamed.txt")
            (repository / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
            (repository / "untracked.bin").write_bytes(b"\x00untracked\xff")
            (repository / "untracked-link").symlink_to("tracked.txt")
            (repository / "subdir").mkdir()

            snapshot, digest = materialize_git_change_snapshot(
                run, repository / "subdir", base_ref="HEAD", target_ref=None
            )
            manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
            staged_patch = (snapshot / "staged.patch").read_bytes()
            unstaged_patch = (snapshot / "unstaged.patch").read_bytes()
            untracked_content = (snapshot / "untracked" / "untracked.bin").read_bytes()
            link_entry = next(
                entry
                for entry in manifest["files"]
                if entry["path"] == "untracked/untracked-link"
            )

        self.assertEqual(manifest["snapshot_sha256"], digest)
        self.assertEqual(manifest["repository_root"], str(repository.resolve()))
        self.assertIn(b"staged.txt", staged_patch)
        self.assertIn(b"renamed.txt", staged_patch)
        self.assertIn(b"tracked.txt", unstaged_patch)
        self.assertEqual(untracked_content, b"\x00untracked\xff")
        self.assertEqual(link_entry["git_mode_or_null"], "120000")

    def test_empty_and_target_revision_snapshots_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repository = initialized_repository(tmp)
            run, _artifact = _stage_run(tmp)
            empty_one = materialize_git_change_snapshot(
                run, repository, base_ref="HEAD", target_ref=None
            )
            empty_two = materialize_git_change_snapshot(
                run, repository, base_ref="HEAD", target_ref=None
            )
            (repository / "tracked.txt").write_text("target\n", encoding="utf-8")
            git(repository, "add", "tracked.txt")
            git(repository, "commit", "-qm", "target")
            target = materialize_git_change_snapshot(
                run, repository, base_ref="HEAD^", target_ref="HEAD"
            )
            empty_staged = (empty_one[0] / "staged.patch").read_bytes()
            target_patch = (target[0] / "target.patch").read_bytes()

        self.assertEqual(empty_one, empty_two)
        self.assertEqual(empty_staged, b"")
        self.assertIn(b"target", target_patch)

    def test_concurrent_worktree_mutation_aborts_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repository = initialized_repository(tmp)
            run, _artifact = _stage_run(tmp)

            with self.assertRaisesRegex(ValueError, "changed while CAC captured"):
                materialize_git_change_snapshot(
                    run,
                    repository,
                    base_ref="HEAD",
                    target_ref=None,
                    mutation_probe=lambda: (repository / "tracked.txt").write_text(
                        "mutated during capture\n", encoding="utf-8"
                    ),
                )

        self.assertEqual(list((run / "snapshots").glob("git-change-*")), [])

    def test_snapshot_command_writes_artifact_and_manifest_drift_blocks_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repository = initialized_repository(tmp)
            run, _artifact = _stage_run(tmp)
            (repository / "tracked.txt").write_text("review this\n", encoding="utf-8")
            args = argparse.Namespace(
                run=str(run),
                repository=str(repository),
                base_ref="HEAD",
                target_ref=None,
                artifact_version="v2",
                predecessor="v1",
                produced_by="author",
                actor="orchestrator-git-snapshot",
            )

            self.assertEqual(cmd_snapshot_git(args), 0)
            records = parse_run_records(run)
            artifact = next(
                record
                for record in records_by_type(records, "ArtifactVersion")
                if record.data.get("artifact_version_id") == "v2"
            )
            manifest = run / str(artifact.data["content_locator"])
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            draft = tmp / "stale-review.json"
            draft.write_text(
                json.dumps(
                    {
                        "kind": "reviewer_findings",
                        "review_text": "stale",
                        "findings": [],
                    }
                ),
                encoding="utf-8",
            )
            manifest.write_text(
                json.dumps({**payload, "snapshot_sha256": "0" * 64}),
                encoding="utf-8",
            )
            promotion = argparse.Namespace(
                run=str(run),
                source_file=str(draft),
                actor="reviewer",
                round="round-1",
                artifact_version="v2",
                review_batch="review-batch-round-1-fresh_review",
                validator_id=None,
                predecessor=None,
                content_locator=None,
                source_record=[],
                allow_manual_source=True,
            )
            stale_code = cmd_promote_draft(promotion)

        self.assertEqual(artifact.data["git_change_snapshot_sha256"], payload["snapshot_sha256"])
        self.assertEqual(artifact.data["predecessor_id_or_null"], "v1")
        self.assertEqual(stale_code, 1)

    def test_snapshot_member_drift_blocks_integrity_and_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            repository = initialized_repository(tmp)
            run, _artifact = _stage_run(tmp)
            (repository / "tracked.txt").write_text("review this\n", encoding="utf-8")
            args = argparse.Namespace(
                run=str(run),
                repository=str(repository),
                base_ref="HEAD",
                target_ref=None,
                artifact_version="v2",
                predecessor="v1",
                produced_by="author",
                actor="orchestrator-git-snapshot",
            )
            self.assertEqual(cmd_snapshot_git(args), 0)
            artifact = next(
                record
                for record in records_by_type(parse_run_records(run), "ArtifactVersion")
                if record.data.get("artifact_version_id") == "v2"
            )
            manifest = run / str(artifact.data["content_locator"])
            (manifest.parent / "unstaged.patch").write_bytes(b"tampered snapshot bytes\n")
            draft = tmp / "validator.json"
            draft.write_text(
                json.dumps(
                    {
                        "kind": "validator_output",
                        "result": "pass",
                        "evidence": "snapshot bytes are stable",
                    }
                ),
                encoding="utf-8",
            )
            promotion = argparse.Namespace(
                run=str(run),
                source_file=str(draft),
                actor="validator-tests",
                round="round-1",
                artifact_version="v2",
                review_batch=None,
                validator_id="tests",
                predecessor=None,
                content_locator=None,
                source_record=[],
                allow_manual_source=True,
            )

            integrity = check_integrity(run)
            promotion_code = cmd_promote_draft(promotion)

        self.assertFalse(integrity.ok)
        self.assertTrue(
            any("Git snapshot member" in message and "changed" in message for message in integrity.messages),
            integrity.messages,
        )
        self.assertEqual(promotion_code, 1)


if __name__ == "__main__":
    unittest.main()
