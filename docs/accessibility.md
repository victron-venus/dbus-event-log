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

This review checked the repository's Markdown, diagram descriptions and CLI
output options. It did not test the tables with a screen reader or establish
WCAG conformance for GitHub or an operator's Grafana dashboard. The OpenSSF
assessment records the remaining review requirement.

Report a barrier through [GitHub Issues](https://github.com/victron-venus/dbus-event-log/issues).
Include the affected page or command, expected behaviour, platform and assistive
technology when relevant. Remove credentials and private captured data.
