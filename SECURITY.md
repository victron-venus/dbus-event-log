# Security policy

## Reporting a vulnerability

Send vulnerability details privately using [GitHub's Report a vulnerability form](https://github.com/victron-venus/dbus-event-log/security/advisories/new). Include the affected commit or release, reproduction steps, impact and a suggested mitigation if known. Remove live secrets and personal information. Do not open a public issue with exploit details. If private reporting is temporarily unavailable, open a public issue requesting a confidential contact without disclosing the vulnerability.

Maintainers aim to acknowledge reports within 14 days, investigate promptly and coordinate disclosure with the reporter. Critical confirmed vulnerabilities receive priority. Confirmed exploitable medium-or-higher issues should be corrected within 60 days of confirmation. Coordinate normal public disclosure with a fix or effective mitigation. If active exploitation or another urgent risk requires earlier warning, promptly publish the affected scope and available protective steps while remediation continues. Release notes should identify any assigned CVE or equivalent advisory identifier for fixes. These are maintenance policies, not claims about historical response times.

## Supported code

Security fixes are developed on the current default branch and released through the repository's normal delivery process. Older releases are not guaranteed backports; reproduce against the current code where practical and include the original affected version in the report. A prerelease or development build is not a promise of production or hardware acceptance.

## Trust boundaries

A D-Bus capture can contain identifiers, commands and confidential values. Limit collection duration, restrict access to capture files and database endpoints, and redact exports before sharing. Capture privileges and retention are operator responsibilities.

TimescaleDB uses verified TLS by default, with no plaintext fallback. Follow the
[database transport instructions](README.md#timescaledb-transport) for CA trust,
client certificates and the explicit trusted-local opt-out. This opt-out removes
transport protection and must not be used on an untrusted network. Configure
server-side password authentication to use SCRAM-SHA-256; transport verification
does not change the PostgreSQL account authentication policy.

## Secure development and delivery

Validate external values at trust boundaries, reject unsupported or malformed commands, avoid shell interpolation, preserve certificate verification, and use maintained cryptographic libraries rather than custom cryptography. Apply least privilege to service accounts, repository tokens and filesystem permissions. Follow [CONTRIBUTING.md](CONTRIBUTING.md) for validation and review.

Obtain source and published artifacts through the repository's HTTPS URLs. Verify published checksums or provenance when provided, over an authenticated channel. Keep local configuration, credentials and private keys out of source control and logs. Report suspected exposure through the private channel so credentials can be revoked and replaced; deleting a file alone does not revoke it.

[OpenSSF evidence and remaining verification](docs/openssf-evidence.md) is maintained separately from this policy.

## Recognition, credentials and assurance review

With the reporter's consent, acknowledge vulnerability reporters in the advisory or release notes. Offer anonymity, and do not make credit depend on public disclosure before remediation. This is the policy for future reports; it does not assert a history of reports or awards.

If a repository token, service credential or signing identity is compromised, revoke or disable it at its provider, replace it through the normal secret store, inspect access/publication logs and invalidate affected artifacts where necessary. A source-code deletion does not revoke a credential. Prefer scoped, short-lived credentials; never publish private recovery material.

[The project security assurance case](docs/security-assurance.md) records claims, test evidence and limitations. Revisit it when interfaces, privilege, transport, storage or release mechanisms change. The public issue/PR history records design decisions; confidential details stay in the private reporting channel until coordinated disclosure.
