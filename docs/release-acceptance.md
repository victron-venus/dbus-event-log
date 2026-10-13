# Release acceptance and build evidence

The verified release is [v0.2.1](https://github.com/victron-venus/dbus-event-log/releases/tag/v0.2.1), promoted byte for byte from [v0.2.1-rc.3](https://github.com/victron-venus/dbus-event-log/releases/tag/v0.2.1-rc.3). Its reviewed source is `a02ec28b1b406a760bf9a61032e90df277589c6f`. The exact original candidate run is [38344408225](https://github.com/victron-venus/dbus-event-log/actions/runs/38344408225); stable promotion is [38392023294](https://github.com/victron-venus/dbus-event-log/actions/runs/38392023294).

## Recipient verification

The signed inventory, Sigstore identity and every original signed payload were verified against the expected repository, default branch, workflow and reviewed source, independently of the downloaded metadata. `release-manifest.json` and `release-inputs-signature.json` are unsigned publisher metadata outside that signed inventory; they are not recipient trust roots. The wrong-source identity check was rejected. The separately signed provenance sidecar remains mandatory; verification checks its original native input graph and the frozen version plan. [Verification receipt](evidence/release-verification-2026-10-13.json) records the exact hashes and results. All 12 stable files were re-downloaded and compared byte for byte with the accepted RC3. The original RC manifest and signature are intentionally preserved by this promote-bytes policy. Historical unsigned releases are not retroactively signed.

## Accepted target and limitations

[The recorded RC acceptance](https://github.com/victron-venus/dbus-event-log/pull/145#issuecomment-6144748340) was completed before stable promotion.

The exact downloaded ARM64 OCI image was exercised on an isolated Linux **companion host**, with a private D-Bus session and synthetic events. No live Venus OS bus, production broker, physical device or production database was accessed. The image ran as its non-root user with network access disabled, a read-only root filesystem, a temporary writable `/tmp`, all Linux capabilities dropped and no-new-privileges.

The acceptance checks exercised native GI/GLib/Gio/cairo loading, method attribution and reply correlation, signal capture, real temporary SQLite persistence, clean and bounded stopping, and JSON/CSV output to standard output and files. Long text, Unicode, literal terminal markup and embedded newlines were preserved. [Runtime acceptance receipt](evidence/release-runtime-2026-10-13.json) binds these results to the exact loaded image config and archive. The original release build also ran native AMD64 and ARM64 smoke tests against the exact exported config recorded in its signed evidence. Independent local runtime acceptance was ARM64 only; AMD64 was inspected statically. The local Docker-loaded index, native ARM64 config and all 13 root filesystem layer identities were matched to the authenticated archive.

This validates the container's documented companion-host mode and private test bus. It does not validate a native Cerbo installation, firmware D-Bus compatibility, physical hardware, external MQTT/TimescaleDB deployments or production upgrades. Those targets require their own operator acceptance; the README's manual/unvalidated native Cerbo boundary remains in force.

## Repeatable complete runnable archive

[The final PR cold-build check](https://github.com/victron-venus/dbus-event-log/actions/runs/38343716785/job/115121153350) used two independent native builds per architecture. Both assembled runnable OCI tars were **335206400 bytes** with SHA-256 `56c38f4551031291f07504dfcd313876530f40e59e53d05be7a870e9c7956ce0`. [The original comparison record](evidence/container-repeatability-2026-10-12.json) identifies its exact PR merge source `95bbb71a5821394217966599eee5300d70b5e298` and fixed diagnostic plan. This comparison covers every byte of the runnable OCI archive, not a selected application layer or wheel.

Each independent build's truthful original attestations were retained and verified separately; their execution timestamps are not expected to repeat. The diagnostic comparison is not a claim that an archive with a different source or frozen version plan has the same digest. The [container recipe](container-repeatability.md) records the pinned inputs and required security-refresh process.

## Native runtime hardening

[Static ELF inspection](evidence/release-elf-2026-10-13.json) of the final distributed image found 93 Python runtime/native extension objects under `/usr/local` on each architecture. 93 had GNU_RELRO; 0 requested an executable GNU_STACK; 1 had immediate binding; 74 referenced `__stack_chk_fail`.

The Python executable is ET_DYN (PIE); most selected objects have partial RELRO. This static inspection does not establish full stack-canary coverage, host ASLR or process-wide W^X. Separately, the native ARM64 acceptance invocation observed ASLR level 2 without a process opt-out, no writable-executable mappings, a non-executable stack, UID 1000, zero effective capabilities and no-new-privileges. The read-only root filesystem, capability restrictions and other isolation options describe that tested invocation, not automatic defaults for every deployment. The inventory does not cover every system executable/library or claim comprehensive fortification or sanitizer coverage. The native compiler/linker forwarding checks also exercised configured flags and rejected invalid flags on both architectures.
