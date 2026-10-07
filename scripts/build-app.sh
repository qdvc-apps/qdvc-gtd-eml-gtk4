#!/usr/bin/env bash
# Check, test, stage and install QDVC GTD EML for GNOME (per user, no root).
#
#   scripts/build-app.sh              # check deps, byte-compile, test, stage in build/
#   scripts/build-app.sh --install    # ... then install to ~/.local and refresh GNOME
#   scripts/build-app.sh --uninstall  # remove the installed copy, launcher and icons
#   scripts/build-app.sh --run [dir]  # run straight from the checkout (no install)
#   scripts/build-app.sh --no-test    # skip the tests (combine with --install)
#
# The installed copy (~/.local/share/qdvc-gtd-eml/) is independent of this
# checkout, so editing the source never breaks the app in the Dock until you
# reinstall. Reinstalling quits a running copy so the next launch is fresh.
set -euo pipefail

cd "$(dirname "$0")/.."

APP_ID="qdvc.GtdEml"
NAME="qdvc-gtd-eml"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN_DIR="$HOME/.local/bin"
DEST="$DATA_HOME/$NAME"
STAGE="build/$NAME"

install=0; uninstall=0; run=0; tests=1; run_args=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --install) install=1 ;;
        --uninstall) uninstall=1 ;;
        --run) run=1; shift; run_args=("$@"); break ;;
        --no-test) tests=0 ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done

quit_running_copy() {
    # Ask a running instance to quit (its app.quit action, over D-Bus).
    if command -v gdbus >/dev/null 2>&1; then
        gdbus call --session --dest "$APP_ID" --object-path "/${APP_ID//./\/}" \
            --method org.gtk.Actions.Activate quit '[]' '{}' >/dev/null 2>&1 \
            && echo "==> Quit the running copy" || true
    fi
}

refresh_desktop() {
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database -q "$DATA_HOME/applications" || true
    if command -v gtk-update-icon-cache >/dev/null 2>&1 \
            && [[ -f "$DATA_HOME/icons/hicolor/index.theme" ]]; then
        gtk-update-icon-cache -q -f -t "$DATA_HOME/icons/hicolor" || true
    fi
    # GNOME Shell picks up new launchers by watching the folder; touching it
    # nudges caches that are slow to notice.
    touch "$DATA_HOME/applications" "$DATA_HOME/icons/hicolor" 2>/dev/null || true
}

if [[ $uninstall -eq 1 ]]; then
    quit_running_copy
    rm -rf "$DEST" "$BIN_DIR/$NAME"
    rm -f "$DATA_HOME/applications/$APP_ID.desktop" \
          "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg" \
          "$DATA_HOME/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
    refresh_desktop
    echo "Uninstalled. Your preferences (~/.config/$NAME) and cache (~/.cache/$NAME) were kept."
    exit 0
fi

if [[ $run -eq 1 ]]; then
    exec python3 qdvc_gtd_eml.py "${run_args[@]}"
fi

echo "==> Checking dependencies"
python3 - <<'PY'
import sys
problems = []
if sys.version_info < (3, 10):
    problems.append(f"Python 3.10 or later (this is {sys.version.split()[0]})")
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 14):
        problems.append(f"GTK 4.14 or later (found {Gtk.get_major_version()}."
                        f"{Gtk.get_minor_version()})")
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 5):
        problems.append(f"libadwaita 1.5 or later (found {Adw.get_major_version()}."
                        f"{Adw.get_minor_version()})")
    else:
        print(f"    GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}, "
              f"libadwaita {Adw.get_major_version()}.{Adw.get_minor_version()}")
except (ImportError, ValueError) as e:
    problems.append(f"PyGObject with GTK 4 and libadwaita ({e})")
for module, package in (("yaml", "PyYAML"), ("cairo", "pycairo / python3-gi-cairo")):
    try:
        __import__(module)
    except ImportError:
        problems.append(package)
try:
    from gi.repository import PangoCairo  # noqa: F401
    import gi.repository.Gtk  # noqa: F401
except Exception:  # noqa: BLE001
    pass
if problems:
    print("Missing: " + "; ".join(problems), file=sys.stderr)
    print("  Debian/Ubuntu: sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 "
          "gir1.2-adw-1 python3-yaml", file=sys.stderr)
    print("  Fedora:        sudo dnf install python3-gobject python3-cairo gtk4 libadwaita "
          "python3-pyyaml", file=sys.stderr)
    sys.exit(1)
PY

echo "==> Byte-compiling"
python3 -m compileall -q qdvc qdvc_gtd_eml.py tools tests

if [[ $tests -eq 1 ]]; then
    echo "==> Running the tests"
    python3 tools/vendor_core.py --check
    if ! python3 -m unittest discover -s tests -t .; then
        echo "Tests failed; nothing was installed." >&2
        exit 1
    fi
fi

echo "==> Staging $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R qdvc qdvc_gtd_eml.py data "$STAGE/"
find "$STAGE" -name "__pycache__" -type d -prune -exec rm -rf {} +

if [[ $install -eq 1 ]]; then
    quit_running_copy
    echo "==> Installing to $DEST"
    rm -rf "$DEST"
    mkdir -p "$(dirname "$DEST")" "$BIN_DIR" "$DATA_HOME/applications" \
             "$DATA_HOME/icons/hicolor/scalable/apps" "$DATA_HOME/icons/hicolor/symbolic/apps"
    cp -R "$STAGE" "$DEST"
    python3 -m compileall -q "$DEST" || true
    cat > "$BIN_DIR/$NAME" <<SH
#!/bin/sh
exec python3 "$DEST/qdvc_gtd_eml.py" "\$@"
SH
    chmod +x "$BIN_DIR/$NAME"
    sed "s|@EXEC@|$BIN_DIR/$NAME|" data/$APP_ID.desktop.in \
        > "$DATA_HOME/applications/$APP_ID.desktop"
    cp data/icons/hicolor/scalable/apps/$APP_ID.svg "$DATA_HOME/icons/hicolor/scalable/apps/"
    cp data/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg \
        "$DATA_HOME/icons/hicolor/symbolic/apps/"
    if command -v desktop-file-validate >/dev/null 2>&1; then
        desktop-file-validate "$DATA_HOME/applications/$APP_ID.desktop" || true
    fi
    refresh_desktop
    echo "==> Installed: search for “QDVC GTD EML” in Activities, or run $BIN_DIR/$NAME"
fi

echo "Done."
