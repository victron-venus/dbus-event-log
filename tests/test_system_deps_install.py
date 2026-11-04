"""Exercise bounded, authenticated APT fetching without a network or root access."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import call, patch

SPEC = importlib.util.spec_from_file_location(
    "system_deps", Path(__file__).resolve().parents[1] / "docker/install-system-deps.py"
)
assert SPEC and SPEC.loader
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)
TEMPORARY = (
    "E: Failed to fetch https://snapshot.debian.org/example.deb "
    "TooManyRequests 503  No healthy backends [IP: 192.0.2.1 443]\n"
    "E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?\n"
)


class TestFetch(unittest.TestCase):
    """Verify retries, fatal diagnostics, installation order and shared budgets."""

    def setUp(self) -> None:
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.invoke = self.enterContext(patch.object(INSTALLER, "run"))
        self.clock = self.enterContext(patch.object(INSTALLER.time, "monotonic", return_value=100))
        self.sleep = self.enterContext(patch.object(INSTALLER.time, "sleep"))

    def test_success_keeps_checks_enabled_and_installs_offline(self) -> None:
        self.invoke.return_value = (0, "")
        INSTALLER.install(["dbus", "libdbus-1-3"])
        commands = [item.args[0] for item in self.invoke.call_args_list]
        self.assertEqual(commands[0], [INSTALLER.APT, "--version"])
        self.assertEqual(commands[1][-2:], ["update", "--error-on=any"])
        selection = ["install", "-y", "--no-install-recommends", "dbus", "libdbus-1-3"]
        cache_options = commands[2][len(INSTALLER.OPTIONS) + 1 : len(INSTALLER.OPTIONS) + 3]
        self.assertEqual(cache_options[0], "-o")
        self.assertTrue(cache_options[1].startswith("Dir::Cache::archives="))
        self.assertFalse(Path(cache_options[1].split("=", 1)[1]).exists())
        self.assertEqual(
            commands[2],
            [INSTALLER.APT, *INSTALLER.OPTIONS, *cache_options, "--download-only", *selection],
        )
        self.assertEqual(
            commands[3],
            [INSTALLER.APT, *INSTALLER.OPTIONS, *cache_options, "--no-download", *selection],
        )
        for command in commands[1:]:
            self.assertIn("Acquire::Retries=0", command)
            self.assertNotIn("--allow-unauthenticated", command)
            self.assertNotIn("--fix-missing", command)
        self.sleep.assert_not_called()

    def test_temporary_download_retries_same_operation_then_installs(self) -> None:
        self.invoke.side_effect = [(0, ""), (0, ""), (100, TEMPORARY), (0, ""), (0, "")]
        INSTALLER.install(["dbus"])
        self.assertEqual(self.invoke.call_args_list[2], self.invoke.call_args_list[3])
        self.sleep.assert_called_once_with(5)
        self.assertIn("--no-download", self.invoke.call_args_list[-1].args[0])

    def test_download_only_504_summary_retries_but_unknown_errors_still_fail(self) -> None:
        output = (
            "E: Failed to fetch https://snapshot.debian.org/example.deb "
            "504  Gateway Timeout [IP: 192.0.2.1 443]\n"
            "E: Some files failed to download\n"
        )
        self.invoke.side_effect = [(100, output), (0, "")]
        INSTALLER.fetch(["--download-only", "install", "dbus"], 820)
        self.assertEqual(self.invoke.call_count, 2)
        self.sleep.assert_called_once_with(5)
        self.assertFalse(INSTALLER.transient_failure(output + "E: Unknown failure\n"))
        self.assertFalse(INSTALLER.transient_failure("E: Some files failed to download\n"))

    def test_retry_limit_never_reaches_install(self) -> None:
        self.invoke.side_effect = [(0, ""), *[(100, TEMPORARY)] * 3]
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.install(["dbus"])
        self.assertEqual(self.invoke.call_count, 4)
        self.assertEqual(self.sleep.call_args_list, [call(5), call(10)])

    def test_snapshot_first_byte_timeout_retries_download(self) -> None:
        output = (
            "E: Failed to fetch https://snapshot.debian.org/example.deb "
            "TooManyRequests 503  first byte timeout [IP: 192.0.2.1 443]\n"
            "E: Some files failed to download\n"
        )
        self.invoke.side_effect = [(100, output), (0, "")]
        INSTALLER.fetch(["--download-only", "install", "dbus"], 820)
        self.assertEqual(self.invoke.call_count, 2)
        self.sleep.assert_called_once_with(5)

    def test_index_retry_uses_terminal_fetch_reason(self) -> None:
        output = (
            "Err:5 https://snapshot.debian.org/ trixie-updates/main arm64 Packages\n"
            "  TooManyRequests 503  No healthy backends [IP: 192.0.2.1 443]\n"
            "  404  NOT FOUND [IP: 192.0.2.1 443]\n"
            "  504  Gateway Timeout [IP: 192.0.2.1 443]\n"
            "E: Failed to fetch https://snapshot.debian.org/Packages "
            "504  Gateway Timeout [IP: 192.0.2.1 443]\n"
            "E: Some index files failed to download. "
            "They have been ignored, or old ones used instead.\n"
        )
        self.invoke.side_effect = [(100, output), (0, "")]
        INSTALLER.fetch(["update", "--error-on=any"], 820)
        self.assertEqual(self.invoke.call_count, 2)
        self.sleep.assert_called_once_with(5)
        for reason in ("401 Unauthorized", "403 Forbidden", "404 Not Found"):
            with self.subTest(reason=reason):
                self.assertFalse(
                    INSTALLER.transient_failure(
                        output
                        + ("E: Failed to fetch https://snapshot.debian.org/other " + reason + "\n")
                    )
                )
                self.assertFalse(
                    INSTALLER.transient_failure(
                        output.replace("Packages 504  Gateway Timeout", "Packages " + reason)
                    )
                )
        self.assertFalse(INSTALLER.transient_failure(output + "Hash Sum mismatch\n"))

    def test_progress_and_url_numbers_are_not_http_statuses(self) -> None:
        self.assertTrue(
            INSTALLER.transient_failure(
                "Get:1 https://snapshot.debian.org/ package [404 kB]\n"
                "E: Failed to fetch https://snapshot.debian.org/401/403/404/example.deb "
                "503 Service Unavailable\n"
            )
        )
        self.assertFalse(
            INSTALLER.transient_failure(
                "E: Failed to fetch https://snapshot.debian.org/503/Service_Unavailable "
                "unknown error\n"
            )
        )
        self.assertFalse(INSTALLER.transient_failure("E: Failed to fetch missing-reason\n"))

    def test_permanent_and_unknown_errors_do_not_retry_even_with_503(self) -> None:
        diagnostics = [
            "E: Hash Sum mismatch",
            "E: NO_PUBKEY 123456",
            "E: EXPKEYSIG 123456",
            "E: BADSIG 123456",
            "E: Repository is not signed",
            "E: invalid signature",
            "E: signatures couldn't be verified",
            "E: Release file is expired",
            "E: Repository does not have a Release file",
            "E: Unable to locate package x",
            "E: unmet dependencies",
            "E: held broken packages",
            "E: No space left on device",
            "E: unknown failure",
            "W: unexpected warning",
            "E: Failed to fetch https://example.invalid/a.deb 401 Unauthorized",
            "E: Failed to fetch https://example.invalid/a.deb 403 Forbidden",
            "E: Failed to fetch https://example.invalid/a.deb 404 Not Found",
            "E: Failed to fetch https://example.invalid/a.deb unidentified failure",
        ]
        for diagnostic in diagnostics:
            with self.subTest(diagnostic=diagnostic):
                self.invoke.reset_mock()
                self.invoke.return_value = (100, TEMPORARY + diagnostic + "\n")
                with self.assertRaises(INSTALLER.AptError):
                    INSTALLER.fetch(["update", "--error-on=any"], 820)
                self.assertEqual(self.invoke.call_count, 1)
                self.sleep.assert_not_called()

    def test_incidental_numbers_and_missing_error_diagnostics_do_not_retry(self) -> None:
        for output in (
            "package 503 unavailable",
            "503 Service Unavailable",
            "E: Failed to fetch https://example.invalid/package503.deb unknown",
        ):
            with self.subTest(output=output):
                self.invoke.reset_mock()
                self.invoke.return_value = (100, output)
                with self.assertRaises(INSTALLER.AptError):
                    INSTALLER.fetch(["update"], 820)
                self.assertEqual(self.invoke.call_count, 1)

    def test_supported_transport_errors_are_explicit(self) -> None:
        for reason in (
            "408 Request Timeout",
            "429 Too Many Requests",
            "500 Internal Server Error",
            "502 Bad Gateway",
            "503 Service Unavailable",
            "504 Gateway Timeout",
            "Temporary failure resolving 'example.invalid'",
            "Connection timed out",
            "Could not connect to example.invalid",
            "Connection reset by peer",
            "TLS connection was non-properly terminated",
        ):
            with self.subTest(reason=reason):
                self.assertTrue(
                    INSTALLER.transient_failure(
                        "E: Failed to fetch https://example.invalid/a.deb " + reason
                    )
                )

    def test_non_apt_exit_and_command_timeout_do_not_retry(self) -> None:
        self.invoke.return_value = (137, TEMPORARY)
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.fetch(["update"], 820)
        self.assertEqual(self.invoke.call_count, 1)
        self.invoke.reset_mock()
        self.invoke.side_effect = INSTALLER.AptError("timeout")
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.fetch(["update"], 820)
        self.assertEqual(self.invoke.call_count, 1)
        self.sleep.assert_not_called()

    def test_offline_install_failure_is_never_retried(self) -> None:
        self.invoke.side_effect = [(0, ""), (0, ""), (0, ""), (100, TEMPORARY)]
        with self.assertRaisesRegex(INSTALLER.AptError, "Offline"):
            INSTALLER.install(["dbus"])
        self.assertEqual(self.invoke.call_count, 4)
        self.sleep.assert_not_called()

    def test_commands_share_one_total_deadline(self) -> None:
        self.clock.side_effect = [100, 100, 340, 580, 805]
        self.invoke.return_value = (0, "")
        INSTALLER.install(["dbus"])
        self.assertEqual([item.args[1] for item in self.invoke.call_args_list], [240, 240, 240, 15])

    def test_no_new_command_or_sleep_after_budget_exhaustion(self) -> None:
        self.clock.return_value = 821
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.fetch(["update"], 820)
        self.invoke.assert_not_called()
        self.clock.return_value = 100
        self.invoke.return_value = (100, TEMPORARY)
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.fetch(["update"], 104)
        self.sleep.assert_not_called()

    def test_bad_packages_and_unavailable_apt_fail_early(self) -> None:
        for packages in ([], ["--allow-unauthenticated"], ["dbus=1"], ["dbus;true"], ["../dbus"]):
            with self.subTest(packages=packages), self.assertRaises(INSTALLER.AptError):
                INSTALLER.install(packages)
        self.invoke.assert_not_called()
        self.invoke.return_value = (1, "")
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.install(["dbus"])
        self.assertEqual(self.invoke.call_count, 1)


class TestOfficialPoolFallback(unittest.TestCase):
    """The fallback uses native APT and exact authenticated missing-package inputs."""

    URI = INSTALLER.SNAPSHOT + "pool/main/g/gcc-14/libcc1-0_14.2.0-19_amd64.deb"
    NAME = "libcc1-0_14.2.0-19_amd64.deb"
    BODY = b"test"

    def setUp(self) -> None:
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(INSTALLER.time, "sleep"))
        self.enterContext(patch.object(INSTALLER.time, "monotonic", return_value=100))
        self.enterContext(
            patch.object(
                INSTALLER.pwd, "getpwnam", return_value=SimpleNamespace(pw_uid=os.geteuid())
            )
        )
        self.digest = "SHA256:" + hashlib.sha256(self.BODY).hexdigest()
        self.plan = f"'{self.URI}' {self.NAME} 4 {self.digest}\n"
        self.failed = (
            f"E: Failed to fetch {self.URI} 503 Service Unavailable\n"
            "E: Some files failed to download\n"
        )
        self.commands: list[list[str]] = []
        self.cache: Path | None = None
        self.helper_body = self.BODY
        self.helper_status = 0
        self.partial_mode = 0o700

    def execute(self, command: list[str], _timeout: float) -> tuple[int, str]:
        self.commands.append(command)
        if command[0] == "/usr/bin/dpkg":
            return 0, "amd64\n"
        if command[0] == INSTALLER.APT_HELPER:
            index = command.index("download-file")
            target = Path(command[index + 2])
            self.assertFalse(target.exists())
            self.assertEqual(
                command[index + 1], INSTALLER.POOL + self.URI.removeprefix(INSTALLER.SNAPSHOT)
            )
            self.assertEqual(command[index + 3 :], [self.digest, "Checksum-FileSize:4"])
            self.assertNotIn("Acquire::ForceHash=sha256", command)
            self.assertIn("Acquire::ForceHash=", command)
            self.assertIn("Acquire::https::deb.debian.org::AllowRedirect=false", command)
            target.write_bytes(self.helper_body)
            return self.helper_status, ""
        cache = [x for x in command if x.startswith("Dir::Cache::archives=")]
        if cache:
            self.cache = Path(cache[0].split("=", 1)[1])
        if "--print-uris" in command:
            self.assertIn("Acquire::ForceHash=sha256", command)
            return 0, self.plan
        if "--download-only" in command:
            assert self.cache
            partial = self.cache / "partial"
            partial.mkdir(exist_ok=True)
            partial.chmod(self.partial_mode)
            return 100, self.failed
        if "--no-download" in command:
            assert self.cache
            self.assertEqual((self.cache / self.NAME).read_bytes(), self.BODY)
        return 0, ""

    def test_native_exact_byte_fallback_then_offline_install_and_cleanup(self) -> None:
        with patch.object(INSTALLER, "run", side_effect=self.execute):
            INSTALLER.install(["dbus"])
        self.assertEqual(sum(c[0] == INSTALLER.APT_HELPER for c in self.commands), 1)
        self.assertEqual(
            sum("--download-only" in c and "--print-uris" not in c for c in self.commands), 3
        )
        self.assertIn("--no-download", self.commands[-1])
        assert self.cache
        self.assertFalse(self.cache.exists())

    def test_equal_size_corruption_is_never_published_or_installed(self) -> None:
        self.helper_body = b"evil"
        with (
            patch.object(INSTALLER, "run", side_effect=self.execute),
            self.assertRaisesRegex(INSTALLER.AptError, "hash"),
        ):
            INSTALLER.install(["dbus"])
        self.assertFalse(any("--no-download" in c for c in self.commands))
        assert self.cache
        self.assertFalse(self.cache.exists())

    def test_helper_error_and_wrong_size_do_not_retry_or_install(self) -> None:
        for status, body in [(100, self.BODY), (0, b"too-long")]:
            with self.subTest(status=status):
                self.helper_status, self.helper_body = status, body
                self.commands.clear()
                with (
                    patch.object(INSTALLER, "run", side_effect=self.execute),
                    self.assertRaises(INSTALLER.AptError),
                ):
                    INSTALLER.install(["dbus"])
                self.assertEqual(sum(c[0] == INSTALLER.APT_HELPER for c in self.commands), 1)
                self.assertFalse(any("--no-download" in c for c in self.commands))

    def test_sandbox_directory_must_remain_private_and_traversable(self) -> None:
        self.partial_mode = 0o755
        with (
            patch.object(INSTALLER, "run", side_effect=self.execute),
            self.assertRaisesRegex(INSTALLER.AptError, "sandbox"),
        ):
            INSTALLER.install(["dbus"])
        self.assertFalse(any(c[0] == INSTALLER.APT_HELPER for c in self.commands))

    def test_plan_rejects_untrusted_paths_hashes_architecture_and_duplicates(self) -> None:
        invalid = [
            self.plan.replace("https://snapshot.debian.org", "https://evil.invalid"),
            self.plan.replace("20261012T", "20261013T"),
            self.plan.replace("pool/main/", "dists/main/"),
            self.plan.replace("pool/main/", "pool/main/../"),
            self.plan.replace("pool/main/", "pool/main/%2f"),
            self.plan.replace("pool/main/", "pool/main/%xx"),
            self.plan.replace(".deb'", ".deb?q=1'"),
            self.plan.replace("amd64", "arm64"),
            self.plan.replace("SHA256:", "MD5Sum:"),
            self.plan.replace(" 4 ", " 0 "),
            self.plan.replace(" 4 ", " 9999999999 "),
            self.plan + self.plan,
            self.plan + "W: Unauthenticated package\n",
            self.plan.replace(" " + self.NAME + " ", " ../escape.deb "),
        ]
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(INSTALLER.AptError):
                INSTALLER.parse_plan(plan, self.failed, "amd64")

    def test_plan_must_match_all_and_only_terminal_failed_packages(self) -> None:
        for failed in [
            self.failed.replace("libcc1-0_", "other_"),
            self.failed + self.failed.replace("libcc1-0_", "other_"),
            self.failed + self.failed,
        ]:
            with self.subTest(failed=failed), self.assertRaises(INSTALLER.AptError):
                INSTALLER.parse_plan(self.plan, failed, "amd64")
        parsed = INSTALLER.parse_plan(self.plan, self.failed, "amd64")
        self.assertEqual(parsed[0][1:], (self.NAME, 4, self.digest))

    def test_whole_plan_is_checked_before_helper_writes(self) -> None:
        self.plan += self.plan.replace("libcc1-0_", "other_")
        with (
            patch.object(INSTALLER, "run", side_effect=self.execute),
            self.assertRaises(INSTALLER.AptError),
        ):
            INSTALLER.install(["dbus"])
        self.assertFalse(any(c[0] == INSTALLER.APT_HELPER for c in self.commands))

    def test_count_and_aggregate_size_limits_are_not_per_file_only(self) -> None:
        with patch.object(INSTALLER, "MAX_PACKAGES", 0), self.assertRaises(INSTALLER.AptError):
            INSTALLER.parse_plan(self.plan, self.failed, "amd64")
        with (
            patch.object(INSTALLER, "MAX_FALLBACK_BYTES", 3),
            self.assertRaises(INSTALLER.AptError),
        ):
            INSTALLER.parse_plan(self.plan, self.failed, "amd64")

    def test_no_fallback_on_auth_integrity_or_index_failures(self) -> None:
        original_failure = self.failed
        for failure in [
            original_failure + "E: Hash Sum mismatch\n",
            original_failure + "E: NO_PUBKEY abc\n",
            original_failure.replace("503 Service Unavailable", "403 Forbidden"),
        ]:
            with self.subTest(failure=failure):
                self.failed = failure
                self.commands.clear()
                with (
                    patch.object(INSTALLER, "run", side_effect=self.execute),
                    self.assertRaises(INSTALLER.AptError),
                ):
                    INSTALLER.install(["dbus"])
                self.assertFalse(
                    any(c[0] == INSTALLER.APT_HELPER or "--print-uris" in c for c in self.commands)
                )


class TestBoundedProcess(unittest.TestCase):
    """Check real child-process output and termination without invoking APT."""

    def test_output_and_returncode(self) -> None:
        status, output = INSTALLER.run(
            [sys.executable, "-c", "print('ok'); raise SystemExit(7)"], 5
        )
        self.assertEqual((status, output.strip()), (7, "ok"))

    def test_command_timeout_is_bounded(self) -> None:
        started = time.monotonic()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(INSTALLER.AptError, "time budget"),
        ):
            INSTALLER.run([sys.executable, "-c", "import time; time.sleep(30)"], 0.1)
        self.assertLess(time.monotonic() - started, 5)

    def test_output_flood_is_bounded(self) -> None:
        captured = io.StringIO()
        with (
            patch.object(INSTALLER, "MAX_OUTPUT_BYTES", 4096),
            contextlib.redirect_stdout(captured),
            self.assertRaisesRegex(INSTALLER.AptError, "output budget"),
        ):
            INSTALLER.run([sys.executable, "-c", "import os; os.write(1, b'x' * 1000000)"], 5)
        self.assertEqual(len(captured.getvalue()), 4096)

    def test_inherited_pipe_cannot_outlive_deadline(self) -> None:
        # The direct child exits but its descendant keeps the output pipe open.
        program = (
            "import subprocess, sys; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"
        )
        started = time.monotonic()
        with (
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(INSTALLER.AptError, "time budget"),
        ):
            INSTALLER.run([sys.executable, "-c", program], 0.2)
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
