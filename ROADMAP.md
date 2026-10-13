# Maintenance roadmap

This roadmap covers October 2026 through October 2027. Priorities are reviewed in public issues and pull requests. These are work items, not delivery-date promises or claims that a badge has been awarded.

1. Establish and verify the real emergency-access/continuity arrangement described in [GOVERNANCE.md](GOVERNANCE.md). This is an open mandatory Silver requirement.
2. Maintain the [verified signed-release and complete-container repeatability process](docs/release-acceptance.md). Re-run exact RC acceptance for code, dependency or packaging changes, preserve original provenance and verify each new stable publication. Historical unsigned releases remain distinguishable.
3. Keep the combined application, packaging and release-tooling test run above 80% of executable Python statements. Increase coverage of currently unexecuted release helpers and security-relevant error paths; do not remove them from the denominator to pass the gate.
4. Maintain the six-month bugfix/regression ledger. Resolve entries without linked regression evidence and refresh the reviewed time window before submitting a badge assessment.
5. Keep dependency/security analyses current and investigate findings; periodically re-evaluate the assumptions in [the security assurance case](docs/security-assurance.md), including native dependencies and deployment boundaries.
6. Maintain the published Passing assessment and recheck each Silver criterion against the merged revision and public CI/release results. Human knowledge and response-history answers must come from the responsible maintainer, not repository prose.

## Scope and non-goals

Maintain bounded diagnostic capture, dependable storage and optional live publication. The project does not intend to become a tamper-proof compliance audit system, control D-Bus hardware methods, or run an unauthenticated public telemetry service during this roadmap period.
