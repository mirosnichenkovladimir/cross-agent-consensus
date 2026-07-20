from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install-cac"


def installer_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "HERMES_HOME": str(home / ".hermes"),
            "CODEX_HOME": str(home / ".codex"),
            "CLAUDE_HOME": str(home / ".claude"),
            "KIMI_CODE_HOME": str(home / ".kimi-code"),
        }
    )
    return environment


def run_installer(
    environment: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALLER), *arguments],
        cwd=str(REPO_ROOT),
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


class InstallerHarnessTargetTests(unittest.TestCase):
    def test_kimi_target_installs_under_kimi_code_home_and_passes_selftest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            home = Path(tmp_name)
            environment = installer_environment(home)

            completed = run_installer(environment, "--target", "kimi")
            installed = home / ".kimi-code" / "skills" / "cross-agent-consensus"

            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr + completed.stdout,
            )
            self.assertIn("kimi [OK]", completed.stdout)
            self.assertEqual(
                (installed / "VERSION").read_text(encoding="utf-8").strip(),
                "0.20.0",
            )
            self.assertTrue((installed / "SKILL.md").is_file())

    def test_all_target_installs_every_supported_agent_harness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_name:
            home = Path(tmp_name)
            environment = installer_environment(home)

            completed = run_installer(
                environment,
                "--target",
                "all",
                "--no-selftest",
            )

            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr + completed.stdout,
            )
            for harness_home in (".hermes", ".codex", ".claude", ".kimi-code"):
                version = (
                    home / harness_home / "skills" / "cross-agent-consensus" / "VERSION"
                )
                self.assertEqual(version.read_text(encoding="utf-8").strip(), "0.20.0")


if __name__ == "__main__":
    unittest.main()
