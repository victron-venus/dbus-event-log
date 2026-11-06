# Dependency updates

## Coupled automatic updates

Renovate updates the project manifest and `uv.lock`, then regenerates the exports
below with uv 0.13.4 before committing the dependency update. The reviewed runner
allows only these fixed commands, executes them without a shell, and retains only
the listed output files. Each command checks source/lock consistency offline and
disables package builds; an outdated lock fails without rewriting the output.

For a manual dependency update, edit the manifest and run `uv lock` first, then
run the same export commands and the repository's normal checks. Commit the
manifest, lock and generated exports together.

```sh
uv export --locked --offline --no-build --no-dev --extra monitor --no-emit-project --format requirements-txt --output-file requirements-docker.txt
```
