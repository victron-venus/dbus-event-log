# Accessibility

The project provides a background recorder, command-line commands and Markdown
documentation. Its CLI uses named options and text output and can be operated
with a keyboard. Monitoring is a bounded session that also accepts Ctrl+C.

## Reading output

The table view labels columns and values in text; colour is supplementary.
For a linear representation of recorded events, select JSON or CSV with the
query command's `--format` option with SQLite or TimescaleDB storage. `--output` writes the
result to a file for the reader's preferred tools. The SQLite-only `export`
command also supports JSON and CSV.
Use `dbus-event-log query --help` or `dbus-event-log export --help` to inspect the
available filters before reading a capture. Captures can contain private data;
keep exports within their intended audience.

The README supplies prose descriptions alongside its architecture diagrams.
Documentation uses headings, descriptive links and text labels for badges.
The linked status pages provide details independently of badge colour. GitHub
hosts the rendered documentation and contribution interface.

## Review boundary

The dated [CLI review](evidence/accessibility-cli-2026-10-12.json) exercised the
actual command entry point against an isolated SQLite fixture: main/query/export
help was readable without terminal colour escapes, and JSON/CSV exports preserved
the fixture's named fields. JSON standard output also preserved long strings,
literal terminal markup, Unicode and embedded newlines without wrapping or interpretation. The [rendered README review](evidence/accessibility-browser-2026-10-12.json) also checked headings,
labelled badge images, real table headers and keyboard navigation to this page.
Diagram alternatives and machine-readable output remain available.

This is a scoped review of authored documentation and text interfaces. It did not
test a screen reader or establish WCAG conformance for GitHub or an operator's
Grafana dashboard.

Report a barrier through [GitHub Issues](https://github.com/victron-venus/dbus-event-log/issues).
Include the affected page or command, expected behaviour, platform and assistive
technology when relevant. Remove credentials and private captured data.
