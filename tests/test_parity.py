"""Parity with the Python CLI.

tests/fixtures/parity.json is the macOS edition's fixture, produced by
running the CLI's own code (see qdvc-gtd-eml-mac/tools/make_fixtures.py). The
app's core *is* that code (qdvc/gtd_modules), so these tests mainly check the
glue this app adds: the parse cache's fields, records, and above all the
workspace actions, replayed step by step and compared byte for byte with the
CLI's metadata.csv.
"""

import base64
import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from qdvc import folders as F
from qdvc import records
from qdvc.gtd_modules import config as cli_config
from qdvc.gtd_modules import dashboard, metrics, naming
from qdvc.workspace import GTDError, Workspace
from qdvc.workspace_config import WorkspaceConfig

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "parity.json")
                     .read_text(encoding="utf-8"))
TODAY = date.fromisoformat(FIXTURE["today"])
NOW = datetime.fromtimestamp(FIXTURE["now_utc"], timezone.utc)


def _d(value):
    return date.fromisoformat(value) if value else None


class EmailParity(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.config = WorkspaceConfig(my_own_accounts=FIXTURE["accounts"])

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_emails(self):
        for case in FIXTURE["emails"]:
            with self.subTest(case["name"]):
                path = os.path.join(self.dir, "x.eml")
                Path(path).write_bytes(base64.b64decode(case["raw_base64"]))
                parsed = records.parse_file(path)
                rec = records.build_record(F.TRIAGE, "x.eml", parsed, {}, self.config, None, NOW)
                self.assertEqual(parsed.subject, case["subject"])
                self.assertEqual(parsed.from_text, case["from"])
                self.assertEqual(parsed.to_text, case["to"])
                self.assertEqual(parsed.cc_text, case["cc"])
                self.assertEqual(parsed.body, case["body"])
                self.assertEqual(parsed.preview, case["preview"])
                self.assertEqual(parsed.attachments, case["attachments"])
                self.assertEqual([(m["depth"], m["text"]) for m in parsed.thread],
                                 [(m["depth"], m["text"]) for m in case["thread"]])
                self.assertEqual(rec.correspondents, case["correspondents"])
                self.assertEqual(rec.account["display_name"] if rec.account else None,
                                 case["own_account"])
                self.assertEqual([a["display_name"] for a in rec.inbox_accounts], case["inbox"])
                self.assertEqual([a["display_name"] for a in rec.sent_accounts], case["sent"])
                self.assertEqual(parsed.date is not None, case["has_date_header"])
                if case["has_date_header"]:
                    self.assertEqual(int(parsed.date.timestamp()), case["date"]["epoch"])
                    self.assertEqual(rec.age_days, case["age_days"])


class AnalyticsParity(unittest.TestCase):
    def test_plan_autofix(self):
        fx = FIXTURE["plan_autofix"]
        fixes, blockers = metrics.plan_autofix(fx["records"], today=TODAY)
        self.assertEqual(fixes, fx["fixes"])
        self.assertEqual(blockers, fx["blockers"])

    def test_metrics(self):
        for case in FIXTURE["metrics"]:
            m = metrics.compute_email_metrics(case["filename"], case["row"])
            self.assertEqual(m["status"], case["status"])
            self.assertEqual(m["metrics"], case["metrics"])

    def test_backlog_and_flow(self):
        fx = FIXTURE["backlog"]
        computed = [metrics.compute_email_metrics(c["filename"], c["row"])
                    for c in FIXTURE["metrics"]]
        stats = dashboard.build_backlog_stats(computed, TODAY)
        # The fixture was built from its own corpus; check the shape and that
        # the functions run on ours (their values are the CLI's by definition).
        self.assertEqual(set(stats), set(fx["stats"]))
        labels, _ = dashboard.build_backlog_age_histogram(computed, TODAY)
        self.assertEqual(labels, fx["histogram"]["labels"])
        self.assertEqual(set(dashboard.build_flow_series(computed, "weekly")),
                         set(fx["weekly"]))


class ScenarioParity(unittest.TestCase):
    """Replays the CLI session recorded in the fixture with this app's
    Workspace, comparing every file and metadata.csv's bytes after each step."""

    def state(self, root):
        files = []
        for d in cli_config.ALL_DIRS:
            p = os.path.join(root, d)
            if os.path.isdir(p):
                files.extend(f"{d}/{n}" for n in sorted(os.listdir(p)))
        meta = os.path.join(root, "metadata.csv")
        csv = Path(meta).read_bytes().decode("utf-8") if os.path.exists(meta) else None
        return {"files": files, "metadata_csv": csv}

    def test_scenario(self):
        steps = FIXTURE["scenario"]
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, True)
        initial = steps[0]
        for d in cli_config.ALL_DIRS:
            os.makedirs(os.path.join(root, d))
        for rel, b64 in initial["contents"].items():
            Path(root, rel).write_bytes(base64.b64decode(b64))
        Path(root, "metadata.csv").write_bytes(
            b"eml_filename,general_notes,project,next_action\r\n"
            b"2026-01-01-hand-filed.eml,\"note, with comma\",Proj,\"Closed with gone.eml\"\r\n"
            b"vanished.eml,,,\r\n")
        self.assertEqual(self.state(root), initial["state"])
        ws = Workspace(root, today=lambda: TODAY)

        for i, step in enumerate(steps[1:], start=1):
            op, argv = step["op"], step.get("argv", [])
            with self.subTest(i=i, op=op, argv=argv):
                code = 0
                try:
                    if op == "ingest":
                        moved = ws.ingest(cli_config.DEFAULTS["max_filename_chars"])
                        self.assertEqual([list(m) for m in moved], step["moved"])
                    elif op == "alloc":
                        ws.alloc(argv[0], F.resolve(argv[1]))
                    elif op == "metadata":
                        name, _, key, _, *value = argv
                        ws.set_fields(name, {key: " ".join(value)})
                    elif op in ("pin", "unpin"):
                        ws.set_flag(argv[0], "pinned", op == "pin")
                    elif op == "close":
                        ws.close(argv[0], argv[2])
                    elif op == "metadata_check":
                        report = ws.metadata_check()
                        code = 0 if report.is_clean else 1
                    elif op == "autofix":
                        plan = ws.plan_autofix()
                        self.assertEqual(plan.fixes, step["fixes"])
                        self.assertEqual(plan.blockers, step["blockers"])
                        ws.apply_autofix(plan)
                except GTDError:
                    code = 1
                if "code" in step:
                    self.assertEqual(code, step["code"])
                self.assertEqual(self.state(root), step["state"])


class RecordsMatchSite(unittest.TestCase):
    """The app's records agree with the web UI's (site.build_email_record)."""

    def test_sample_workspace(self):
        from qdvc import workspace_config
        from qdvc.gtd_modules import site
        root = str(Path(__file__).resolve().parents[1] / "sample-workspace")
        cfg, err = workspace_config.load(root)
        self.assertIsNone(err)
        ws = Workspace(root)
        snap = records.RecordLoader(cache_dir=tempfile.mkdtemp()).load(ws, cfg, NOW)
        rows = ws.load_metadata()
        sections = {s["dir"]: s for s in site.FOLDER_SECTIONS}
        for r in snap.records:
            with self.subTest(r.id):
                m = metrics.compute_email_metrics(r.filename, rows.get(r.filename, {})) \
                    if r.tracked else None
                web = site.build_email_record(root, sections[r.folder.dir], r.filename, rows,
                                              cfg.my_own_accounts, m, NOW)
                self.assertEqual(r.subject, web["subject"])
                self.assertEqual(r.correspondents, web["correspondents"])
                self.assertEqual(r.account["display_name"] if r.account else "", web["account"])
                self.assertEqual([a["display_name"] for a in r.inbox_accounts], web["inbox"])
                self.assertEqual([a["display_name"] for a in r.sent_accounts], web["sent"])
                self.assertEqual(r.age_days, web["ageDays"])
                self.assertEqual(r.status, web["status"])
                self.assertEqual(r.flags, web["flags"])
                self.assertEqual([s[0] for s in r.stamps], [s["field"] for s in web["stamps"]])
                self.assertEqual([(m["depth"], m["text"]) for m in r.parsed.thread],
                                 [(m["depth"], m["text"]) for m in web["thread"]])


if __name__ == "__main__":
    unittest.main()
