"""Finding the backup drive and folder, free-space guard, folder icons."""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kramers import config as C, engine as E, folder_icons as FI  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        os.environ["KRAMERS_CONFIG"] = str(self.base / "cfg" / "config.json")

    def tearDown(self):
        os.environ.pop("KRAMERS_CONFIG", None)
        self.tmp.cleanup()

    def marker(self, folder: Path, serial="1A2B-3C4D"):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / C.MARKER).write_text(json.dumps({"app": "Kramers", "volume_serial": serial}))


class PickingAFolder(Base):
    def test_drive_like_folder_with_backup_inside_uses_it(self):
        self.marker(self.base / "Backups")
        (self.base / "Other").mkdir()
        self.assertEqual(C.find_backup_folder(str(self.base)), str(self.base / "Backups"))
        folder, reason = C.suggest_destination(str(self.base))
        self.assertEqual(folder, str(self.base / "Backups"))
        self.assertIn("existing", reason)

    def test_the_backup_folder_itself_is_kept(self):
        self.marker(self.base / "Backups")
        self.assertEqual(C.suggest_destination(str(self.base / "Backups")),
                         (str(self.base / "Backups"), ""))

    def test_two_candidate_backups_are_not_guessed(self):
        self.marker(self.base / "A")
        self.marker(self.base / "B")
        self.assertIsNone(C.find_backup_folder(str(self.base)))

    @unittest.skipUnless(os.name == "nt", "drive letters")
    def test_bare_drive_root_becomes_backups_folder(self):
        with mock.patch.object(C, "find_backup_folder", return_value=None):
            self.assertEqual(C.suggest_destination("Q:\\")[0], "Q:\\Backups")


@unittest.skipUnless(os.name == "nt", "drive letters and volume serials")
class FollowingTheDrive(Base):
    VOLS = [("C:\\", "0000-1111", "Windows"), ("K:\\", "9F8E-7D6C", "USBSTICK"),
            ("L:\\", "1A2B-3C4D", "Backup")]

    def resolve(self, root, vols=None, current=("", "")):
        cfg = {"dest_root": root, "dest_volume_serial": "1A2B-3C4D", "dest_volume_label": "Backup"}
        with mock.patch.object(C.winshell, "mounted_volumes", return_value=vols or self.VOLS), \
                mock.patch.object(C.winshell, "volume_info", return_value=current), \
                mock.patch.object(C, "find_backup_folder", return_value=None):
            note = C.resolve_destination(cfg)
        return cfg["dest_root"], note

    def test_letter_change_is_followed(self):
        root, note = self.resolve("K:\\Backups", current=("9F8E-7D6C", "USBSTICK"))
        self.assertEqual(root, "L:\\Backups")
        self.assertIn("now L:", note)

    def test_unknown_drive_with_same_label_but_no_marker_is_not_used(self):
        vols = [("M:\\", "1111-2222", "Backup")]
        root, note = self.resolve("K:\\Backups", vols=vols)
        self.assertEqual((root, note), ("K:\\Backups", ""))

    def test_same_drive_same_letter_is_left_alone(self):
        root, note = self.resolve("L:\\Backups", current=("1A2B-3C4D", "Backup"))
        self.assertEqual((root, note), ("L:\\Backups", ""))


class FreeSpace(Base):
    def test_backup_stops_before_copying_when_drive_is_too_small(self):
        src, dst = self.base / "src", self.base / "dst"
        src.mkdir()
        (src / "big.bin").write_bytes(b"x" * 4096)
        root = E.Root(source=str(src), dest=str(dst), label="T")
        tiny = mock.Mock(free=E.SPACE_MARGIN)               # nothing left above the margin
        with mock.patch.object(E.shutil, "disk_usage", return_value=tiny):
            preview = E.run([root], dry_run=True)
            res = E.run([root])
        self.assertEqual(preview.progress.phase, "done")
        self.assertEqual(preview.progress.warnings, 1)
        self.assertEqual(res.progress.phase, "error")
        self.assertIn("Not enough free space", res.message)
        self.assertFalse((dst / "big.bin").exists())


class FolderIcons(Base):
    def test_ico_container_holds_the_pngs(self):
        ico = FI.ico_from_pngs([(32, b"P32"), (256, b"P256"), (16, b"P16")])
        reserved, kind, count = struct.unpack("<HHH", ico[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 3))
        dims = [ico[6 + 16 * i] for i in range(3)]
        self.assertEqual(dims, [16, 32, 0])                  # 0 means 256 px
        self.assertTrue(ico.endswith(b"P16P32P256"))

    @unittest.skipUnless(os.name == "nt", "icon resources in executables")
    def test_program_icon_is_copied_at_native_resolution(self):
        exe = os.path.join(os.path.dirname(sys.executable), "python.exe")
        images = FI.winshell.icon_resource_images(exe, 0)
        self.assertIn(256, [s for s, _b, _d in images])
        ico = FI.native_icon("exe:" + exe)
        count = struct.unpack("<HHH", ico[:6])[2]
        self.assertEqual(count, len(images))
        big = next(d for s, _b, d in images if s == 256)
        self.assertIn(big, ico)                          # stored byte-for-byte, not resampled

    @unittest.skipUnless(os.name == "nt", "Explorer folder icons")
    def test_icon_files_survive_an_exact_mirror(self):
        src, dst = self.base / "src", self.base / "dst"
        src.mkdir()
        (src / "a.txt").write_text("a")
        root = E.Root(source=str(src), dest=str(dst), label="T")
        E.run([root])
        self.assertTrue(FI.set_folder_icon(str(dst), FI.ico_from_pngs([(16, b"png")])))
        self.assertFalse(FI.set_folder_icon(str(dst), FI.ico_from_pngs([(16, b"png")])))  # idempotent
        E.run([root])                                        # mirror must not remove them
        self.assertTrue((dst / FI.ICON_NAME).exists())
        self.assertTrue((dst / FI.INI_NAME).exists())
        self.assertIn(b"IconResource=.kramers.ico,0", (dst / FI.INI_NAME).read_bytes())


if __name__ == "__main__":
    unittest.main()
