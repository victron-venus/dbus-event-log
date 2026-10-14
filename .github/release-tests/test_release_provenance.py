# Vendored release toolkit; change the toolkit source, then render again.
# ruff: noqa
# mypy: ignore-errors
# pylint: skip-file
# fmt: off
"""Detached provenance retains complete raw evidence and fails closed on mismatches."""

import base64
import copy
import hashlib
import importlib.util
import json
import subprocess  # nosec B404
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import release_provenance as provenance  # noqa: E402 - use this checkout
import version_plan  # noqa: E402 - use this checkout

SPEC = importlib.util.spec_from_file_location("oci_fixture", Path(__file__).with_name("test_merge_oci_archives.py"))
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


class DetachedProvenanceTests(unittest.TestCase):
    def setUp(self):
        # Synthetic contracts test their own pair; generated policy enforcement
        # is exercised explicitly below and in the isolated generator suite.
        requirements = patch.object(provenance, "REQUIRED_PAIRS", ())
        requirements.start()
        self.addCleanup(requirements.stop)
        self.helper = fixture.MergeTest()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.root = self.helper.root
        self.output = self.root / "container.oci.tar"
        self.sidecar = self.root / "container.provenance.json"
        self.pair = {"image": self.output.name, "sidecar": self.sidecar.name,
                     "platforms": ["linux/amd64", "linux/arm64"]}
        self.policy = {"repository": "example/project", "mode": "release", "sign_releases": True,
                       "detached_provenance": [self.pair], "versioning": {"schema": 1,
                       "promotion": "final-build", "files": [{"path": "version", "format": "text", "value": "package"}],
                       "artifacts": [{"path": self.output.name, "format": "oci", "field": ["config", "Labels", "org.opencontainers.image.version"], "value": "package"}]}}
        self.plan = version_plan.create_plan("0.1.6", "beta", 1, fixture.REVISION, self.policy, 1)
        self.inputs = self.helper.inputs(self.helper.fixture("amd64", attestation="artifact"),
                                         self.helper.fixture("arm64", attestation="legacy"))

    def detach(self):
        return provenance.detach(self.inputs, self.output, self.sidecar, self.policy, self.plan)

    def verify(self):
        return provenance.verify_all(self.root, self.policy, self.plan)

    def alter_sidecar(self, transform):
        data = json.loads(self.sidecar.read_text())
        transform(data)
        self.sidecar.write_text(json.dumps(data))

    def test_preserves_raw_reachable_metadata_and_original_roots(self):
        self.detach()
        self.verify()
        data = json.loads(self.sidecar.read_text())
        with ExitStack() as stack:
            for platform, path in self.inputs.items():
                original = provenance.oci.Archive(path, platform, fixture.VERSION, fixture.REVISION, stack)
                expected = next(item for item in data["inputs"] if item["platform"] == platform)
                self.assertEqual(base64.b64decode(expected["index"]), provenance.read_raw(original, "index.json"))
                for name in original.metadata_names:
                    if name.startswith(provenance.oci.BLOB_PREFIX):
                        self.assertEqual(base64.b64decode(data["blobs"][name.rsplit("/", 1)[1]]), provenance.read_raw(original, name))

    def test_missing_sidecar_fails(self):
        self.detach()
        self.sidecar.unlink()
        with self.assertRaises(OSError):
            self.verify()

    def test_large_attestations_survive_detached_evidence_replay(self):
        # Exercise the exact bound and a measured native-CI SBOM size.
        sizes = (provenance.oci.MAX_ATTESTATION_JSON, 3_848_360)
        entries = [self.helper.fixture(arch, attestation=kind, attestation_size=size)
                   for (arch, kind), size in zip((("amd64", "artifact"), ("arm64", "legacy")), sizes)]
        self.inputs = self.helper.inputs(*entries)
        self.detach()
        self.verify()
        data = json.loads(self.sidecar.read_text())
        for original, size in zip(entries, sizes):
            name, raw = next((name, raw) for name, raw in original.items() if len(raw) == size)
            self.assertEqual(base64.b64decode(data["blobs"][name.rsplit("/", 1)[1]]), raw)

    def test_detached_structural_blob_keeps_smaller_limit(self):
        self.detach()
        data = json.loads(self.sidecar.read_text())
        root = json.loads(base64.b64decode(data["inputs"][0]["index"]))
        descriptor = root["manifests"][0]
        old_digest = descriptor["digest"][7:]
        raw = base64.b64decode(data["blobs"].pop(old_digest))
        raw = raw.ljust(provenance.oci.MAX_JSON + 1, b" ")
        digest = hashlib.sha256(raw).hexdigest()
        data["blobs"][digest] = base64.b64encode(raw).decode()
        descriptor.update(digest="sha256:" + digest, size=len(raw))
        data["inputs"][0]["index"] = base64.b64encode(provenance.oci.canonical(root)).decode()
        self.sidecar.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "oversized OCI metadata"):
            self.verify()

    def test_detached_attestation_decode_size_and_total_boundaries(self):
        limit = provenance.oci.MAX_ATTESTATION_JSON
        raw = b" " * limit
        encoded = base64.b64encode(raw).decode()
        self.assertEqual(provenance.decode(encoded, [0], max_bytes=limit), raw)
        with self.assertRaisesRegex(ValueError, "oversized provenance blob"):
            provenance.decode(base64.b64encode(raw + b" ").decode(), [0], max_bytes=limit)
        encoded = base64.b64encode(b"{}").decode()
        budget = [provenance.oci.MAX_JSON_TOTAL - 2]
        self.assertEqual(provenance.decode(encoded, budget, max_bytes=limit), b"{}")
        self.assertEqual(budget[0], provenance.oci.MAX_JSON_TOTAL)
        with self.assertRaisesRegex(ValueError, "Too much decoded provenance metadata"):
            provenance.decode(encoded, [provenance.oci.MAX_JSON_TOTAL - 1], max_bytes=limit)

    def test_sidecar_missing_or_wrong_source_plan_and_image_fail(self):
        self.detach()
        original = self.sidecar.read_bytes()
        edits = [lambda d: d.update(source_sha="a" * 40),
                 lambda d: d.update(plan_sha256="a" * 64),
                 lambda d: d["image"].update(sha256="a" * 64),
                 lambda d: d["image"].update(index_sha256="a" * 64),
                 lambda d: d["image"].update(name="different.oci.tar"),
                 lambda d: d["image"].update(size=1),
                 lambda d: d["inputs"].pop(),
                 lambda d: d["inputs"][0].update(platform="linux/arm64"),
                 lambda d: d.update(extra="unexpected")]
        for edit in edits:
            with self.subTest(edit=edit):
                self.sidecar.write_bytes(original)
                self.alter_sidecar(edit)
                with self.assertRaises(ValueError):
                    self.verify()
        self.sidecar.write_bytes(original)
        self.verify()

    def test_omitted_dangling_changed_and_extra_metadata_fail(self):
        self.detach()
        original = self.sidecar.read_bytes()
        data = json.loads(original)
        statement_key = next(key for key, raw in data["blobs"].items()
                             if '"predicateType"' in base64.b64decode(raw).decode())
        edits = [lambda d: d["blobs"].pop(statement_key),
                 lambda d: d["blobs"].update({statement_key: base64.b64encode(b"{}").decode()}),
                 lambda d: d["blobs"].update({hashlib.sha256(b'{"extra":true}').hexdigest(): base64.b64encode(b'{"extra":true}').decode()})]
        for edit in edits:
            self.sidecar.write_bytes(original)
            self.alter_sidecar(edit)
            with self.assertRaises(ValueError):
                self.verify()

    def test_policy_cannot_remove_or_change_embedded_requirement(self):
        with patch.object(provenance, "REQUIRED_PAIRS", (copy.deepcopy(self.pair),)):
            for replacement in ([], [{**self.pair, "sidecar": "other.provenance.json"}]):
                policy = {**self.policy, "detached_provenance": replacement}
                with self.assertRaisesRegex(ValueError, "generated requirements"):
                    provenance.policy_pairs(policy)
            policy = dict(self.policy)
            policy.pop("detached_provenance")
            with self.assertRaises(ValueError):
                provenance.policy_pairs(policy)

    def test_policy_requires_signed_exact_unique_pair_and_both_platforms(self):
        for replacement in ([{**self.pair, "image": "../container.oci.tar"}],
                            [{**self.pair, "sidecar": "container.oci.tar"}],
                            [{**self.pair, "platforms": ["linux/amd64"]}],
                            [{**self.pair, "extra": True}], [self.pair, self.pair]):
            with self.assertRaises(ValueError):
                provenance.policy_pairs({**self.policy, "detached_provenance": replacement})
        with self.assertRaises(ValueError):
            provenance.policy_pairs({**self.policy, "sign_releases": False})

    def test_default_policy_has_no_new_requirement(self):
        self.assertEqual(provenance.policy_pairs({}), ())
        self.assertEqual(provenance.verify_all(self.root, {}, {}), [])

    def test_refuses_unsigned_original_platform(self):
        self.inputs["linux/arm64"] = self.helper.archive("unsigned.tar", self.helper.fixture("arm64"))
        with self.assertRaisesRegex(ValueError, "missing BuildKit provenance"):
            self.detach()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.sidecar.exists())

    def test_atomic_pair_rolls_back_if_second_link_fails(self):
        actual = provenance.os.link
        def link(source, destination):
            if destination == self.sidecar:
                raise FileExistsError("another writer")
            return actual(source, destination)
        with patch.object(provenance.os, "link", side_effect=link):
            with self.assertRaises(FileExistsError):
                self.detach()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.sidecar.exists())

    def test_refuses_sidecar_alias_and_duplicate_json_keys(self):
        self.detach()
        original = self.sidecar.read_bytes()
        self.sidecar.write_bytes(original.replace(b'"schema":1', b'"schema":1,"schema":1'))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.verify()
        self.sidecar.unlink()
        target = self.root / "elsewhere"
        target.write_bytes(original)
        self.sidecar.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.verify()

    def test_base64_and_total_budget_are_bounded(self):
        for raw in ("!", "AAAA====", "A" * (4 * ((provenance.oci.MAX_JSON + 2) // 3) + 4)):
            with self.assertRaises(ValueError):
                provenance.decode(raw, [0])
        with self.assertRaises(ValueError):
            provenance.decode(base64.b64encode(b"test").decode(), [provenance.oci.MAX_JSON_TOTAL])

    def test_another_builds_provenance_cannot_be_bound_to_existing_runtime(self):
        self.detach()
        first_runtime = self.output.read_bytes()
        first_identity = json.loads(self.sidecar.read_text())["image"]
        self.output.unlink()
        self.sidecar.unlink()
        changed = b"different filesystem bytes"
        self.inputs = {"linux/" + arch: self.helper.archive("different-" + arch + ".tar",
                       self.helper.fixture(arch, attestation="artifact", layer_content=changed,
                                           diff_id="sha256:" + hashlib.sha256(changed).hexdigest()))
                       for arch in ("amd64", "arm64")}
        self.detach()
        self.output.write_bytes(first_runtime)
        self.alter_sidecar(lambda data: data.update(image=first_identity))
        with self.assertRaises(ValueError):
            self.verify()

    def test_hardlinked_sidecar_is_rejected(self):
        self.detach()
        provenance.os.link(self.sidecar, self.root / "alias")
        with self.assertRaisesRegex(ValueError, "unaliased"):
            self.verify()

    def signed_inventory(self):
        import release_signing
        self.detach()
        for original in self.inputs.values():
            original.unlink()
        (self.root / f"source-{fixture.REVISION}.tar.gz").write_bytes(b"reviewed source fixture")
        files = [self.output, self.sidecar, self.root / f"source-{fixture.REVISION}.tar.gz"]
        manifest = {"schema": 1, "repository": self.policy["repository"], "ref": "refs/heads/main",
                    "source_sha": fixture.REVISION, "plan": self.plan,
                    "artifacts": [release_signing.digest(path) for path in sorted(files)]}
        (self.root / release_signing.MANIFEST).write_text(json.dumps(manifest))
        (self.root / release_signing.BUNDLE).write_text("test signature bundle")
        return release_signing, manifest

    def test_signed_plan_is_validated_against_policy_and_caller_source(self):
        signing, manifest = self.signed_inventory()
        path = self.root / signing.MANIFEST
        with patch.object(signing.subprocess, "run"):
            signing.verify(self.root, self.policy, fixture.REVISION)
            for field, value in (("source_sha", "a" * 40), ("policy_sha256", "a" * 64),
                                 ("version", "99.0.0-beta.1")):
                changed = copy.deepcopy(manifest)
                changed["plan"][field] = value
                path.write_text(json.dumps(changed))
                with self.subTest(field=field), self.assertRaises(ValueError):
                    signing.verify(self.root, self.policy, fixture.REVISION)

    def test_semantics_reject_replacement_after_inventory_hashing(self):
        signing, _ = self.signed_inventory()
        verify_inventory = signing.verify_inventory
        def race(*args):
            verify_inventory(*args)
            # A whitespace-only replacement is semantically equivalent but is
            # still not the exact authenticated payload from the manifest.
            self.sidecar.write_bytes(self.sidecar.read_bytes() + b" ")
        with patch.object(signing.subprocess, "run"), patch.object(signing, "verify_inventory", side_effect=race):
            with self.assertRaisesRegex(ValueError, "authenticated inventory"):
                signing.verify(self.root, self.policy, fixture.REVISION)

    def test_signature_authenticates_private_snapshot_not_mutable_path(self):
        signing, _ = self.signed_inventory()
        seen = []
        def cosign(command, **_kwargs):
            authenticated_path = Path(command[-1])
            seen.append(authenticated_path)
            self.assertNotEqual(authenticated_path, self.root / signing.MANIFEST)
            self.assertTrue(authenticated_path.is_file())
            # Changing the original after authentication must not affect the
            # exact snapshot parsed and verified by this invocation.
            (self.root / signing.MANIFEST).write_text("not json")
        with patch.object(signing.subprocess, "run", side_effect=cosign):
            signing.verify(self.root, self.policy, fixture.REVISION)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0].exists())

    def test_snapshot_rejects_fifo_without_waiting(self):
        fifo = self.root / "control-fifo"
        provenance.os.mkfifo(fifo)
        code = "from pathlib import Path; from release_provenance import read_snapshot; read_snapshot(Path(__import__('sys').argv[1]), 1024)"
        # Fixed interpreter/source and a temporary pathname; no shell execution.
        result = subprocess.run(  # nosec B603
            [sys.executable, "-c", code, str(fifo)], cwd=ROOT / "scripts",
            capture_output=True, text=True, timeout=5, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("regular unaliased", result.stderr)

    def test_signature_control_swap_rejected_before_cosign(self):
        signing, _ = self.signed_inventory()
        original_files = signing.files
        manifest = self.root / signing.MANIFEST
        alias = self.root / "outside"
        alias.write_bytes(manifest.read_bytes())
        def race(directory):
            entries = original_files(directory)
            manifest.unlink()
            manifest.symlink_to(alias)
            return entries
        with patch.object(signing, "files", side_effect=race), patch.object(signing.subprocess, "run") as run:
            with self.assertRaises((ValueError, OSError)):
                signing.verify(self.root, self.policy, fixture.REVISION)
            run.assert_not_called()

    def test_snapshot_rejects_hardlinks_and_oversized_data(self):
        control = self.root / "control"
        control.write_bytes(b"control")
        with self.assertRaisesRegex(ValueError, "Oversized"):
            provenance.read_snapshot(control, 3)
        provenance.os.link(control, self.root / "alias")
        with self.assertRaisesRegex(ValueError, "unaliased"):
            provenance.read_snapshot(control, 1024)

    def test_staging_unrelated_target_allowed_but_aggregate_requires_pair(self):
        import stage_release_assets as stage
        target = self.root / "signature"
        target.mkdir()
        source = self.root / "release-signatures.json"
        source.write_text("signature fixture")
        def receipt(*_args, **_kwargs):
            (target / "release-inputs-signature.json").write_text("{}")
        with patch.object(stage, "inspect_staged_artifacts", return_value=[]), patch.object(stage.subprocess, "run", side_effect=receipt):
            stage.write_staged_files(self.root, target, {source.name: source}, self.policy, self.plan,
                                     self.root / ".release-plan.json")
        self.assertTrue((target / source.name).is_file())
        with self.assertRaises(FileNotFoundError):
            provenance.verify_all(target, self.policy, self.plan)

    def test_staging_requires_complete_owned_pair(self):
        self.detach()
        self.assertEqual(len(provenance.verify_staged(self.root, self.policy, self.plan)), 1)
        self.sidecar.unlink()
        with self.assertRaisesRegex(ValueError, "partial provenance pair"):
            provenance.verify_staged(self.root, self.policy, self.plan)
        self.output.unlink()
        self.sidecar.write_text("orphan")
        with self.assertRaisesRegex(ValueError, "partial provenance pair"):
            provenance.verify_staged(self.root, self.policy, self.plan)

    @staticmethod
    def change_invocation(entries, invocation, subject=None):
        result = {"oci-layout": entries["oci-layout"]}
        def descriptor(item):
            value = dict(item)
            raw = entries["blobs/sha256/" + value["digest"][7:]]
            if value["mediaType"] in (provenance.oci.INDEX, provenance.oci.MANIFEST, provenance.oci.IN_TOTO):
                data = json.loads(raw)
                for key in ("manifests", "layers"):
                    if key in data:
                        data[key] = [descriptor(child) for child in data[key]]
                if "config" in data:
                    data["config"] = descriptor(data["config"])
                if "predicateType" in data:
                    data["predicate"] = {"runDetails": {"metadata": {"invocationId": invocation}}}
                    if subject is not None:
                        data["subject"] = [{"name": "_", "digest": {"sha256": subject}}]
                raw = provenance.oci.canonical(data)
            value.update(digest="sha256:" + hashlib.sha256(raw).hexdigest(), size=len(raw))
            result["blobs/sha256/" + value["digest"][7:]] = raw
            return value
        root = json.loads(entries["index.json"])
        root["manifests"] = [descriptor(item) for item in root["manifests"]]
        result["index.json"] = provenance.oci.canonical(root)
        return result

    def test_mismatched_slsa_subject_is_rejected_before_output(self):
        entries = self.change_invocation(self.helper.fixture("amd64", attestation="artifact"),
                                         "wrong-subject", subject="0" * 64)
        self.inputs["linux/amd64"] = self.helper.archive("wrong-subject.tar", entries)
        with self.assertRaisesRegex(ValueError, "statement subject"):
            self.detach()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.sidecar.exists())

    def test_swapped_original_platform_roots_are_rejected(self):
        self.detach()
        def swap(data):
            data["inputs"][0]["index"], data["inputs"][1]["index"] = data["inputs"][1]["index"], data["inputs"][0]["index"]
        self.alter_sidecar(swap)
        with self.assertRaises(ValueError):
            self.verify()

    def test_hardlinked_runtime_is_rejected(self):
        self.detach()
        provenance.os.link(self.output, self.root / "runtime-alias")
        with self.assertRaisesRegex(ValueError, "unaliased"):
            self.verify()

    def test_different_truthful_evidence_gives_identical_full_runnable_archive(self):
        self.detach()
        first_archive, first_evidence = self.output.read_bytes(), self.sidecar.read_bytes()
        self.output.unlink()
        self.sidecar.unlink()
        self.inputs = {"linux/" + architecture: self.helper.archive("changed-" + architecture + ".tar",
                       self.change_invocation(self.helper.fixture(architecture, attestation="artifact"), "another-run"))
                       for architecture in ("amd64", "arm64")}
        self.detach()
        self.verify()
        self.assertEqual(self.output.read_bytes(), first_archive)
        self.assertNotEqual(self.sidecar.read_bytes(), first_evidence)


if __name__ == "__main__":
    unittest.main()
