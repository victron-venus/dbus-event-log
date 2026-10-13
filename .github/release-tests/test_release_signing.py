# Vendored release toolkit; change the toolkit source, then render again.
# ruff: noqa
# mypy: ignore-errors
# pylint: skip-file
# fmt: off
"""Release signatures authenticate all payloads before any downloaded data is trusted."""

import json
from contextlib import chdir
# Subprocesses use explicit argv and never a shell.
import subprocess  # nosec B404
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import release_signing as signing
import release_control
import version_plan
import version_receipt


class ReleaseSignatureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.sha = "a" * 40
        self.policy = {"repository": "example/project", "default_branch": "main",
                       "mode": "release", "sign_releases": True, "version_file": "version",
                       "versioning": {"schema": 1, "promotion": "promote-bytes",
                                      "files": [{"path": "version", "format": "text", "value": "package"}]}}
        (self.root / "version").write_text("1.2.3\n")
        (self.root / ".release-policy.json").write_text(json.dumps(self.policy))
        self.plan = version_plan.create_plan("1.2.3", "rc", 1, self.sha, self.policy, 1)
        (self.root / ".release-plan.json").write_text(json.dumps(self.plan))
        changed = version_plan.sync_versions(self.root, self.policy, self.plan)
        inputs = {"source_sha": self.sha, "plan_sha256": version_plan.plan_digest(self.plan),
                  "effective_inputs_sha256": version_plan.effective_inputs_digest(changed), "files": changed}
        (self.root / ".release-inputs.json").write_text(json.dumps(inputs))
        (self.assets / "package.tar.gz").write_bytes(b"package contents")
        with patch.object(version_receipt, "capture_toolchain", return_value={"python": "3.12"}):
            version_receipt.create_receipt(self.root / ".release-plan.json", self.root / ".release-inputs.json",
                                          self.assets, self.assets / "release-inputs-package.json")

    def prepare(self):
        def archive(_args, **kwargs):
            kwargs["stdout"].write(b"reviewed source archive")
        with patch.object(signing.subprocess, "check_output", return_value=self.sha), \
             patch.object(signing.subprocess, "run", side_effect=archive):
            signing.prepare(self.assets, self.root, self.sha)
        (self.assets / signing.BUNDLE).write_text("signature bundle")

    def verify(self):
        with patch.object(signing.subprocess, "run") as process:
            signing.verify(self.assets, self.policy, self.sha)
            return process.call_args.args[0]

    def test_publisher_enforces_authentication_before_any_api_write(self):
        class NoWrites:
            repo = "example/project"
            def api(self, *_args, **_kwargs):
                raise AssertionError("Publication must not write before authentication")
        with chdir(self.root), patch.object(release_control, "RELEASE_SIGNATURES_REQUIRED", True):
            with patch.object(signing, "verify", side_effect=ValueError("invalid signature")) as verify:
                with self.assertRaisesRegex(ValueError, "invalid signature"):
                    release_control._publish_prepared(
                        NoWrites(), "v1.2.3", self.sha, self.assets, False, "notes")
                verify.assert_called_once_with(self.assets, self.policy, self.sha)

    def test_required_signature_cannot_be_disabled_by_missing_or_changed_policy(self):
        with chdir(self.root), patch.object(release_control, "RELEASE_SIGNATURES_REQUIRED", True):
            with patch.object(signing, "verify") as verify:
                for update in ({"repository": "different/repo"}, {"sign_releases": False}):
                    (self.root / ".release-policy.json").write_text(json.dumps({**self.policy, **update}))
                    with self.assertRaises(release_control.ReleaseError):
                        release_control.verify_required_signatures(self.assets, "example/project", self.sha)
                (self.root / ".release-policy.json").unlink()
                with self.assertRaises(ValueError):
                    release_control.verify_required_signatures(self.assets, "example/project", self.sha)
                verify.assert_not_called()

    def test_complete_inventory_authenticates_source_and_build_receipt(self):
        self.prepare()
        command = self.verify()
        self.assertIn("https://github.com/example/project/.github/workflows/release-pipeline.yml@refs/heads/main", command)
        self.assertIn("--certificate-github-workflow-sha", command)
        self.assertIn(self.sha, command)
        self.assertIn("--certificate-github-workflow-repository", command)
        self.assertIn("--certificate-oidc-issuer", command)

    def test_signature_failure_precedes_manifest_parsing(self):
        self.prepare()
        (self.assets / signing.MANIFEST).write_text("not json")
        with patch.object(signing.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "cosign")), \
             patch.object(signing, "verify_inventory") as inventory:
            with self.assertRaises(subprocess.CalledProcessError):
                signing.verify(self.assets, self.policy, self.sha)
        inventory.assert_not_called()

    def test_tampered_missing_and_additional_assets_fail(self):
        self.prepare()
        target = self.assets / "package.tar.gz"
        target.write_bytes(b"modified package")
        with self.assertRaises(ValueError):
            self.verify()
        target.unlink()
        with self.assertRaises(ValueError):
            self.verify()
        target.write_bytes(b"package contents")
        (self.assets / "extra.bin").write_bytes(b"not signed")
        with self.assertRaises(ValueError):
            self.verify()

    def test_source_archive_is_required(self):
        self.prepare()
        (self.assets / f"source-{self.sha}.tar.gz").unlink()
        with self.assertRaisesRegex(ValueError, "source archive"):
            self.verify()

    def test_only_known_post_signing_metadata_may_be_added(self):
        self.prepare()
        for name in (signing.RECEIPT, "release-manifest.json"):
            (self.assets / name).write_text("publisher metadata, not a trust root")
        self.verify()

    def test_signed_identity_must_equal_independent_expectation(self):
        self.prepare()
        path = self.assets / signing.MANIFEST
        original = json.loads(path.read_text())
        for field, value in (("repository", "attacker/project"), ("ref", "refs/heads/other"),
                             ("source_sha", "b" * 40), ("schema", 2), ("artifacts", [])):
            path.write_text(json.dumps({**original, field: value}))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.verify()

    def test_symlinks_directories_and_case_collisions_fail(self):
        self.prepare()
        path = self.assets / "alias"
        path.symlink_to(self.assets / "package.tar.gz")
        with self.assertRaises(ValueError):
            self.verify()
        path.unlink()
        path.mkdir()
        with self.assertRaises(ValueError):
            self.verify()
        path.rmdir()
        (self.assets / "PACKAGE.tar.gz").write_text("case collision")
        with self.assertRaises(ValueError):
            self.verify()

    def test_control_document_and_manifest_limits(self):
        for value in ("[]", " " * (signing.MAX_MANIFEST_BYTES + 1)):
            path = self.root / "bad.json"
            path.write_text(value)
            with self.assertRaises(ValueError):
                signing.read_json(path)
        self.prepare()
        (self.assets / signing.MANIFEST).write_text(" " * (signing.MAX_MANIFEST_BYTES + 1))
        with patch.object(signing.subprocess, "run") as process, self.assertRaises(ValueError):
            signing.verify(self.assets, self.policy, self.sha)
        process.assert_not_called()

    def test_invalid_trusted_identity_is_rejected(self):
        for policy, sha in (({"repository": "../bad"}, self.sha),
                            ({**self.policy, "default_branch": "main'"}, self.sha),
                            (self.policy, "unreviewed")):
            with self.subTest(policy=policy, sha=sha), self.assertRaises(ValueError):
                signing.trust(policy, sha)

    def test_prepare_rejects_modified_build_and_wrong_checkout(self):
        with patch.object(signing.subprocess, "check_output", return_value="b" * 40), \
             self.assertRaisesRegex(ValueError, "Checkout"):
            signing.prepare(self.assets, self.root, self.sha)
        (self.assets / "package.tar.gz").write_text("tampered before signing")
        with self.assertRaises(ValueError):
            signing.prepare(self.assets, self.root, self.sha)

    def test_repeated_prepare_does_not_overwrite_existing_signature(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "original"):
            signing.prepare(self.assets, self.root, self.sha)


if __name__ == "__main__":
    unittest.main()
