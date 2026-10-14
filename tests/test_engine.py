"""Engine tests:  python -X utf8 -m unittest discover -s tests -v"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kramers import engine as E  # noqa: E402


def write(p: Path, text: str = "x", mtime: float | None = None) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.src, self.dst = self.base / "src", self.base / "dst"
        self.src.mkdir()
        self.logs: list = []

    def tearDown(self):
        for p in self.base.rglob("*"):          # read-only files block cleanup on Windows
            try:
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        self.tmp.cleanup()

    def root(self, **kw) -> E.Root:
        kw.setdefault("label", "T")
        kw.setdefault("archive_dir", str(self.base / "_archive"))
        return E.Root(source=str(kw.pop("source", self.src)), dest=str(kw.pop("dest", self.dst)), **kw)

    def run_roots(self, *roots, **kw) -> E.RunResult:
        kw.setdefault("workers", 3)
        return E.run(list(roots), log=self.logs.append, **kw)


class MirrorBasics(Base):
    def test_copy_update_delete(self):
        write(self.src / "a.txt", "one")
        write(self.src / "sub" / "b.txt", "two")
        self.run_roots(self.root())
        self.assertEqual((self.dst / "sub" / "b.txt").read_text(), "two")

        write(self.src / "a.txt", "changed!", mtime=time.time() + 10)
        (self.src / "sub" / "b.txt").unlink()
        (self.src / "sub").rmdir()
        r = self.run_roots(self.root())
        self.assertEqual((self.dst / "a.txt").read_text(), "changed!")
        self.assertFalse((self.dst / "sub").exists())
        self.assertEqual((r.progress.updated, r.progress.deleted), (1, 1))

        again = self.run_roots(self.root())
        self.assertEqual(again.progress.files_total, 0)

    def test_add_update_never_deletes(self):
        write(self.dst / "keep.txt")
        write(self.src / "new.txt")
        self.run_roots(self.root(mode="add_update"))
        self.assertTrue((self.dst / "keep.txt").exists())

    def test_versioned_archives_before_overwrite_and_delete(self):
        write(self.src / "a.txt", "v1")
        write(self.src / "b.txt", "bye")
        self.run_roots(self.root(mode="versioned"))
        write(self.src / "a.txt", "v2", mtime=time.time() + 10)
        (self.src / "b.txt").unlink()
        self.run_roots(self.root(mode="versioned"))
        archived = sorted(p.name for p in (self.base / "_archive").rglob("*.txt"))
        self.assertEqual(archived, ["a.txt", "b.txt"])
        self.assertEqual((self.dst / "a.txt").read_text(), "v2")

    def test_file_root(self):
        f = write(self.base / "settings.json", "{}")
        r = self.root(source=f, dest=self.dst / "settings.json")
        self.run_roots(r)
        self.assertEqual((self.dst / "settings.json").read_text(), "{}")
        self.assertEqual(self.run_roots(r).progress.files_total, 0)

    def test_read_only_file_can_be_updated(self):
        f = write(self.src / "ro.txt", "1")
        os.chmod(f, stat.S_IREAD)
        self.run_roots(self.root())
        os.chmod(f, stat.S_IWRITE | stat.S_IREAD)
        write(f, "2", mtime=time.time() + 10)
        os.chmod(f, stat.S_IREAD)
        r = self.run_roots(self.root())
        self.assertEqual(r.progress.errors, 0)
        self.assertEqual((self.dst / "ro.txt").read_text(), "2")

    def test_stale_partials_removed(self):
        write(self.src / "a.txt")
        write(self.dst / "a.txt.kramers-partial", "junk")
        write(self.dst / "old.dmb_partial", "junk")
        self.run_roots(self.root())
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["a.txt"])


class Excludes(Base):
    def test_anchored_and_name_patterns(self):
        write(self.src / "cache" / "x")
        write(self.src / "deep" / "cache" / "y")
        write(self.src / "Thumbs.db")
        write(self.src / "keep.txt")
        self.run_roots(self.root(excludes=("/cache",)))
        self.assertFalse((self.dst / "cache").exists())
        self.assertTrue((self.dst / "deep" / "cache" / "y").exists())
        self.assertFalse((self.dst / "Thumbs.db").exists())

    def test_tool_dirs_only_at_top_level(self):
        write(self.src / "_archive" / "top")
        write(self.src / "project" / "_archive" / "nested")
        self.run_roots(self.root())
        self.assertFalse((self.dst / "_archive").exists())
        self.assertTrue((self.dst / "project" / "_archive" / "nested").exists())

    def test_only_allow_list(self):
        write(self.src / "config.json", "{}")
        write(self.src / "Cache" / "big")
        self.run_roots(self.root(only=("config.json",)))
        self.assertEqual([p.name for p in self.dst.iterdir()], ["config.json"])

    def test_excluded_backup_files_are_not_deleted(self):
        write(self.src / "a.txt")
        write(self.dst / "cache" / "old")
        self.run_roots(self.root(excludes=("/cache",)))
        self.assertTrue((self.dst / "cache" / "old").exists())


class Guards(Base):
    def test_missing_source_leaves_backup(self):
        write(self.dst / "precious.txt")
        r = self.run_roots(self.root(source=self.base / "gone"))
        self.assertTrue((self.dst / "precious.txt").exists())
        self.assertEqual(r.plans[0].skip_reason, "source not found")
        self.assertEqual(r.progress.warnings, 1)

    def test_optional_missing_source_is_quiet(self):
        r = self.run_roots(self.root(source=self.base / "gone", optional=True))
        self.assertEqual(r.progress.warnings, 0)

    def test_empty_source_never_deletes(self):
        write(self.dst / "precious.txt")
        self.run_roots(self.root())
        self.assertTrue((self.dst / "precious.txt").exists())

    def _many(self, n_keep: int, n_gone: int):
        for i in range(n_keep):
            write(self.src / f"k{i}.txt")
            write(self.dst / f"k{i}.txt")
        for i in range(n_gone):
            write(self.dst / f"g{i}.txt")
        self.run_roots(self.root(mode="add_update"))     # sync the keepers' times

    def test_mass_deletion_needs_confirmation(self):
        self._many(10, 60)
        guard = E.Guard(fraction=0.2, min_files=50, max_files=5000)
        r = self.run_roots(self.root(), guard=guard)               # unattended: no confirm
        self.assertTrue(r.plans[0].deletes_blocked)
        self.assertEqual(len(list(self.dst.glob("g*.txt"))), 60)

        asked = []
        r = self.run_roots(self.root(), guard=guard,
                           confirm_deletes=lambda plan: asked.append(len(plan.deletes)) or True)
        self.assertEqual(asked, [60])
        self.assertEqual(len(list(self.dst.glob("g*.txt"))), 0)

    def test_small_deletions_do_not_ask(self):
        self._many(10, 5)
        r = self.run_roots(self.root(), guard=E.Guard(), confirm_deletes=lambda p: False)
        self.assertEqual(r.progress.deleted, 5)

    def test_dry_run_changes_nothing(self):
        write(self.src / "a.txt")
        write(self.dst / "old.txt")
        r = self.run_roots(self.root(), dry_run=True)
        self.assertFalse((self.dst / "a.txt").exists())
        self.assertTrue((self.dst / "old.txt").exists())
        self.assertEqual(len(r.plans[0].deletes), 1)


class Timestamps(Base):
    def test_pre_1980_times_are_clamped_and_stable(self):
        f = write(self.src / "py.typed", "")
        os.utime(f, (0, 1_788_698_152))                  # atime = 1970 (the exFAT WinError 87 case)
        r = self.run_roots(self.root())
        self.assertEqual(r.progress.errors, 0)
        self.assertGreaterEqual(os.stat(self.dst / "py.typed").st_atime, E.FAT_EPOCH)

        g = write(self.src / "ancient.txt", "old", mtime=86400)   # mtime 1970 as well
        self.run_roots(self.root())
        os.utime(self.dst / "ancient.txt", (E.FAT_EPOCH, E.FAT_EPOCH))  # what exFAT would store
        plan = E.build_plan(self.root())
        self.assertEqual(plan.updates, [])
        self.assertTrue(g.exists())


@unittest.skipUnless(os.name == "nt", "Windows byte-range locks")
class Locked(Base):
    def test_locked_file_is_skipped_not_error(self):
        import msvcrt
        write(self.src / "free.txt")
        f = write(self.src / ".lockedfile", "data")
        fh = open(f, "r+")
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 4)
        try:
            r = self.run_roots(self.root())
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 4)
            fh.close()
        self.assertEqual((r.progress.skipped, r.progress.errors), (1, 0))
        self.assertTrue((self.dst / "free.txt").exists())
        self.assertFalse((self.dst / ".lockedfile.kramers-partial").exists())


class Special(Base):
    def test_sqlite_snapshot_of_open_wal_database(self):
        db = self.src / "state.sqlite"
        con = sqlite3.connect(db)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t (v)")
        con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(500)])
        con.commit()                                      # data still sits in -wal
        try:
            r = self.run_roots(self.root(sqlite_snapshot=True))
            self.assertEqual(r.progress.errors, 0)
            self.assertFalse(any(p.name.endswith(("-wal", "-shm")) for p in self.dst.iterdir()))
            copy = sqlite3.connect(self.dst / "state.sqlite")
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM t").fetchone()[0], 500)
            copy.close()
            self.assertEqual(E.build_plan(self.root(sqlite_snapshot=True)).updates, [])

            time.sleep(0.05)
            con.execute("INSERT INTO t VALUES (1)")
            con.commit()
            future = time.time() + 10
            os.utime(str(db) + "-wal", (future, future))
            self.assertEqual(len(E.build_plan(self.root(sqlite_snapshot=True)).updates), 1)
        finally:
            con.close()

    def test_snapshot_removes_stale_sidecars_in_backup(self):
        db = self.src / "run.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t (v)")
        con.commit()
        con.close()
        write(self.dst / "run.sqlite-wal", "stale frames from an older plain copy")
        write(self.dst / "run.sqlite-shm", "stale")
        r = self.run_roots(self.root(sqlite_snapshot=True))
        self.assertEqual(r.progress.errors, 0)
        self.assertEqual(sorted(p.name for p in self.dst.iterdir()), ["run.sqlite"])

    def test_json_redaction(self):
        write(self.src / "config.json", json.dumps(
            {"locale": "en", "oauth:tokenCache": "SECRET", "nested": {"apiKey": "K", "ok": 1}}))
        write(self.src / "broken.json", "{not json")
        rules = (("config.json", (r"^oauth:", r"(?i)api[_-]?key")), ("broken.json", (r"x",)))
        r = self.run_roots(self.root(redact_json=rules))
        out = json.loads((self.dst / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(out, {"locale": "en", "nested": {"ok": 1}})
        self.assertFalse((self.dst / "broken.json").exists())   # never copied unredacted
        self.assertEqual(r.progress.skipped, 1)
        self.assertEqual(E.build_plan(self.root(redact_json=rules)).updates, [])


class Neighbourly(Base):
    def test_pause_then_resume_and_file_deleted_meanwhile(self):
        write(self.src / "keep.txt", "k")
        doomed = write(self.src / "doomed.txt", "d")
        pause = threading.Event()
        pause.set()
        out = {}
        t = threading.Thread(target=lambda: out.update(r=self.run_roots(self.root(), pause=pause)))
        t.start()
        time.sleep(0.4)
        self.assertTrue(t.is_alive())                      # waiting while paused
        self.assertFalse((self.dst / "keep.txt").exists())
        doomed.unlink()                                    # deleted while the run is paused
        pause.clear()
        t.join(10)
        p = out["r"].progress
        self.assertEqual((p.phase, p.copied, p.vanished, p.errors), ("done", 1, 1, 0))

    def test_speed_limit(self):
        write(self.src / "big.bin", "x" * (2 * 1024 * 1024))
        t0 = time.monotonic()
        self.run_roots(self.root(), max_mb_per_s=4)
        self.assertGreater(time.monotonic() - t0, 0.35)    # 2 MB at 4 MB/s

    def test_source_size_is_reported(self):
        write(self.src / "a.txt", "12345")
        write(self.src / "sub" / "b.txt", "123")
        plan = E.build_plan(self.root())
        self.assertEqual((plan.source_bytes, plan.scanned_source_files), (8, 2))


class Cancel(Base):
    def test_cancel_leaves_no_partials(self):
        for i in range(40):
            write(self.src / f"f{i}.bin", "x" * 50_000)
        ev = threading.Event()
        calls = []

        def cb(p):
            calls.append(1)
            if p.phase == "copying" and len(calls) > 5:
                ev.set()
        r = self.run_roots(self.root(), progress_cb=cb, cancel=ev)
        self.assertEqual(r.progress.phase, "cancelled")
        self.assertEqual(list(self.dst.rglob("*.kramers-partial")), [])


if __name__ == "__main__":
    unittest.main()
