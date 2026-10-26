"""Config / preset tests."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kramers import config as C, presets as P  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        os.environ["KRAMERS_CONFIG"] = str(self.base / "cfg" / "config.json")

    def tearDown(self):
        os.environ.pop("KRAMERS_CONFIG", None)
        self.tmp.cleanup()

    @unittest.skipUnless(os.name == "nt", "v1 configs hold Windows paths")
    def test_v1_migration_keeps_existing_backup_folder(self):
        v1 = {"source": r"C:\Users\x\Desktop", "dest": r"K:\Desktop Backup", "mode": "versioned",
              "schedule_time": "07:30"}
        cfg = C.migrate_v1(v1)
        self.assertEqual(cfg["schema"], 2)
        self.assertEqual(cfg["dest_root"], "K:\\")
        desk = cfg["jobs"][0]
        self.assertEqual((desk["source"], desk["subdir"], desk["mode"]),
                         (r"C:\Users\x\Desktop", "Desktop Backup", "versioned"))
        self.assertEqual(cfg["default_mode"], "mirror")
        self.assertEqual(cfg["schedule_time"], "07:30")

    def test_save_load_roundtrip_and_defaults(self):
        cfg = C.load_config()
        self.assertEqual([j["id"] for j in cfg["jobs"]][:3], ["desktop", "downloads", "endnote"])
        cfg["dest_root"] = str(self.base / "backup")
        C.save_config(cfg)
        again = C.load_config()
        self.assertEqual(again["dest_root"], cfg["dest_root"])

    def test_folder_job_roots_and_validation(self):
        src = self.base / "data"
        src.mkdir()
        cfg = json.loads(json.dumps(C.DEFAULTS))
        cfg["dest_root"] = str(self.base / "backup")
        cfg["jobs"] = [C.new_job("f", "Data", "folder", source=str(src))]
        (spec, root, on), = C.job_roots(cfg, cfg["jobs"][0])
        self.assertEqual(root.dest, str(self.base / "backup" / "Data"))
        self.assertEqual(root.mode, "mirror")
        self.assertEqual(C.validate(cfg), [])
        cfg["dest_root"] = str(src / "inside")
        self.assertTrue(C.validate(cfg))

    def test_disabled_roots_are_skipped(self):
        cfg = json.loads(json.dumps(C.DEFAULTS))
        cfg["dest_root"] = str(self.base / "backup")
        job = C.new_job("claude", "Claude", "claude")
        job["disabled_roots"] = ["science"]
        cfg["jobs"] = [job]
        keys = [s.key for s, r, on in C.job_roots(cfg, job) if on]
        self.assertNotIn("science", keys)
        self.assertIn("home", keys)

    def test_offline_destination(self):
        cfg = json.loads(json.dumps(C.DEFAULTS))
        cfg["dest_root"] = "Q:\\Nowhere" if os.name == "nt" else "/nonexistent/x"
        if os.name == "nt" and os.path.exists("Q:\\"):
            self.skipTest("Q: exists here")
        status, *_ = C.check_destination(cfg)
        self.assertEqual(status, "offline")
        res = C.run_jobs(cfg, dry_run=True)
        self.assertTrue(res.dest_offline)

    def test_second_concurrent_backup_is_skipped(self):
        src = self.base / "data"
        src.mkdir()
        (src / "a.txt").write_text("x")
        cfg = json.loads(json.dumps(C.DEFAULTS))
        cfg["dest_root"] = str(self.base / "backup")
        cfg["jobs"] = [C.new_job("f", "Data", "folder", source=str(src))]
        held = C._acquire_run_lock(Path(cfg["dest_root"]) / "_logs" / ".kramers-run.lock")
        self.assertIsNotNone(held)
        try:
            res = C.run_jobs(cfg)
            self.assertIn("already running", res.message)
            self.assertFalse((self.base / "backup" / "Data" / "a.txt").exists())
        finally:
            held.close()
        res = C.run_jobs(cfg)
        self.assertEqual(res.message, "")
        self.assertTrue((self.base / "backup" / "Data" / "a.txt").exists())

    def _folder_cfg(self):
        src = self.base / "data"
        src.mkdir(exist_ok=True)
        cfg = json.loads(json.dumps(C.DEFAULTS))
        cfg["dest_root"] = str(self.base / "backup")
        cfg["jobs"] = [C.new_job("f", "Data", "folder", source=str(src))]
        return cfg, src

    def test_restore_missing_only_then_overwrite(self):
        cfg, src = self._folder_cfg()
        (src / "a.txt").write_text("original")
        (src / "b.txt").write_text("original")
        C.run_jobs(cfg)
        (src / "a.txt").unlink()                          # lost
        (src / "b.txt").write_text("edited since")        # changed since the backup
        os.utime(src / "b.txt", (1_900_000_000, 1_900_000_000))
        preview = C.run_restore(cfg, "f", dry_run=True)
        self.assertFalse((src / "a.txt").exists())
        self.assertEqual(len(preview.plans[0].copies), 1)
        C.run_restore(cfg, "f")                           # missing files only
        self.assertEqual((src / "a.txt").read_text(), "original")
        self.assertEqual((src / "b.txt").read_text(), "edited since")
        C.run_restore(cfg, "f", overwrite=True)
        self.assertEqual((src / "b.txt").read_text(), "original")

    def test_restore_to_other_folder_never_touches_source(self):
        cfg, src = self._folder_cfg()
        (src / "a.txt").write_text("x")
        C.run_jobs(cfg)
        other = self.base / "restored"
        C.run_restore(cfg, "f", target=str(other))
        self.assertEqual((other / "Data" / "a.txt").read_text(), "x")
        self.assertEqual(sorted(p.name for p in src.iterdir()), ["a.txt"])

    @unittest.skipUnless(os.name == "nt", "the Claude preset uses Windows paths")
    def test_restore_skips_redacted_files_at_original_location(self):
        home = self.base / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "CLAUDE.md").write_text("instructions")
        (home / ".claude.json").write_text(json.dumps({"mcpServers": {}, "oauth:token": "T"}))
        saved = {k: os.environ.get(k) for k in ("USERPROFILE", "APPDATA", "LOCALAPPDATA")}
        os.environ.update(USERPROFILE=str(home), APPDATA=str(home / "ad"), LOCALAPPDATA=str(home / "lad"))
        try:
            cfg = json.loads(json.dumps(C.DEFAULTS))
            cfg["dest_root"] = str(self.base / "backup")
            cfg["jobs"] = [C.new_job("claude", "Claude", "claude")]
            C.run_jobs(cfg)
            backup_json = self.base / "backup" / "Claude" / ".claude.json"
            self.assertNotIn("oauth:token", json.loads(backup_json.read_text()))   # token stripped
            (home / ".claude.json").unlink()
            (home / ".claude" / "CLAUDE.md").unlink()
            C.run_restore(cfg, "claude")                                       # to the original place
            self.assertTrue((home / ".claude" / "CLAUDE.md").exists())
            self.assertFalse((home / ".claude.json").exists())                # redacted copy not restored
            other = self.base / "elsewhere"
            C.run_restore(cfg, "claude", target=str(other))                    # but available elsewhere
            self.assertTrue((other / "Claude" / ".claude.json").exists())
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_archive_retention(self):
        from datetime import datetime, timedelta
        cfg, _ = self._folder_cfg()
        arch = self.base / "backup" / "_archive" / "f"
        old = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d_%H-%M-%S")
        new = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d_%H-%M-%S")
        for stamp in (old, new):
            (arch / stamp / "sub").mkdir(parents=True)
            (arch / stamp / "sub" / "x.txt").write_text("x")
        (arch / "notastamp").mkdir()
        self.assertEqual(C.prune_archives(cfg), 1)
        self.assertEqual(sorted(p.name for p in arch.iterdir()), sorted([new, "notastamp"]))
        cfg["archive_keep_days"] = 0
        self.assertEqual(C.prune_archives(cfg), 0)

    def test_frequency_skips_sets_not_due(self):
        from datetime import datetime, timedelta
        cfg, src = self._folder_cfg()
        (src / "a.txt").write_text("x")
        cfg["jobs"][0]["every_days"] = 7
        due, later = C.jobs_due(cfg)
        self.assertEqual((due, later), (["f"], {}))       # never backed up -> due
        C.run_jobs(cfg)
        due, later = C.jobs_due(cfg)
        self.assertEqual(due, [])
        self.assertIn("f", later)
        due, _ = C.jobs_due(cfg, now=datetime.now() + timedelta(days=8))
        self.assertEqual(due, ["f"])
        res = C.run_jobs(cfg, ["f"])                      # an explicit request always runs
        self.assertEqual(len(res.plans), 1)

    def test_scheduled_notification_rules(self):
        from kramers import runner
        E = C.engine
        ok = E.RunResult(progress=E.Progress(phase="done", copied=3), plans=[])
        warn = E.RunResult(progress=E.Progress(phase="done", warnings=1), plans=[])
        offline = E.RunResult(progress=E.Progress(phase="done"), plans=[], dest_offline=True,
                              message="The backup drive K:\\ is not connected.")
        wrong = E.RunResult(progress=E.Progress(phase="error"), plans=[], message="different drive")
        self.assertFalse(runner.summary(ok)[3])
        self.assertTrue(runner.summary(warn)[3])
        self.assertFalse(runner.summary(offline)[3])
        self.assertTrue(runner.summary(wrong)[3])
        self.assertTrue(runner.should_notify("always", False))
        self.assertTrue(runner.should_notify("problems", True))
        self.assertFalse(runner.should_notify("problems", False))
        self.assertFalse(runner.should_notify("never", True))

    def test_scheduled_run_records_outcome_even_when_drive_missing(self):
        from kramers import runner
        cfg = json.loads(json.dumps(C.DEFAULTS))
        cfg["dest_root"] = "Q:\\Nowhere" if os.name == "nt" else "/nonexistent/x"
        if os.name == "nt" and os.path.exists("Q:\\"):
            self.skipTest("Q: exists here")
        cfg["notify_scheduled"] = "never"
        C.save_config(cfg)
        self.assertEqual(runner.main(["--run"]), 0)
        last = C.load_state()["last_scheduled"]
        self.assertIn("not connected", last["summary"])
        self.assertIn("skipped", last["title"])

    def _mass_removal_cfg(self, setting: str, keep_files: int = 10):
        src = self.base / "data"
        dest = self.base / "backup" / "Data"
        src.mkdir()
        dest.mkdir(parents=True)
        for i in range(keep_files):
            (src / f"k{i}.txt").write_text("k")
        for i in range(80):                               # far past the 20% / 50-file guard
            (dest / f"gone{i}.txt").write_text("g")
        cfg = json.loads(json.dumps(C.DEFAULTS))
        cfg.update(dest_root=str(self.base / "backup"), unattended_removals=setting,
                   notify_scheduled="never")
        cfg["jobs"] = [C.new_job("f", "Data", "folder", source=str(src))]
        C.save_config(cfg)
        return dest

    def test_unattended_run_applies_large_removals_without_asking(self):
        from kramers import runner
        dest = self._mass_removal_cfg("apply")
        self.assertEqual(runner.main(["--run"]), 0)
        self.assertEqual(sorted(p.name for p in dest.glob("*.txt")), [f"k{i}.txt" for i in range(10)])

    def test_unattended_run_can_keep_files_instead(self):
        from kramers import runner
        dest = self._mass_removal_cfg("skip")
        runner.main(["--run"])
        self.assertEqual(len(list(dest.glob("gone*.txt"))), 80)
        self.assertIn("warning", C.load_state()["last_scheduled"]["level"])

    def test_unattended_run_never_empties_backup_of_an_empty_source(self):
        from kramers import runner
        dest = self._mass_removal_cfg("apply", keep_files=0)
        runner.main(["--run"])
        self.assertEqual(len(list(dest.glob("gone*.txt"))), 80)

    def test_presets_exclude_credentials(self):
        claude_home = next(s for s in P.CLAUDE.roots if s.key == "home")
        self.assertIn("/.credentials.json", claude_home.excludes)
        codex = P.CODEX.roots[0]
        self.assertIn("/auth.json", codex.excludes)
        self.assertTrue(codex.sqlite_snapshot)
        self.assertIn("login.txt", P.endnote_preset().roots[0].excludes)

    def test_wildcards_expand_to_directories_only(self):
        envs = self.base / "envs"
        (envs / "a").mkdir(parents=True)
        (envs / "b").mkdir()
        (envs / "stray.txt").write_text("x")
        spec = P.RootSpec("venv", "env {name}", str(envs / "*"), r"envs\{name}")
        got = P._wild(spec)
        self.assertEqual([s.label for s, _ in got], ["env a", "env b"])


if __name__ == "__main__":
    unittest.main()
