"""Exercise bounded, authenticated APT fetching without a network or root access."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import time
import unittest
from pathlib import Path
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
        self.run = self.enterContext(patch.object(INSTALLER, "run"))
        self.clock = self.enterContext(patch.object(INSTALLER.time, "monotonic", return_value=100))
        self.sleep = self.enterContext(patch.object(INSTALLER.time, "sleep"))

    def test_success_keeps_checks_enabled_and_installs_offline(self) -> None:
        self.run.return_value = (0, "")
        INSTALLER.install(["dbus", "libdbus-1-3"])
        commands = [item.args[0] for item in self.run.call_args_list]
        self.assertEqual(commands[0], [INSTALLER.APT, "--version"])
        self.assertEqual(commands[1][-2:], ["update", "--error-on=any"])
        selection = ["install", "-y", "--no-install-recommends", "dbus", "libdbus-1-3"]
        self.assertEqual(
            commands[2], [INSTALLER.APT, *INSTALLER.OPTIONS, "--download-only", *selection]
        )
        self.assertEqual(
            commands[3], [INSTALLER.APT, *INSTALLER.OPTIONS, "--no-download", *selection]
        )
        for command in commands[1:]:
            self.assertIn("Acquire::Retries=0", command)
            self.assertNotIn("--allow-unauthenticated", command)
            self.assertNotIn("--fix-missing", command)
        self.sleep.assert_not_called()

    def test_temporary_download_retries_same_operation_then_installs(self) -> None:
        self.run.side_effect = [(0, ""), (0, ""), (100, TEMPORARY), (0, ""), (0, "")]
        INSTALLER.install(["dbus"])
        self.assertEqual(self.run.call_args_list[2], self.run.call_args_list[3])
        self.sleep.assert_called_once_with(5)
        self.assertIn("--no-download", self.run.call_args_list[-1].args[0])

    def test_retry_limit_never_reaches_install(self) -> None:
        self.run.side_effect = [(0, ""), *[(100, TEMPORARY)] * 3]
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.install(["dbus"])
        self.assertEqual(self.run.call_count, 4)
        self.assertEqual(self.sleep.call_args_list, [call(5), call(10)])

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
                self.run.reset_mock()
                self.run.return_value = (100, TEMPORARY + diagnostic + "\n")
                with self.assertRaises(INSTALLER.AptError):
                    INSTALLER.fetch(["update", "--error-on=any"], 820)
                self.assertEqual(self.run.call_count, 1)
                self.sleep.assert_not_called()

    def test_incidental_numbers_and_missing_error_diagnostics_do_not_retry(self) -> None:
        for output in (
            "package 503 unavailable",
            "503 Service Unavailable",
            "E: Failed to fetch https://example.invalid/package503.deb unknown",
        ):
            with self.subTest(output=output):
                self.run.reset_mock()
                self.run.return_value = (100, output)
                with self.assertRaises(INSTALLER.AptError):
                    INSTALLER.fetch(["update"], 820)
                self.assertEqual(self.run.call_count, 1)

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
        self.run.return_value = (137, TEMPORARY)
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.fetch(["update"], 820)
        self.assertEqual(self.run.call_count, 1)
        self.run.reset_mock()
        self.run.side_effect = INSTALLER.AptError("timeout")
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.fetch(["update"], 820)
        self.assertEqual(self.run.call_count, 1)
        self.sleep.assert_not_called()

    def test_offline_install_failure_is_never_retried(self) -> None:
        self.run.side_effect = [(0, ""), (0, ""), (0, ""), (100, TEMPORARY)]
        with self.assertRaisesRegex(INSTALLER.AptError, "Offline"):
            INSTALLER.install(["dbus"])
        self.assertEqual(self.run.call_count, 4)
        self.sleep.assert_not_called()

    def test_commands_share_one_total_deadline(self) -> None:
        self.clock.side_effect = [100, 100, 340, 580, 805]
        self.run.return_value = (0, "")
        INSTALLER.install(["dbus"])
        self.assertEqual([item.args[1] for item in self.run.call_args_list], [240, 240, 240, 15])

    def test_no_new_command_or_sleep_after_budget_exhaustion(self) -> None:
        self.clock.return_value = 821
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.fetch(["update"], 820)
        self.run.assert_not_called()
        self.clock.return_value = 100
        self.run.return_value = (100, TEMPORARY)
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.fetch(["update"], 104)
        self.sleep.assert_not_called()

    def test_bad_packages_and_unavailable_apt_fail_early(self) -> None:
        for packages in ([], ["--allow-unauthenticated"], ["dbus=1"], ["dbus;true"], ["../dbus"]):
            with self.subTest(packages=packages), self.assertRaises(INSTALLER.AptError):
                INSTALLER.install(packages)
        self.run.assert_not_called()
        self.run.return_value = (1, "")
        with self.assertRaises(INSTALLER.AptError):
            INSTALLER.install(["dbus"])
        self.assertEqual(self.run.call_count, 1)


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
