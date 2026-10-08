"""Exercise real Bandit discovery with repository and worktree Git layouts."""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = (".git", ".venv", ".venv-ci", "tests", "release-dist", "dist", "build")
SOURCES = (
    "app.py",
    ".github/security_probe.py",
    "build_helpers.py",
    "tests_support.py",
    ".venv_helpers.py",
    "dist_helpers.py",
    "release-dist-helper.py",
)


class BanditDiscoveryTests(unittest.TestCase):
    """Keep generated outputs excluded without hiding neighboring source files."""

    def configured_exclusions(self):
        commands = []
        for relative in ("scripts/ci.sh", ".github/workflows/release-security.yml"):
            lines = (ROOT / relative).read_text().splitlines()
            commands.extend(line for line in lines if "-m bandit " in line)
        self.assertEqual(len(commands), 2)
        values = []
        for command in commands:
            arguments = shlex.split(command)
            values.append(arguments[arguments.index("-x") + 1])
        self.assertEqual(values[0], values[1], "local and hosted discovery must agree")
        return values[0]

    def check_discovery(self, *, worktree):
        exclusions = self.configured_exclusions()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in EXCLUDED:
                if directory == ".git" and worktree:
                    (root / directory).write_text("gitdir: /fixture/worktrees/example\n")
                    continue
                (root / directory).mkdir()
                (root / directory / "dependency.py").write_text("VALUE = 1\n")
            for name in SOURCES:
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("VALUE = 1\n")
            report_path = root / "bandit-results.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "bandit",
                    "-r",
                    ".",
                    "-x",
                    exclusions,
                    "-f",
                    "json",
                    "-o",
                    str(report_path),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(report_path.read_text())
            self.assertEqual(report["errors"], [])
            scanned = {name.removeprefix("./") for name in report["metrics"] if name != "_totals"}
            self.assertEqual(scanned, set(SOURCES))

    def test_regular_checkout(self):
        self.check_discovery(worktree=False)

    def test_git_worktree(self):
        self.check_discovery(worktree=True)
