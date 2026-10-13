# Governance and maintenance

dbus-event-log is maintained in public in the [victron-venus repository](https://github.com/victron-venus/dbus-event-log). This document defines project responsibilities, not a claim that several independent maintainers currently fill them.

## Roles and decisions

Contributors propose changes through issues and pull requests, provide tests and documentation, and preserve license notices. Reviewers check behavior, compatibility, security boundaries and test evidence. Repository maintainers decide scope and merge changes after the protected branch's required checks and reviews. A review bot may help find defects; it is not a human maintainer or successor.

Release maintainers verify the reviewed source, checks, release notes and produced artifacts before authorizing publication. Organization administrators control repository access, branch protection and credential revocation; administrative access must not substitute for ordinary change review.

Use an issue for a proposal affecting interfaces, supported platforms, dependencies or security assumptions. Describe the problem, alternatives, compatibility and migration implications. Record the maintainer's decision and rationale publicly before implementing a disputed design. Small fixes can use the pull request itself. Report confidential concerns through [SECURITY.md](SECURITY.md). Contributors can request reconsideration with new evidence in the issue; maintainers should explain the final decision respectfully.

## Continuity: open requirement

There is currently **no documented and verified succession or emergency-access arrangement** for this project. Do not report the OpenSSF Silver `access_continuity` requirement as met. Bots, organization membership and a public clone do not establish recovery of repository administration, release publication or security reporting.

Before closing this gap, the owner must establish a real arrangement (for example an authorized successor or a controlled recovery arrangement), inventory critical services, verify recovery without publishing credentials, and record the responsible role, review date and procedure here. See [ROADMAP.md](ROADMAP.md). No person is assigned responsibility by this document without their consent.

## Participation

Participation follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Contributions use the existing project [license](LICENSE). The project accepts English issues and documentation; evidence and constructive disagreement are welcome.
