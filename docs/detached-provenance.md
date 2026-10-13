# Repeatable OCI payloads with detached build evidence

This optional contract separates runnable container bytes from the truthful,
run-specific BuildKit evidence describing how they were produced. It does not
claim that signatures, invocation identifiers or build timestamps repeat.
Existing consumers keep inline attestations unless their reviewed release
policy explicitly opts in.

```json
{
  "sign_releases": true,
  "detached_provenance": [
    {
      "image": "container.oci.tar",
      "sidecar": "container.provenance.json",
      "platforms": ["linux/amd64", "linux/arm64"]
    }
  ]
}
```

The image must also be declared once as an OCI artifact in `versioning.artifacts`.
The generator embeds the exact required pair in the consumer validator. Removing
or changing policy cannot silently disable the requirement in an already
rendered consumer. Rerendering a policy change requires normal source review.

Build one OCI input for each platform with BuildKit provenance enabled, retaining
the frozen release plan used for both builds. The new assembler validates both
original graphs with the existing native OCI checks. It copies runtime manifests,
configs and layers unchanged into a canonical two-platform OCI archive. It copies
all reachable original metadata, including every attestation predicate, as exact
base64-encoded bytes into the bounded JSON sidecar. Original index and layout bytes
remain available for graph reconstruction. No command executes extracted content.

```sh
python3 scripts/release_provenance.py detach \
  --directory release-dist \
  --input linux/amd64=build/amd64.oci.tar \
  --input linux/arm64=build/arm64.oci.tar \
  --image container.oci.tar --sidecar container.provenance.json
python3 scripts/release_provenance.py verify --directory release-dist
```

The verifier requires the exact declared pair, both platform subjects, at least
one SLSA provenance predicate per platform, all graph references and blob hashes,
and the complete runnable archive hash. Unknown or unused sidecar metadata,
missing attestations, duplicated platforms and mismatched subjects fail closed.
The existing OCI parser verifies every attestation's subject against the actual
image manifest and the runtime config's source/version labels. It preserves
other SLSA fields, including materials and invocation details, but does not
independently reproduce every dependency or validate every predicate-specific
claim. Do not describe this as a complete SLSA certification.

Staging and aggregate receipt checks use the independently validated frozen plan.
Each independent build target may stage neither file or the complete pair; a
partial pair is rejected. Aggregate receipt and recipient checks require every
declared pair, including when other targets contain only wheels or signatures.
The signer authenticates both files and includes that plan in its signed release
inventory. Recipient verification authenticates a private snapshot of the
inventory and bundle, validates its plan against the reviewed local policy and
caller-supplied source SHA, and checks semantic bindings against those exact
signed payload digests. The sidecar never chooses its own expected source or
plan. This also rejects replacement of a semantically equivalent file after an
earlier inventory hash check.

Detaching attestations does not make image contents deterministic. A consumer
must fix its actual build inputs and generated timestamps/cache/bytecode, then
run the shipping recipe twice with fresh builders and identical source, plan and
toolchain. Compare the full published runnable `.oci.tar` bytes. Keep each
execution's original sidecar and verify it separately; matching layers alone do
not prove matching archives. Report actual hashes, environment and limitations
before claiming repeatable builds. A changed packaging recipe needs a new
candidate and its applicable acceptance checks.

## Native builds

For a declared detached pair, `assemble_native_container.py` checks the complete
native receipt inventory and its frozen version fields before detaching. It
then compares the held archive's full hash, size and runnable config digest to
the exact successful native smoke result. The runnable OCI, original provenance
sidecar and native build evidence are validated in private staging and published
with no-clobber links. Failed publication never replaces existing files; cleanup
only removes outputs still owned by that attempt. Without an opt-in pair, native
assembly retains the existing inline-attestation format.
