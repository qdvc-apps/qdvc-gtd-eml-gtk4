"""Model tests: the workspace actions' refusals and edges, the loader's
cache, and the views' rules — all on temporary workspaces, no display."""

import os
import shutil
import stat
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from qdvc import folders as F
from qdvc import records, views, workspace_config
from qdvc.dates import DateFormatter
from qdvc.workspace import GTDError, Workspace

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample-workspace"
TODAY = date(2026, 9, 30)
NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


class TempWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "ws")
        shutil.copytree(SAMPLE, self.root)
        self.ws = Workspace(self.root, today=lambda: TODAY)
        self.cfg, err = workspace_config.load(self.root)
        self.assertIsNone(err)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def load(self):
        return records.RecordLoader(cache_dir=os.path.join(self.tmp, "cache")).load(
            self.ws, self.cfg, NOW)


class Actions(TempWorkspace):
    def test_alloc_refuses_to_overwrite_a_stamp(self):
        name = "2026-09-30-project-pudding-budget.eml"   # in 03-actionable, ds_triage set
        err = self.ws.alloc_refusal(name, F.TRIAGE)
        self.assertIsInstance(err, GTDError)
        self.assertIn("already has ds_triage = 2026-09-30", err.message)
        before = Path(self.ws.metadata_path).read_bytes()
        with self.assertRaises(GTDError):
            self.ws.alloc(name, F.TRIAGE)
        self.assertEqual(Path(self.ws.metadata_path).read_bytes(), before)
        self.assertEqual(self.ws.find(name)[0], F.ACTIONABLE)

    def test_alloc_same_folder_is_a_no_op(self):
        self.assertEqual(self.ws.alloc("2026-09-14-smart-quotes.eml", F.TRIAGE), "already-there")

    def test_close_refuses_archived_and_missing(self):
        with self.assertRaises(GTDError) as e:
            self.ws.close("2026-09-21-re-lunch.eml", "2026-09-14-smart-quotes.eml")
        self.assertIn("refusing to close it again", e.exception.message)
        with self.assertRaises(GTDError) as e:
            self.ws.close("2026-09-14-smart-quotes.eml", "missing.eml")
        self.assertIn("nothing was changed", e.exception.message)
        self.assertEqual(self.ws.find("2026-09-14-smart-quotes.eml")[0], F.TRIAGE)

    def test_due_date_warning_and_editable_fields(self):
        w = self.ws.set_fields("2026-09-14-smart-quotes.eml", {"due_date": "end of Q3"})
        self.assertEqual(len(w), 1)
        self.assertEqual(self.ws.set_fields("2026-09-14-smart-quotes.eml",
                                            {"due_date": "2026-10-01"}), [])
        with self.assertRaises(GTDError):
            self.ws.set_fields("2026-09-14-smart-quotes.eml", {"ds_triage": "2026-01-01"})

    def test_write_is_atomic_and_keeps_mode(self):
        os.chmod(self.ws.metadata_path, 0o640)
        self.ws.set_flag("2026-09-14-smart-quotes.eml", "pinned", True)
        mode = stat.S_IMODE(os.stat(self.ws.metadata_path).st_mode)
        self.assertEqual(mode, 0o640)
        leftovers = [n for n in os.listdir(self.root) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertFalse(self.ws.set_flag("2026-09-14-smart-quotes.eml", "pinned", True))

    def test_import_never_overwrites(self):
        src = os.path.join(self.tmp, "new-arrival.eml")
        shutil.copyfile(os.path.join(self.root, "01-input", "new-arrival.eml"), src)
        added = self.ws.import_to_input([src, src, os.path.join(self.tmp, "x.txt")])
        self.assertEqual(added, ["new-arrival-2.eml", "new-arrival-3.eml"])

    def test_ingest_and_autofix_round_trip(self):
        moved = self.ws.ingest(self.cfg.max_filename_chars)
        self.assertEqual(len(moved), 1)
        self.assertEqual(self.ws.list_eml(F.INPUT), [])
        plan = self.ws.plan_autofix()
        self.assertEqual(plan.pending_input, 0)
        self.ws.apply_autofix(plan)
        self.assertTrue(self.ws.plan_autofix().is_clean)

    def test_autofix_refuses_while_input_has_files(self):
        plan = self.ws.plan_autofix()
        self.assertEqual(plan.pending_input, 1)
        with self.assertRaises(GTDError):
            self.ws.apply_autofix(plan)

    def test_metadata_check_preview_changes_nothing(self):
        with open(self.ws.metadata_path, "a", encoding="utf-8", newline="") as f:
            f.write("gone.eml,,,Closed with nowhere.eml,,,,,,,,\r\n")
        before = Path(self.ws.metadata_path).read_bytes()
        report = self.ws.preview_metadata_check()
        self.assertEqual(report.missing_files, ["gone.eml"])
        self.assertIn(("gone.eml", "nowhere.eml"), report.dangling_refs)
        self.assertEqual(Path(self.ws.metadata_path).read_bytes(), before)
        self.ws.metadata_check()
        self.assertNotIn("gone.eml", self.ws.load_metadata())


class LoaderAndViews(TempWorkspace):
    def test_cache_survives_a_move_and_reloads(self):
        cache = os.path.join(self.tmp, "cache")
        loader = records.RecordLoader(cache_dir=cache)
        loader.attach(self.root)
        loader.load(self.ws, self.cfg, NOW)
        index = [n for n in os.listdir(cache) if n.startswith("index-")]
        self.assertEqual(len(index), 1)
        self.ws.alloc("2026-09-14-smart-quotes.eml", F.ACTIONABLE)
        fresh = records.RecordLoader(cache_dir=cache)
        fresh.attach(self.root)
        self.assertTrue(fresh._cache)            # read back from disk
        snap = fresh.load(self.ws, self.cfg, NOW)
        moved = [r for r in snap.records if r.filename == "2026-09-14-smart-quotes.eml"]
        self.assertEqual(moved[0].folder, F.ACTIONABLE)
        self.assertEqual(moved[0].subject, "Smart quotes")

    def test_members_and_radar(self):
        snap = self.load()
        overview = records.Overview(snap.records)
        radar = {F.INPUT, F.TRIAGE, F.ACTIONABLE, F.DELEGATED}
        triage = views.members(("folder", "triage"), snap.records, radar)
        self.assertEqual(len(triage), overview.counts["triage"])
        due = views.members(("due",), snap.records, radar)
        self.assertEqual([r.filename for r in due], ["2026-09-30-project-pudding-budget.eml"])
        inbox = views.members(("inbox", None), snap.records, set())   # ignores the radar
        self.assertTrue(inbox)
        tag = views.members(("tag", "#URGENT"), snap.records, radar)
        self.assertEqual(len(tag), 1)

    def test_sort_and_groups(self):
        snap = self.load()
        ordered = views.sort_records(snap.records, "date", False)
        stamps = [r.date.timestamp() for r in ordered]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        fmt = DateFormatter("long", "UTC", "local")
        groups = views.groups(ordered, "date", True, fmt.day, TODAY)
        self.assertEqual(groups[0][0], "Today")
        self.assertEqual(views.groups(ordered, "subject", True, fmt.day, TODAY)[0][0], "")

    def test_date_buckets(self):
        b = records.date_bucket
        self.assertEqual(b(date(2026, 9, 29), TODAY), "Yesterday")
        self.assertEqual(b(date(2026, 9, 28), TODAY), "This Week")      # Monday
        self.assertEqual(b(date(2026, 9, 21), TODAY), "Last Week")
        self.assertEqual(b(date(2026, 9, 2), TODAY), "Earlier This Month")
        self.assertEqual(b(date(2026, 8, 31), TODAY), "Last Month")
        self.assertEqual(b(date(2026, 3, 1), TODAY), "March")
        self.assertEqual(b(date(2025, 3, 1), TODAY), "2025")

    def test_move_refusal_wording(self):
        snap = self.load()
        r = next(x for x in snap.records if x.folder == F.INPUT)
        self.assertEqual(views.move_refusal(r, F.TRIAGE, self.ws, snap.rows), "Ingest it first")
        r = next(x for x in snap.records if x.filename.startswith("2026-09-30-project"))
        self.assertEqual(views.move_refusal(r, F.TRIAGE, self.ws, snap.rows),
                         "Already has ds_triage = 2026-09-30")
        self.assertIsNone(views.move_refusal(r, F.DELEGATED, self.ws, snap.rows))


class Dates(unittest.TestCase):
    def test_quoted_dates(self):
        fmt = DateFormatter("iso", "Europe/London", "local")
        self.assertEqual(fmt.quoted({"y": 2026, "mo": 8, "d": 3, "h": 12, "mi": 34}, "x"),
                         "2026-08-03 12:34")
        # A stated offset wins: 12:34 +0000 is 13:34 in London (BST).
        self.assertEqual(fmt.quoted({"y": 2026, "mo": 8, "d": 3, "h": 12, "mi": 34,
                                     "offset": 0}, "x"), "2026-08-03 13:34")
        utc = DateFormatter("iso", "Europe/London", "utc")
        self.assertEqual(utc.quoted({"y": 2026, "mo": 8, "d": 3, "h": 23, "mi": 30}, "x"),
                         "2026-08-04 00:30")
        self.assertEqual(utc.quoted_day({"y": 2026, "mo": 8, "d": 3, "h": 23, "mi": 30}),
                         date(2026, 8, 4))
        self.assertEqual(fmt.quoted({"y": 2026, "mo": 2, "d": 31}, "raw"), "raw")

    def test_day_text_passes_free_text_through(self):
        fmt = DateFormatter("medium")
        self.assertEqual(fmt.day_text("2026-10-02"), "2 Oct 2026")
        self.assertEqual(fmt.day_text("end of Q3"), "end of Q3")


class Vendored(unittest.TestCase):
    def test_gtd_modules_are_unmodified(self):
        import subprocess
        import sys
        res = subprocess.run([sys.executable, str(ROOT / "tools" / "vendor_core.py"), "--check"],
                             capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
