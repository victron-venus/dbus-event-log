"""Exercise the native build adapter without Docker or a live D-Bus service."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVISION = "a" * 40
CONFIG_DIGEST = "sha256:" + "b" * 64


class NativeContainerAdapterTests(unittest.TestCase):
    """Require one native build, successful smoke tests, and bound evidence."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        (self.root / "bin").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "tests/smoke_monitor.py").write_text("# isolated smoke fixture\n")
        for name in ("build-native-container.sh", "package-container.sh"):
            shutil.copyfile(ROOT / "scripts" / name, scripts / name)
        (scripts / "release_container_labels.py").write_text(f"print('0.1.6-beta.1 {REVISION}')\n")
        (scripts / "version_plan.py").write_text(
            "def validate_plan(plan):\n    return plan\n"
            "def plan_digest(plan):\n    return 'f' * 64\n"
            "def projections(plan, ecosystem):\n    return {'package': '0.1.6b1'}\n"
        )
        (scripts / "version_receipt.py").write_text(
            "import pathlib, sys\n"
            "output = pathlib.Path(sys.argv[sys.argv.index('--output') + 1])\n"
            "output.write_text('{}')\n"
        )
        (self.root / ".release-plan.json").write_text(json.dumps({"source_sha": REVISION}))
        (self.root / ".release-inputs.json").write_text("{}")
        self.environment = dict(
            os.environ,
            PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"],
            GITHUB_RUN_ID="12345",
            GITHUB_RUN_ATTEMPT="1",
            RUNNER_ARCH="X64",
            NATIVE_TEST_ROOT=str(self.root),
        )
        self._executable(
            "uname",
            "import os, sys\n"
            "print('Linux' if sys.argv[1] == '-s' else os.getenv('TEST_MACHINE', 'x86_64'))\n",
        )
        self._executable(
            "docker",
            "import json, os, pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "root = pathlib.Path(os.environ['NATIVE_TEST_ROOT'])\n"
            "with (root / 'docker-calls.jsonl').open('a') as stream:\n"
            "    stream.write(json.dumps(args) + '\\n')\n"
            "if args[:2] == ['buildx', 'build']:\n"
            "    for arg in args:\n"
            "        if arg.startswith('type=oci,dest='):\n"
            "            dest = arg.split('dest=', 1)[1].split(',', 1)[0]\n"
            "            pathlib.Path(dest).write_bytes(b'oci-fixture')\n"
            "elif args[:2] == ['image', 'inspect']:\n"
            "    image = {'Os': 'linux', 'Architecture': os.getenv('TEST_IMAGE_ARCH', 'amd64'),\n"
            f"      'Id': '{CONFIG_DIGEST}', 'Config': {{\n"
            "      'User': os.getenv('TEST_USER', 'appuser'),\n"
            "      'Labels': {'org.opencontainers.image.version':\n"
            "      os.getenv('TEST_VERSION', '0.1.6-beta.1'),\n"
            f"      'org.opencontainers.image.revision': '{REVISION}'}}}}}}\n"
            "    print(json.dumps([image]))\n"
            "elif args[0] == 'run':\n"
            "    failure = os.getenv('TEST_SMOKE_FAILURE')\n"
            "    session_failure = failure == 'session-bus' and 'dbus-run-session' in args\n"
            "    if failure == 'true' or session_failure:\n"
            "        sys.exit(1)\n"
            "else:\n"
            "    print('Docker Buildx test-version')\n",
        )

    def _executable(self, name: str, source: str) -> None:
        path = self.root / "bin" / name
        path.write_text(f"#!{sys.executable}\n{source}")
        path.chmod(0o755)

    def _run(self, platform: str = "linux/amd64") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "scripts/build-native-container.sh",
                platform,
                "native-container-" + platform.split("/")[-1],
            ],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _calls(self) -> list[list[str]]:
        path = self.root / "docker-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_one_build_exports_oci_and_loads_same_result_for_offline_smoke(self) -> None:
        """The release bytes and native smoke image originate in one build result."""
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self._calls()
        builds = [call for call in calls if call[:2] == ["buildx", "build"]]
        self.assertEqual(len(builds), 1)
        self.assertIn(
            "type=oci,dest=native-container-amd64/container-amd64.oci.tar,attestation-inline=true",
            builds[0],
        )
        self.assertIn("type=docker", builds[0])
        self.assertNotIn("--push", builds[0])
        self.assertFalse(any(arg.startswith("--provenance") for arg in builds[0]))
        self.assertFalse(any("provenance=false" in arg or "cache-" in arg for arg in builds[0]))
        smoke = [call for call in calls if call[0] == "run"]
        self.assertEqual(len(smoke), 2)
        self.assertEqual(smoke[0][-1], "0.1.6b1")
        for call in smoke:
            self.assertEqual(call[call.index("--network") + 1], "none")
            self.assertIn(CONFIG_DIGEST, call)
            self.assertNotIn("--user", call)
        output = self.root / "native-container-amd64"
        evidence = json.loads((output / "native-build.json").read_text())
        self.assertEqual(evidence["image_config_digest"], CONFIG_DIGEST)
        self.assertEqual(evidence["source_sha"], REVISION)
        self.assertEqual(evidence["version"], "0.1.6-beta.1")
        self.assertEqual(evidence["run_id"], "12345")
        self.assertEqual(evidence["run_attempt"], "1")
        self.assertTrue(evidence["smoke_passed"])
        self.assertEqual(
            {path.name for path in output.iterdir()},
            {"container-amd64.oci.tar", "native-build.json", "release-inputs-native-amd64.json"},
        )

    def test_arm64_runner_produces_arm64_evidence(self) -> None:
        """ARM64 uses the same adapter with its native host and image architecture."""
        self.environment.update(
            TEST_MACHINE="aarch64", TEST_IMAGE_ARCH="arm64", RUNNER_ARCH="ARM64"
        )
        result = self._run("linux/arm64")
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads((self.root / "native-container-arm64/native-build.json").read_text())
        self.assertEqual((evidence["platform"], evidence["machine"]), ("linux/arm64", "aarch64"))
        self.assertEqual(evidence["runner_arch"], "ARM64")

    def test_wrong_native_architecture_stops_before_docker(self) -> None:
        """A mismatched host may not silently fall back to emulation."""
        result = self._run("linux/arm64")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._calls(), [])

    def test_failed_smoke_creates_no_success_evidence_or_receipt(self) -> None:
        """A built image cannot be attested when its smoke check fails."""
        self.environment["TEST_SMOKE_FAILURE"] = "true"
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "native-container-amd64/native-build.json").exists())
        self.assertFalse(
            (self.root / "native-container-amd64/release-inputs-native-amd64.json").exists()
        )

    def test_wrong_image_identity_stops_before_smoke(self) -> None:
        """Incorrect version, platform, or root user invalidates the loaded image."""
        for variable, value in (
            ("TEST_VERSION", "0.0.0"),
            ("TEST_IMAGE_ARCH", "arm64"),
            ("TEST_USER", "0:0"),
        ):
            with self.subTest(variable=variable):
                self.environment[variable] = value
                result = self._run()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call[0] == "run" for call in self._calls()))
                shutil.rmtree(self.root / "native-container-amd64")
                (self.root / "docker-calls.jsonl").unlink()
                self.environment.pop(variable)

    def test_failed_session_bus_smoke_cannot_attest_a_successful_identity_probe(self) -> None:
        """Passing the first runtime probe cannot hide failure of the actual monitor."""
        self.environment["TEST_SMOKE_FAILURE"] = "session-bus"
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        smoke = [call for call in self._calls() if call[0] == "run"]
        self.assertEqual(len(smoke), 2)
        self.assertEqual(smoke[0][smoke[0].index("--entrypoint") + 1], "python")
        self.assertEqual(smoke[1][smoke[1].index("--entrypoint") + 1], "dbus-run-session")
        output = self.root / "native-container-amd64"
        self.assertFalse((output / "native-build.json").exists())
        self.assertFalse((output / "release-inputs-native-amd64.json").exists())

    def test_reused_output_is_rejected_before_docker(self) -> None:
        """Existing outputs cannot be mixed with another plan or attempt."""
        (self.root / "native-container-amd64").mkdir()
        self.assertNotEqual(self._run().returncode, 0)
        self.assertEqual(self._calls(), [])

    def test_missing_actions_identity_is_rejected(self) -> None:
        """Intermediate receipts require a definite workflow run and attempt."""
        self.environment.pop("GITHUB_RUN_ID")
        self.assertNotEqual(self._run().returncode, 0)
        self.assertEqual(self._calls(), [])

    def test_assembly_appends_checksums_for_archive_and_evidence(self) -> None:
        """Both assembled outputs are covered before final release staging."""
        output = self.root / "release-dist"
        output.mkdir()
        (output / "SHA256SUMS").write_text("existing-wheel-checksum\n")
        (self.root / "scripts/assemble_native_container.py").write_text(
            "import pathlib, sys\n"
            "for option in ('--output', '--evidence'):\n"
            "    pathlib.Path(sys.argv[sys.argv.index(option) + 1]).write_bytes(b'checked')\n"
        )
        result = subprocess.run(
            ["bash", "scripts/package-container.sh"],
            cwd=self.root,
            env=self.environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        digest = hashlib.sha256(b"checked").hexdigest()
        self.assertEqual(
            (output / "SHA256SUMS").read_text(),
            "existing-wheel-checksum\n"
            f"{digest}  dbus-event-log-container.oci.tar\n"
            f"{digest}  container-build-evidence.json\n",
        )
        self.assertEqual(self._calls(), [])
