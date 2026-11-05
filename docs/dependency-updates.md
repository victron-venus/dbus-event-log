# Dependency updates

## Automated update boundary

Renovate updates CI tools, container references, and independent requirement inputs.
Root `pyproject.toml` dependencies remain on the coupled manual source workflow:
the native updater refreshes `uv.lock` but cannot regenerate these `uv export`
outputs. Edit the project dependencies and run all commands below together; review
and commit the project, lock, and exported requirement files in one change.

```sh
uv lock
uv export --locked --no-dev --extra monitor --no-emit-project --format requirements-txt --output-file requirements-docker.txt
```
