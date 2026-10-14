"""
recipes.py -- rebuild instructions for Python environments.

Full copies of venvs/conda envs only restore cleanly to the same paths, so
alongside them Kramers writes, for every environment:

    venvs\\<name>\\requirements-freeze.txt   pip freeze --all
    venvs\\<name>\\pyvenv.cfg                which base Python it was built from
    conda-envs\\<name>\\explicit.txt         conda list --explicit --md5 (exact rebuild)
    conda-envs\\<name>\\environment.yml      conda env export (portable rebuild)

A recipe is regenerated only when its environment changed since the last
export (site-packages folder / conda-meta\\history newer than the recipe).
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
from pathlib import Path

from .presets import CONDA_ROOTS, expand_path

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
TIMEOUT_S = 300

README = """Rebuilding environments from these recipes
==========================================

venv (pip):
    <base python> -m venv C:\\path\\to\\venv
    C:\\path\\to\\venv\\Scripts\\python -m pip install -r requirements-freeze.txt

conda, exact (same OS and architecture):
    conda create -p C:\\path\\to\\env --file explicit.txt

conda, portable:
    conda env create -p C:\\path\\to\\env -f environment.yml

The full environment copies next to this folder restore faster, provided they
go back to their original paths.
"""


def _fresh(recipe: Path, source_mtime: float) -> bool:
    try:
        return recipe.stat().st_mtime >= source_mtime
    except OSError:
        return False


def _run(cmd: list) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S,
                         creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip()[-400:])
    return out.stdout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def export(out_dir: Path, log, cancel=None) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write(out_dir / "README.txt", README)
    made = 0

    for venv in sorted(glob.glob(expand_path(r"%USERPROFILE%\.venvs\*"))):
        if cancel is not None and cancel.is_set():
            return
        py = Path(venv, "Scripts", "python.exe")
        site = Path(venv, "Lib", "site-packages")
        if not py.exists() or not site.exists():
            continue
        name = Path(venv).name
        target = out_dir / "venvs" / name / "requirements-freeze.txt"
        if _fresh(target, site.stat().st_mtime):
            continue
        try:
            _write(target, _run([str(py), "-m", "pip", "freeze", "--all"]))
            shutil.copyfile(Path(venv, "pyvenv.cfg"), target.with_name("pyvenv.cfg"))
            made += 1
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            log(f"WARNING: recipe for venv {name}: {exc}")

    for cr in CONDA_ROOTS:
        base = Path(expand_path(rf"%USERPROFILE%\{cr}"))
        conda = next((c for c in (base / "Scripts" / "conda.exe", base / "condabin" / "conda.bat")
                      if c.exists()), None)
        if conda is None:
            continue
        for env in sorted(glob.glob(str(base / "envs" / "*"))):
            if cancel is not None and cancel.is_set():
                return
            hist = Path(env, "conda-meta", "history")
            if not hist.exists():
                continue
            name = Path(env).name
            target = out_dir / "conda-envs" / name / "explicit.txt"
            if _fresh(target, hist.stat().st_mtime):
                continue
            try:
                _write(target, _run([str(conda), "list", "-p", env, "--explicit", "--md5"]))
                _write(target.with_name("environment.yml"),
                       _run([str(conda), "env", "export", "-p", env]))
                made += 1
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                log(f"WARNING: recipe for conda env {name}: {exc}")
    if made:
        log(f"Environment recipes updated: {made}.")
