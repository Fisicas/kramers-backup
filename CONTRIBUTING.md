# Contributing

Thanks for helping. Kramers is small on purpose, and these rules keep it that way.

## Setup

```
git clone https://github.com/Fisicas/kramers-backup
cd kramers-backup
python -m pip install sv-ttk          # the only runtime dependency
python -X utf8 -m unittest discover -s tests -v
pythonw Kramers.pyw                    # run the app from the checkout
```

When you try the app, set `KRAMERS_CONFIG` to a scratch path, for example `set KRAMERS_CONFIG=%TEMP%\kramers-dev\config.json`. Your real settings in `%APPDATA%\Kramers` then stay untouched. Use a scratch destination folder too.

## Ground rules

- **Data safety first.** Every change to the engine needs a test. Deleting anything at a destination must stay behind the guards in `engine.run`.
- **Standard library only** at run time, except `sv-ttk`. Windows integration uses `ctypes`. Tools in `tools/` may use Pillow.
- **No personal data in the repository:** no real paths, user names or drive serials. Screenshots come from `tools/make_screenshots.py`, which uses demo data.
- **Windows first, portable engine.** `engine.py`, `config.py` and `presets.py` must import and pass the tests on Linux as well (CI checks this).
- Keep the UI responsive. Long work belongs in the worker process (`worker.py`), never in the Tk thread.

## Useful commands

```
ruff check --select F,E9 kramers tests tools Kramers.pyw
python -X utf8 tools/make_identity.py --concept A     # icon, splash, social preview (Pillow)
python -X utf8 tools/make_screenshots.py              # README screenshots (sv-ttk + Pillow)
python -m build                                       # wheel + sdist into dist/
```

## Design notes

See [docs/DESIGN.md](docs/DESIGN.md).
