# Dependency updates

## Runtime source and lock updates

The root application is a single reviewed update unit: `pyproject.toml`,
`uv.lock`, `requirements-docker.txt`, and `requirements-docker-cairo.txt`.
The optional monitor requires PyGObject, which has no compatible wheel. Its
resolution cannot run on the dependency updater because that runner forbids
package build-backend execution. Renovate excludes this exact unit from
automated changes, including security changes; GitHub vulnerability alerts
remain enabled.

In the qualified development environment, review and edit the manifest, then
use uv 0.13.4:

```sh
uv lock
uv export --locked --offline --no-build --no-dev --extra monitor --no-emit-project --format requirements-txt --output-file requirements-docker.txt
```

For a transitive security fix, run `uv lock --upgrade-package <name>` in place
of `uv lock` above, replacing `<name>` with the affected package. Then regenerate
the runtime export and Cairo companion as described below. Without the selective upgrade, uv can retain
the existing locked version. Verify that the resulting lock selects a version
in the advisory's fixed range on every affected marker branch. If the manifest
constraints exclude the fix, review and update the relevant bounds together
with this entire runtime unit before accepting it.

Copy the selected pycairo package and complete hash stanza from the regenerated
runtime export into `requirements-docker-cairo.txt`, preserving its explanatory
comment. The native installer builds pycairo from that companion before
PyGObject, so both files must describe the same selected pycairo release.
Review the source, lock and both requirement files together, then pass the
normal tests and complete Linux container builds before merging.

## Independent automatic updates

Renovate still updates CI references, container digests, workflow tooling, and
the supported native locks for `.github/requirements-build.in`,
`.github/requirements-bandit-1.9.2.in`, `.github/requirements-fuzz.in`, and
`requirements-docker-build.in`. Their recorded compile commands regenerate
hashed outputs together with source changes. The named Bandit 1.9.2 constraint
and Python 3.14 container tag qualification remain in force. Supported security
updates in these independent units remain automatic.
