"""UI smoke test: import every front-end module, then drive the real window
over a copy of sample-workspace (tools/screenshots.py --smoke), which also
checks that the actions changed the workspace as the CLI would.

Skipped when GTK 4 / libadwaita is not installed, or when there is no
display and xvfb-run is not available.
"""

import importlib
import os
import pkgutil
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk  # noqa: F401
    HAVE_GTK = True
except (ImportError, ValueError):
    HAVE_GTK = False


@unittest.skipUnless(HAVE_GTK, "GTK 4 / libadwaita not available")
class UISmoke(unittest.TestCase):
    def test_import_every_module(self):
        import qdvc.gtk4
        for mod in pkgutil.iter_modules(qdvc.gtk4.__path__):
            importlib.import_module(f"qdvc.gtk4.{mod.name}")

    def test_drive_the_window(self):
        cmd = [sys.executable, str(ROOT / "tools" / "screenshots.py"), "--smoke"]
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            if not shutil.which("xvfb-run"):
                self.skipTest("no display and no xvfb-run")
            cmd = ["xvfb-run", "-a"] + cmd
        env = dict(os.environ, GSK_RENDERER="cairo")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(res.returncode, 0, res.stdout[-3000:] + res.stderr[-3000:])


if __name__ == "__main__":
    unittest.main()
