"""
make_release.py -- build the release files and notes for the current version.

    python -X utf8 tools/make_release.py

Runs the tests, builds dist/kramers_backup-<version>-py3-none-any.whl and
dist/kramers_backup-<version>.tar.gz (needs the `build` package and
setuptools), and writes dist/RELEASE_NOTES.md from this version's CHANGELOG
section. Upload them with, for example:

    gh release create v<version> dist/*.whl dist/*.tar.gz --notes-file dist/RELEASE_NOTES.md
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from kramers import __version__  # noqa: E402


def notes() -> str:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(__version__)}\b.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for {__version__}")
    return m.group(1).strip() + "\n"


def main() -> int:
    subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest", "discover", "-s", "tests"],
                   cwd=REPO, check=True)
    dist = REPO / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "build", "--no-isolation", "--outdir", str(dist)],
                   cwd=REPO, check=True)
    for leftover in ("build", "kramers_backup.egg-info"):
        shutil.rmtree(REPO / leftover, ignore_errors=True)
    (dist / "RELEASE_NOTES.md").write_text(notes(), encoding="utf-8")
    for f in sorted(dist.iterdir()):
        print(f"{f.name:45s} {f.stat().st_size:>10,} bytes")
    print(f"\nTag: v{__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
