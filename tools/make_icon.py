#!/usr/bin/env python3
"""Draw the app icon and the bundled symbolic icons as SVG.

    python3 tools/make_icon.py            # writes data/icons/hicolor/...
    python3 tools/make_icon.py --preview  # also renders build/icon-preview.png
                                          # (needs rsvg-convert)

App icon (GNOME HIG style: a 128 × 128 object, not a tile, lit from above, on
the Adwaita palette): the family's envelope, sealed with a green check mark,
in the sage palette of the macOS edition. Its symbolic variant is a single
flat outline. The workflow icons are 16 × 16 symbolic SVGs for the concepts
the stock Adwaita theme has no icon for, in the style of GNOME's Icon Library.
"""

import argparse
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "data" / "icons" / "hicolor"
APP_ID = "qdvc.GtdEml"

APP_ICON = """<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#e8ede6"/>
    </linearGradient>
    <linearGradient id="flap" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#a9c3a0"/><stop offset="1" stop-color="#7fa077"/>
    </linearGradient>
    <linearGradient id="seal" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#57e389"/><stop offset="1" stop-color="#26a269"/>
    </linearGradient>
  </defs>
  <!-- base / shadow side (GNOME icons sit on a darker "edge") -->
  <rect x="12" y="30" width="104" height="76" rx="10" fill="#5e7a57"/>
  <rect x="12" y="26" width="104" height="76" rx="10" fill="url(#body)"/>
  <!-- lower folds -->
  <path d="M16 98 L56 64 Q64 58 72 64 L112 98" fill="none" stroke="#c6d3c1" stroke-width="3"
        stroke-linejoin="round"/>
  <!-- flap -->
  <path d="M14 34 Q14 28 22 28 L106 28 Q114 28 114 34 L72 68 Q64 74 56 68 Z" fill="url(#flap)"/>
  <path d="M14 34 L56 68 Q64 74 72 68 L114 34" fill="none" stroke="#5e7a57" stroke-width="2"
        stroke-linejoin="round" opacity=".5"/>
  <!-- check seal -->
  <circle cx="92" cy="88" r="25" fill="#1f7a4d"/>
  <circle cx="92" cy="85" r="25" fill="url(#seal)"/>
  <path d="M80 85 L89 94 L105 76" fill="none" stroke="#ffffff" stroke-width="7"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

SYMBOLIC_APP = """<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <path fill="#222" d="M2.5 3A1.5 1.5 0 0 0 1 4.5v7A1.5 1.5 0 0 0 2.5 13H8v-1.5H2.5v-6l5 3.5 5-3.5V8H14V4.5A1.5 1.5 0 0 0 12.5 3zm.6 1.5h9.8L8 8z"/>
  <path fill="#222" d="M15.7 10.3l-1-1L11.5 12.5 10 11l-1 1 2.5 2.5z"/>
</svg>
"""


def sym(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
            f'viewBox="0 0 16 16"><g fill="#222">{body}</g></svg>\n')


ACTIONS = {
    # Input: a tray with an arrow into it.
    "gtd-input-symbolic": sym(
        '<path d="M7 1h2v5h2.5L8 9.5 4.5 6H7z"/>'
        '<path d="M1 9h3.5l1 2h5l1-2H15v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 1 13.5z"/>'),
    # Triage: a funnel.
    "gtd-triage-symbolic": sym('<path d="M1 2h14v1.5L10 9v5l-4 1.5V9L1 3.5z"/>'),
    # Actionable: a lightning bolt.
    "gtd-actionable-symbolic": sym('<path d="M9.5 1L3 9h4.5L6 15l7-8.5H8.5z"/>'),
    # Delegated: two people.
    "gtd-delegated-symbolic": sym(
        '<circle cx="5.5" cy="4.5" r="2.5"/><circle cx="11.5" cy="5.5" r="2"/>'
        '<path d="M1 13c0-3 2-5 4.5-5S10 10 10 13v1H1z"/>'
        '<path d="M11 14v-1c0-1.6-.5-3-1.4-4 .5-.3 1.1-.5 1.9-.5 2 0 3.5 1.7 3.5 4V14z"/>'),
    # Reference: books on a shelf.
    "gtd-reference-symbolic": sym(
        '<rect x="1" y="2" width="3" height="11" rx=".5"/><rect x="5" y="4" width="3" '
        'height="9" rx=".5"/><path d="M9.2 4.4l2.4-.9 3.4 9-2.4.9z"/><rect x="0" y="14" '
        'width="16" height="1.5"/>'),
    # Archive: a box with a lid.
    "gtd-archive-symbolic": sym(
        '<rect x="1" y="2" width="14" height="4" rx="1"/>'
        '<path d="M2 7h12v6.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 13.5zm4 2v1.5h4V9z"/>'),
    # Performance: a bar chart.
    "gtd-chart-symbolic": sym(
        '<rect x="1" y="8" width="3" height="6" rx=".5"/><rect x="6.5" y="4" width="3" '
        'height="10" rx=".5"/><rect x="12" y="1" width="3" height="13" rx=".5"/>'
        '<rect x="0" y="14.5" width="16" height="1.5"/>'),
    # A monitored hashtag.
    "gtd-hashtag-symbolic": sym(
        '<path d="M5.2 1h1.6l-.6 4h3.6l.6-4H12l-.6 4H15v1.6h-3.8l-.5 2.8H14V11h-3.5l-.6 4H8.3'
        'l.6-4H5.3l-.6 4H3.1l.6-4H1V9.4h3l.5-2.8H2V5h2.6zm.8 5.6l-.5 2.8h3.6l.5-2.8z"/>'),
    # Inbox: an open tray.
    "gtd-inbox-symbolic": sym(
        '<path d="M3.5 2h9l2.5 7v4.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 1 13.5V9zm.9 '
        '1.5L2.6 8.5h3l1 2h2.8l1-2h3L11.6 3.5z"/>'),
    # No due date: a calendar with a slash.
    "gtd-no-due-symbolic": sym(
        '<path d="M3 1h2v1.5h6V1h2v1.5h.5A1.5 1.5 0 0 1 15 4v6h-1.5V6h-11v7.5H9V15H2.5A1.5 '
        '1.5 0 0 1 1 13.5V4a1.5 1.5 0 0 1 1.5-1.5H3z"/><path d="M10 10.6l1.1-1.1 1.4 1.4 '
        '1.4-1.4 1.1 1.1-1.4 1.4 1.4 1.4-1.1 1.1-1.4-1.4-1.4 1.4-1.1-1.1 1.4-1.4z"/>'),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true")
    args = ap.parse_args()
    (ICONS / "scalable" / "apps").mkdir(parents=True, exist_ok=True)
    (ICONS / "symbolic" / "apps").mkdir(parents=True, exist_ok=True)
    (ICONS / "scalable" / "actions").mkdir(parents=True, exist_ok=True)
    (ICONS / "scalable" / "apps" / f"{APP_ID}.svg").write_text(APP_ICON, encoding="utf-8")
    (ICONS / "symbolic" / "apps" / f"{APP_ID}-symbolic.svg").write_text(SYMBOLIC_APP,
                                                                         encoding="utf-8")
    for name, svg in ACTIONS.items():
        (ICONS / "scalable" / "actions" / f"{name}.svg").write_text(svg, encoding="utf-8")
    print(f"Wrote the app icon, its symbolic variant and {len(ACTIONS)} symbolic icons.")
    if args.preview:
        if not shutil.which("rsvg-convert"):
            raise SystemExit("rsvg-convert not found (librsvg2-bin)")
        out = ROOT / "build"
        out.mkdir(exist_ok=True)
        subprocess.run(["rsvg-convert", "-w", "256", "-h", "256", "-o",
                        str(out / "icon-preview.png"),
                        str(ICONS / "scalable" / "apps" / f"{APP_ID}.svg")], check=True)
        print("Wrote build/icon-preview.png")


if __name__ == "__main__":
    main()
