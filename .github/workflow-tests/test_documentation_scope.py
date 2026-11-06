"""Keep documentation PR routing separate from release qualification."""

import importlib.util
import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("change_scope", ROOT / "scripts/change_scope.py")
SCOPE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCOPE)


def workflow(name):
    """Load a committed workflow without YAML boolean coercion."""
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def classifier(document):
    """Locate the canonical scope decision step."""
    return next(step for step in document["jobs"]["scope"]["steps"] if step.get("id") == "scope")


class DocumentationScopeTests(unittest.TestCase):
    """Exercise required inputs and full release qualification."""

    def test_release_qualification_is_not_skipped_for_documentation(self):
        release = workflow("release-pipeline.yml")
        self.assertEqual(classifier(release)["env"]["FORCE_FULL"], "true")
        self.assertEqual(release["jobs"]["checks"]["with"]["force-full"], "true")
        quality = workflow("quality-gate.yml")
        self.assertEqual(
            classifier(quality)["env"]["FORCE_FULL"],
            "${{ inputs.force-full && 'true' || 'false' }}",
        )
        self.assertNotIn("if", quality["jobs"]["workflow-contracts"])
        for job in quality["jobs"].values():
            if job.get("uses") in {
                "./.github/workflows/codeql.yml",
                "./.github/workflows/release-security.yml",
            }:
                self.assertNotIn("if", job)

    def test_declared_release_and_build_inputs_cannot_use_documentation_fast_path(self):
        policy = json.loads((ROOT / ".release-policy.json").read_text())
        required = frozenset(policy["change_scope"]["required_paths"])
        self.assertIn(policy["release_notes"], required)
        configured = classifier(workflow("quality-gate.yml"))["env"]["REQUIRED_PATHS"]
        self.assertEqual(frozenset(json.loads(configured)), required)
        for name in required | {"docs/guide.md"}:
            for change in ("M", "D"):
                raw = (
                    b":100644 "
                    + (b"100644" if change == "M" else b"000000")
                    + b" "
                    + b"1" * 40
                    + b" "
                    + (b"2" if change == "M" else b"0") * 40
                    + b" "
                    + change.encode()
                    + b"\0"
                    + name.encode()
                    + b"\0"
                )
                result = SCOPE.classify_diff(raw, frozenset(), required)
                expected = (
                    {"run": True, "reason": "required-path-change"}
                    if name in required
                    else {"run": False, "reason": "documentation-only"}
                )
                self.assertEqual(result, expected)
