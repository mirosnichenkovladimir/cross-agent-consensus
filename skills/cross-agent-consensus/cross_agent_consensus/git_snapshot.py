"""Immutable Git change snapshots for CAC code-review ArtifactVersions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from cross_agent_consensus.integrity import canonical_json_sha256
from cross_agent_consensus.io import atomic_write_new, sha256_file, utc_now
from cross_agent_consensus.markdown_records import frontmatter
from cross_agent_consensus.records import parse_run_records, records_by_type
from cross_agent_consensus.run_audit import locked_run_command


@dataclass(frozen=True)
class GitCapture:
    repository_root: Path
    base_revision: str
    target_revision_or_null: str | None
    mode: str
    staged_patch: bytes
    unstaged_patch: bytes
    target_patch: bytes
    untracked_files: tuple[tuple[str, str, bytes], ...]

    def fingerprint(self) -> str:
        return canonical_json_sha256(
            {
                "repository_root": str(self.repository_root),
                "base_revision": self.base_revision,
                "target_revision_or_null": self.target_revision_or_null,
                "mode": self.mode,
                "staged_patch_sha256": hashlib.sha256(self.staged_patch).hexdigest(),
                "unstaged_patch_sha256": hashlib.sha256(self.unstaged_patch).hexdigest(),
                "target_patch_sha256": hashlib.sha256(self.target_patch).hexdigest(),
                "untracked_files": [
                    {
                        "path": path,
                        "git_mode": git_mode,
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                    for path, git_mode, content in self.untracked_files
                ],
            }
        )


def verify_git_change_snapshot(manifest_path: Path, expected_sha256: str) -> str:
    """Verify the descriptor and every byte-bearing member of one Git snapshot."""

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Git snapshot manifest is unreadable: {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise ValueError(f"Git snapshot manifest has no files list: {manifest_path}")
    declared_paths: set[str] = set()
    for index, entry in enumerate(manifest["files"], 1):
        if not isinstance(entry, dict):
            raise ValueError(f"Git snapshot manifest file {index} is not an object")
        path_value = entry.get("path")
        if not isinstance(path_value, str):
            raise ValueError(f"Git snapshot manifest file {index} has no path")
        relative = PurePosixPath(path_value)
        if relative.is_absolute() or ".." in relative.parts or path_value == "manifest.json":
            raise ValueError(f"unsafe Git snapshot member path: {path_value!r}")
        if path_value in declared_paths:
            raise ValueError(f"duplicate Git snapshot member path: {path_value}")
        declared_paths.add(path_value)
        member = manifest_path.parent.joinpath(*relative.parts)
        if member.is_symlink() or not member.is_file():
            raise ValueError(f"Git snapshot member is missing or not a regular file: {path_value}")
        content = member.read_bytes()
        if entry.get("bytes") != len(content):
            raise ValueError(f"Git snapshot member byte count changed: {path_value}")
        if entry.get("sha256") != hashlib.sha256(content).hexdigest():
            raise ValueError(f"Git snapshot member sha256 changed: {path_value}")
    actual_paths = {
        path.relative_to(manifest_path.parent).as_posix()
        for path in manifest_path.parent.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual_paths != declared_paths:
        missing = sorted(declared_paths - actual_paths)
        extra = sorted(actual_paths - declared_paths)
        raise ValueError(
            f"Git snapshot member inventory changed: missing={missing}, extra={extra}"
        )
    descriptor = {
        key: value
        for key, value in manifest.items()
        if key not in {"snapshot_id", "snapshot_sha256"}
    }
    actual_sha256 = canonical_json_sha256(descriptor)
    if manifest.get("snapshot_sha256") != actual_sha256:
        raise ValueError("Git snapshot descriptor sha256 changed")
    if expected_sha256 != actual_sha256:
        raise ValueError(
            f"Git snapshot sha256 differs: expected={expected_sha256}, actual={actual_sha256}"
        )
    if manifest.get("snapshot_id") != f"git-change-{actual_sha256[:16]}":
        raise ValueError("Git snapshot identifier does not match its descriptor")
    return actual_sha256


def git_bytes(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0"},
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"git {' '.join(arguments)} failed: {stderr}")
    return completed.stdout


def resolved_git_revision(repository: Path, revision: str) -> str:
    return git_bytes(repository, "rev-parse", "--verify", f"{revision}^{{commit}}").decode(
        "ascii"
    ).strip()


def untracked_files(repository: Path) -> tuple[tuple[str, str, bytes], ...]:
    raw_paths = git_bytes(
        repository, "ls-files", "--others", "--exclude-standard", "-z"
    )
    captured: list[tuple[str, str, bytes]] = []
    for raw_path in sorted(part for part in raw_paths.split(b"\0") if part):
        path_text = raw_path.decode("utf-8", errors="surrogateescape")
        relative = PurePosixPath(path_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"unsafe untracked Git path: {path_text!r}")
        source = repository.joinpath(*relative.parts)
        if source.is_symlink():
            content = os.readlink(source).encode("utf-8", errors="surrogateescape")
            git_mode = "120000"
        elif source.is_file():
            content = source.read_bytes()
            git_mode = "100755" if source.stat().st_mode & stat.S_IXUSR else "100644"
        else:
            raise ValueError(f"untracked Git path is not a regular file or symlink: {path_text}")
        captured.append((path_text, git_mode, content))
    return tuple(captured)


def capture_git_change(
    repository: Path,
    *,
    base_ref: str,
    target_ref: str | None,
) -> GitCapture:
    root = Path(
        git_bytes(repository, "rev-parse", "--show-toplevel")
        .decode("utf-8", errors="strict")
        .strip()
    ).resolve()
    base_revision = resolved_git_revision(root, base_ref)
    common_diff = ("--binary", "--full-index", "--find-renames", "--no-ext-diff")
    if target_ref is not None:
        target_revision = resolved_git_revision(root, target_ref)
        return GitCapture(
            repository_root=root,
            base_revision=base_revision,
            target_revision_or_null=target_revision,
            mode="target_revision",
            staged_patch=b"",
            unstaged_patch=b"",
            target_patch=git_bytes(
                root, "diff", *common_diff, base_revision, target_revision
            ),
            untracked_files=(),
        )
    return GitCapture(
        repository_root=root,
        base_revision=base_revision,
        target_revision_or_null=None,
        mode="worktree",
        staged_patch=git_bytes(root, "diff", *common_diff, "--cached", base_revision),
        unstaged_patch=git_bytes(root, "diff", *common_diff),
        target_patch=b"",
        untracked_files=untracked_files(root),
    )


def materialize_git_change_snapshot(
    run: Path,
    repository: Path,
    *,
    base_ref: str,
    target_ref: str | None,
    mutation_probe: Callable[[], None] | None = None,
) -> tuple[Path, str]:
    first = capture_git_change(repository, base_ref=base_ref, target_ref=target_ref)
    if mutation_probe is not None:
        mutation_probe()
    second = capture_git_change(repository, base_ref=base_ref, target_ref=target_ref)
    if first.fingerprint() != second.fingerprint():
        raise ValueError("Git repository changed while CAC captured the change snapshot")

    content_entries = {
        "staged.patch": first.staged_patch,
        "unstaged.patch": first.unstaged_patch,
        "target.patch": first.target_patch,
        **{
            f"untracked/{path}": content
            for path, _git_mode, content in first.untracked_files
        },
    }
    untracked_modes = {
        f"untracked/{path}": git_mode
        for path, git_mode, _content in first.untracked_files
    }
    descriptor = {
        "schema_version": "cross-agent-consensus-git-change-snapshot-1",
        "repository_root": str(first.repository_root),
        "base_ref": base_ref,
        "base_revision": first.base_revision,
        "target_ref_or_null": target_ref,
        "target_revision_or_null": first.target_revision_or_null,
        "mode": first.mode,
        "files": [
            {
                "path": path,
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "git_mode_or_null": untracked_modes.get(path),
            }
            for path, content in sorted(content_entries.items())
        ],
    }
    snapshot_sha256 = canonical_json_sha256(descriptor)
    snapshot_id = f"git-change-{snapshot_sha256[:16]}"
    snapshots_root = run / "snapshots"
    snapshots_root.mkdir(parents=True, exist_ok=True)
    target = snapshots_root / snapshot_id
    if target.exists():
        try:
            verify_git_change_snapshot(target / "manifest.json", snapshot_sha256)
        except ValueError as exc:
            raise ValueError(f"existing Git snapshot directory conflicts: {target}: {exc}") from exc
        return target, snapshot_sha256

    temporary = Path(tempfile.mkdtemp(prefix=".git-change-", dir=str(snapshots_root)))
    try:
        for relative_path, content in content_entries.items():
            output = temporary / relative_path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
        manifest = {**descriptor, "snapshot_id": snapshot_id, "snapshot_sha256": snapshot_sha256}
        (temporary / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        verify_git_change_snapshot(temporary / "manifest.json", snapshot_sha256)
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target, snapshot_sha256


@locked_run_command("git_change_snapshot_created")
def cmd_snapshot_git(args: argparse.Namespace) -> int:
    run = Path(args.run)
    try:
        snapshot, snapshot_sha256 = materialize_git_change_snapshot(
            run,
            Path(args.repository),
            base_ref=args.base_ref,
            target_ref=args.target_ref,
        )
        artifact_path = run / "artifacts" / f"{args.artifact_version}.md"
        existing = [
            record
            for record in records_by_type(parse_run_records(run), "ArtifactVersion")
            if record.data.get("artifact_version_id") == args.artifact_version
        ]
        if existing:
            if existing[0].data.get("git_change_snapshot_sha256") != snapshot_sha256:
                raise ValueError(
                    f"ArtifactVersion {args.artifact_version} already references another snapshot"
                )
            args.suppress_run_event = True
            print(f"Git snapshot already promoted: {snapshot}")
            return 0
        manifest = snapshot / "manifest.json"
        relative_manifest = manifest.relative_to(run)
        record = {
            "record_type": "ArtifactVersion",
            "schema_version": "m2-markdown-2",
            "run_id": run.name,
            "actor_identity": args.actor,
            "created_at": utc_now(),
            "artifact_version_id": args.artifact_version,
            "predecessor_id_or_null": args.predecessor,
            "content_locator": str(relative_manifest),
            "content_hash_or_null": sha256_file(manifest),
            "content_locator_base_or_null": str(run.resolve()),
            "produced_by": args.produced_by,
            "git_change_snapshot_id": snapshot.name,
            "git_change_snapshot_sha256": snapshot_sha256,
        }
        atomic_write_new(
            artifact_path,
            "\n".join(
                [
                    frontmatter(record),
                    "",
                    f"# Git Change Artifact {args.artifact_version}",
                    "",
                    f"- snapshot: `{snapshot.relative_to(run)}`",
                    f"- snapshot_sha256: `{snapshot_sha256}`",
                    "",
                ]
            ),
        )
        args.git_change_snapshot_sha256 = snapshot_sha256
        print(f"created Git snapshot: {snapshot}")
        print(f"created artifact: {artifact_path}")
        print(f"snapshot sha256: {snapshot_sha256}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
