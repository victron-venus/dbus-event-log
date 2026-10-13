# Native build flags

The native container build accepts `CC`, `CFLAGS`, `CXX`, `CXXFLAGS` and `LDFLAGS`. These are Docker build arguments available while compiling dependencies; they are not persisted as runtime environment variables. The application itself is Python. Pycairo and PyGObject compile C extensions in the supported image. The C++ settings are forwarded for toolchains or dependencies that use C++, but the present dependency build does not exercise them.

For a local image, pass each setting explicitly:

```bash
docker buildx build --load --platform linux/amd64 \
  --build-arg 'CC=gcc' \
  --build-arg 'CFLAGS=-O2 -g' \
  --build-arg 'LDFLAGS=-Wl,--build-id=sha1' \
  --tag dbus-event-log:local .
```

Use the matching native host (`linux/amd64` or `linux/arm64`); the release adapter deliberately rejects cross-architecture execution. These example flags are caller choices, not a recommendation to replace the dependency toolchain defaults. The compiler and any libraries requested by a flag must exist in the build image. Invalid compiler/linker settings cause the build to fail.

The release adapter `scripts/build-native-container.sh` forwards only the five named variables from its environment, preserving each complete value as one build argument. Unset variables are omitted; explicitly empty values are passed unchanged. It does not evaluate shell expressions in those values. The adapter still requires its normal reviewed version plan and GitHub Actions identity. Passing a compiler command is trusted build configuration, not a safe interface for untrusted input. Do not put credentials in build arguments, which can appear in build logs or provenance.

## Verification

The ordinary adapter tests verify unset and empty values, all five inputs, spaces and shell metacharacters. The native CI jobs then build this repository's real Dockerfile on AMD64 and ARM64, using nondefault `CC`, `CFLAGS` and `LDFLAGS`. They inspect both installed C extension binaries for the recorded compiler options and a linker-created absolute symbol, run the offline D-Bus smoke test as the image's non-root user, and verify that the build settings are absent from runtime `ENV`.

The jobs also build with an invalid compiler option and an invalid linker option. Each must fail with the corresponding toolchain diagnostic; a build failing for an unrelated reason does not pass the test. Logs and source-bound results are uploaded as `native-build-flags-amd64` and `native-build-flags-arm64` CI artifacts. These probe images are not published as releases. Review the actual workflow results before citing successful platform validation.
