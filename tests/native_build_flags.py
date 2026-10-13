"""Prove compiler and linker build arguments reach the real native dependencies.

Run by native Linux CI, independently of the released image. No image is published.
"""

import argparse
import importlib.util
import json
import os
import platform
import re
import subprocess
import uuid
from pathlib import Path

VARIABLES = ("CC", "CFLAGS", "CXX", "CXXFLAGS", "LDFLAGS")


def inspect_native_libraries() -> None:
    """Inspect compiler switches and a linker-created symbol in both extensions."""
    for module in ("cairo._cairo", "gi._gi"):
        spec = importlib.util.find_spec(module)
        if spec is None or spec.origin is None:
            raise RuntimeError(f"Missing compiled extension: {module}")
        flags = subprocess.check_output(
            ["readelf", "-p", ".GCC.command.line", spec.origin], text=True
        )
        for flag in ("-fno-inline", "-O1", "-fno-omit-frame-pointer"):
            if flag not in flags:
                raise RuntimeError(f"Compiler option {flag} absent in {module}")
        symbols = subprocess.check_output(["readelf", "-Ws", spec.origin], text=True)
        if not re.search(r"\b0*51a7\b[^\n]*\bABS\s+openssf_link_flag\b", symbols):
            raise RuntimeError(f"Linker option marker absent in {module}")


def run_native_probe(target: str, output: Path) -> None:
    """Build the real Dockerfile with observable flags and reject invalid ones."""
    expected_machine = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}[target]
    if platform.system() != "Linux" or platform.machine() != expected_machine:
        raise RuntimeError("Compiler proof requires the requested native Linux architecture")
    root = Path(__file__).resolve().parents[1]
    output.mkdir(parents=True, exist_ok=False)
    image = f"dbus-event-log-flag-proof:{uuid.uuid4().hex}"
    base = [
        "docker",
        "buildx",
        "build",
        "--progress",
        "plain",
        "--platform",
        target,
        "--load",
        "--tag",
        image,
    ]
    cases: tuple[tuple[str, dict[str, str], str | None], ...] = (
        (
            "positive",
            {
                "CC": "gcc -fno-inline",
                "CFLAGS": "-O1 -frecord-gcc-switches -fno-omit-frame-pointer",
                "LDFLAGS": "-Wl,--defsym=openssf_link_flag=0x51a7",
            },
            None,
        ),
        (
            "invalid-compiler-flag",
            {"CFLAGS": "-fopenssf-invalid-option"},
            r"(?:unrecognized command-line option|unknown argument)[^\n]*-fopenssf-invalid-option",
        ),
        (
            "invalid-linker-flag",
            {"LDFLAGS": "-Wl,--openssf-invalid-option"},
            r"ERROR: Compiler cc cannot compile programs\.",
        ),
    )
    results = []
    try:
        for name, values, diagnostic in cases:
            arguments = []
            for key, value in values.items():
                arguments.extend(["--build-arg", f"{key}={value}"])
            log = output / f"{name}.log"
            with log.open("w") as stream:
                result = subprocess.run(
                    [*base, *arguments, "."],
                    cwd=root,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            if diagnostic is not None:
                if result.returncode == 0 or not re.search(diagnostic, log.read_text()):
                    raise RuntimeError(
                        f"{name} did not reject the expected native option; see {log}"
                    )
            else:
                if result.returncode:
                    raise RuntimeError(f"Native compiler proof build failed; see {log}")
                inspected = json.loads(
                    subprocess.check_output(["docker", "image", "inspect", image], text=True)
                )[0]
                runtime_env = inspected["Config"].get("Env", [])
                if any(entry.split("=", 1)[0] in VARIABLES for entry in runtime_env):
                    raise RuntimeError("Build-only compiler settings leaked into runtime ENV")
                if inspected["Config"].get("User", "").split(":", 1)[0] in {"", "0", "root"}:
                    raise RuntimeError("Compiler proof must preserve the non-root image user")
                mount = f"type=bind,source={root / 'tests'},target=/proof,readonly"
                subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--platform",
                        target,
                        "--mount",
                        mount,
                        "--entrypoint",
                        "python",
                        image,
                        "/proof/native_build_flags.py",
                        "--inspect",
                    ],
                    check=True,
                )
                subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--platform",
                        target,
                        "--mount",
                        mount,
                        "--entrypoint",
                        "dbus-run-session",
                        image,
                        "--",
                        "python",
                        "/proof/smoke_monitor.py",
                    ],
                    check=True,
                )
            case_result: dict[str, str | int | bool] = {
                "case": name,
                "returncode": result.returncode,
                "verified": True,
            }
            if name == "invalid-linker-flag":
                # Meson hides the sanity-check stderr; independently retain ld's exact rejection.
                rejection = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--platform",
                        target,
                        "--interactive",
                        "--entrypoint",
                        "cc",
                        image,
                        "-x",
                        "c",
                        "-",
                        "-o",
                        "/tmp/openssf-flag-probe",
                        "-Wl,--openssf-invalid-option",
                    ],
                    input="int main(void) { return 0; }\n",
                    capture_output=True,
                    text=True,
                    check=False,
                )
                (output / "invalid-linker-toolchain.log").write_text(
                    f"returncode={rejection.returncode}\n" + rejection.stdout + rejection.stderr
                )
                if rejection.returncode == 0 or not re.search(
                    r"(?:unrecognized option|unknown argument)[^\n]*--openssf-invalid-option",
                    rejection.stderr,
                ):
                    raise RuntimeError("Native linker did not reject the exact invalid option")
                case_result["toolchain_returncode"] = rejection.returncode
                case_result["toolchain_log"] = "invalid-linker-toolchain.log"
                case_result["build_diagnostic"] = "Meson compiler sanity failure (ld stderr hidden)"
            results.append(case_result)
    finally:
        # Remove only this probe's unique image; never prune the runner's image store.
        subprocess.run(["docker", "image", "rm", "--force", image], check=False)
    (output / "results.json").write_text(
        json.dumps(
            {
                "platform": target,
                "source_sha": os.environ.get("GITHUB_SHA"),
                "cases": results,
                "scope": (
                    "Real pycairo and PyGObject compilation/linking; CXX flags are argv-tested "
                    "only because these dependencies compile C."
                ),
            },
            indent=2,
        )
        + "\n"
    )


def main() -> None:
    """Select host build proof or in-container compiled-library inspection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.inspect:
        inspect_native_libraries()
    elif args.platform and args.output:
        run_native_probe(args.platform, args.output)
    else:
        parser.error("Use --inspect or both --platform and --output")


if __name__ == "__main__":
    main()
