# Verify a signed release

Repositories that explicitly enable `sign_releases: true` in their reviewed
`.release-policy.json` publish `release-signatures.json` and
`release-signatures.sigstore.json`. The signed inventory covers every original
build asset, its build receipt and a `source-COMMIT.tar.gz` archive of the reviewed
Git source. It includes wheels, source packages and exported container archives
when those are part of the build. Signing a container archive does not separately
sign a mutable registry tag.

## Recipient verification

Install [Cosign 3.1.3 or newer from Sigstore](https://docs.sigstore.dev/cosign/system_config/installation/)
on a trusted workstation. Obtain a reviewed checkout of the project and the
expected 40-character source commit through its reviewed release record,
independently of the downloaded release metadata. Download all release assets
into an otherwise empty directory. Do not extract or execute them yet.

```sh
python3 /trusted/project/scripts/release_signing.py verify /downloads/release \
  --root /trusted/project --source-sha EXPECTED_REVIEWED_SOURCE_COMMIT
```

The verifier reads the repository and default branch only from the trusted local
policy. It authenticates the signed manifest before parsing its inventory. The
expected certificate identity is
`https://github.com/OWNER/REPO/.github/workflows/release-pipeline.yml@refs/heads/BRANCH`,
the issuer is `https://token.actions.githubusercontent.com`, and the certificate
must match the expected repository, branch and source commit. Then every listed
size and SHA-256 digest, the full source archive, and the complete set of files
are checked. Unexpected files and symlinks are rejected. Never replace these
expectations with values copied from an untrusted certificate or manifest.

The Sigstore bundle contains the short-lived certificate and transparency
evidence needed by [Cosign verification](https://docs.sigstore.dev/cosign/verifying/verify/).
No long-lived project private key or public-key download is needed. The signing
private key exists only in the ephemeral signing process, not in the repository,
Actions secrets or release distribution store.

`release-manifest.json` and `release-inputs-signature.json` are added after signing
and are not independently authenticated by this inventory. They are publisher
provenance, not recipient trust roots. All original build receipts are covered.
The source archive records the reviewed Git commit before the frozen version
overlay; the signed build receipts describe the exact package-version projection.
Retain the verified inventory to check bytes after transfer to an offline device.

## Maintainer adoption and lifecycle

Enable the boolean `sign_releases` option and regenerate with this toolkit. It
requires a public versioned release policy and an artifact pattern that includes
`release-assets-signature`. Review and test the generated changes before merging.
The normal build keeps no OIDC signing permission. A separate GitHub-hosted job
waits for both build and quality checks, validates the frozen build receipts,
signs the inventory, verifies it and checks that a wrong source identity fails.
The publication gate also requires the signing job, and the publisher verifies
the downloaded signature before changing release/tag state.

For `promote-bytes` policies, stable promotion retains the RC bytes and original
source identity. For `final-build` policies, the checked final artifacts receive
a new signed inventory before publication. An unsigned historical RC cannot be
promoted after signing is enabled: create a new signed candidate first. Historical unsigned releases remain distinguishable by the
absence of the two signature files. A successful workflow configuration alone
does not prove a signed release exists; publish and independently verify an
applicable release before making that claim. This authenticates published bytes;
it does not establish that code is safe or that a build is reproducible.
