"""Exercise the real hosted validation plan without allocating release state."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/native-container-check.yml"


class NativeValidationPlanTests(unittest.TestCase):
    """Run the workflow's actual plan step against a real isolated Git checkout."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "scripts").mkdir()
        for name in ("version_plan.py", "release_control.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.root / "scripts" / name)
        self.policy = {
            "repository": "example/native-check",
            "mode": "release",
            "version_file": "VERSION",
            "versioning": {
                "schema": 1,
                "promotion": "promote-bytes",
                "files": [
                    {"path": "VERSION", "format": "text", "value": "package"},
                    {"path": "companion", "format": "text", "value": "package"},
                ],
            },
        }
        (self.root / ".release-policy.json").write_text(json.dumps(self.policy))
        (self.root / "VERSION").write_text("1.2.3\n")
        (self.root / "companion").write_text("1.2.3\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=Native validation test",
            "-c",
            "user.email=native-validation@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        )
        self.source = self.git("rev-parse", "HEAD").stdout.strip()
        workflow = WORKFLOW.read_text()
        marker = "python3 - <<'PY'\n"
        self.assertEqual(workflow.count(marker), 1)
        self.code = textwrap.dedent(workflow.split(marker)[1].split("\n        PY")[0])
        self.output = self.root / "step-output"

    def git(self, *arguments):
        """Use only fixture-local Git operations."""
        return subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )

    def prepare(self, expected_source=None):
        """Execute the exact inline hosted script without a GitHub token."""
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"GH_TOKEN", "GITHUB_TOKEN", "BOT_PAT"}
        }
        environment.update(
            EXPECTED_SOURCE_SHA=expected_source or self.source,
            GITHUB_OUTPUT=str(self.output),
            PYTHONDONTWRITEBYTECODE="1",
        )
        return subprocess.run(
            [sys.executable, "-c", self.code],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_plan_is_deterministic_source_bound_and_does_not_allocate(self):
        """Only local evidence is written; inputs, HEAD and refs remain unchanged."""
        refs = self.git("show-ref").stdout
        original = {
            name: (self.root / name).read_bytes()
            for name in ("VERSION", "companion", ".release-policy.json")
        }
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.root / ".release-plan.json"
        raw = path.read_bytes()
        plan = json.loads(raw)
        self.assertEqual(plan["source_sha"], self.source)
        self.assertEqual(plan["base_version"], "1.2.3")
        self.assertEqual(plan["version"], "1.2.3-beta.1")
        policy_hash = hashlib.sha256(
            json.dumps(self.policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(plan["policy_sha256"], policy_hash)
        self.assertIsNone(plan["build_number"])
        self.assertEqual(self.output.read_text(), "version=1.2.3\n")
        self.assertEqual(self.git("show-ref").stdout, refs)
        self.assertEqual(self.git("rev-parse", "HEAD").stdout.strip(), self.source)
        self.assertEqual(self.git("diff", "HEAD").stdout, "")
        for name, contents in original.items():
            self.assertEqual((self.root / name).read_bytes(), contents)
        path.unlink()
        self.output.unlink()
        repeated = self.prepare()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(path.read_bytes(), raw)

    def test_wrong_event_source_cannot_create_a_plan(self):
        """A PR head/merge SHA mixup must fail before storing an artifact."""
        result = self.prepare("f" * 40)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source SHA mismatch", result.stderr)
        self.assertFalse((self.root / ".release-plan.json").exists())
        self.assertFalse(self.output.exists())

    def test_dirty_policy_cannot_be_bound_to_committed_source(self):
        """The frozen policy must come from the same exact checkout as the source."""
        self.policy["notes"] = ["uncommitted policy change"]
        (self.root / ".release-policy.json").write_text(json.dumps(self.policy))
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".release-plan.json").exists())
        self.assertFalse(self.output.exists())

    def test_stale_plan_is_not_overwritten(self):
        """A stale or cross-attempt local plan cannot silently replace provenance."""
        path = self.root / ".release-plan.json"
        path.write_text("preserve existing evidence\n")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), "preserve existing evidence\n")
        self.assertFalse(self.output.exists())

    def test_committed_version_disagreement_cannot_create_a_plan(self):
        """Validation must catch an inconsistent candidate instead of masking it."""
        (self.root / "companion").write_text("1.2.4\n")
        self.git("add", "companion")
        self.git(
            "-c",
            "user.name=Native validation test",
            "-c",
            "user.email=native-validation@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "inconsistent version",
        )
        self.source = self.git("rev-parse", "HEAD").stdout.strip()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".release-plan.json").exists())
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
