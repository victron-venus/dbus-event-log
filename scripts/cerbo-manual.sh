#!/bin/sh
# Manual-only launcher. Install beside config.yaml and the versioned current link.
set -eu
umask 077

usage() {
    printf '%s\n' 'Usage: recorder version | capture SECONDS [--capture-methods]'
    printf '%s\n' 'Capture requires an interactive terminal and stops after 1-900 seconds.'
}

case "${1:-}" in
    version)
        [ "$#" -eq 1 ] || { usage >&2; exit 64; }
        ;;
    capture)
        [ "$#" -ge 2 ] && [ "$#" -le 3 ] || { usage >&2; exit 64; }
        case "$2" in
            ''|*[!0-9]*|????*) usage >&2; exit 64 ;;
        esac
        [ "$2" -ge 1 ] && [ "$2" -le 900 ] || { usage >&2; exit 64; }
        if [ "$#" -eq 3 ] && [ "$3" != --capture-methods ]; then
            usage >&2
            exit 64
        fi
        if [ ! -t 0 ] || [ ! -t 1 ]; then
            printf '%s\n' 'Capture refused: use an interactive terminal; boot/cron/service execution is disabled.' >&2
            exit 78
        fi
        ;;
    ''|help|--help|-h)
        usage
        exit 0
        ;;
    *) usage >&2; exit 64 ;;
esac

base=$(CDPATH= cd -P "$(dirname "$0")" && pwd)
python="$base/current/.venv/bin/python"
if [ "$1" = version ]; then
    exec "$python" -c 'from importlib.metadata import version; print(version("dbus-event-log"))'
fi
duration=$2
shift 2
exec "$python" -m dbus_event_log.cli --config "$base/config.yaml" \
    monitor --duration "$duration" "$@"
