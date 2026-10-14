"""Kramers launcher.

    pythonw Kramers.pyw                      open the app
    pythonw Kramers.pyw --run [--dry-run]    headless backup (used by Task Scheduler)
    python  Kramers.pyw --install-shortcuts  Desktop + Start Menu shortcuts
    python  Kramers.pyw --uninstall          remove shortcuts, scheduled task (and, if asked, settings)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    args = sys.argv[1:]
    if "--run" in args:
        from kramers.runner import main as run_main
        raise SystemExit(run_main(args))
    if "--uninstall" in args:
        from kramers.uninstall import main as uninstall_main
        raise SystemExit(uninstall_main(assume_yes="--yes" in args))
    if "--install-shortcuts" in args:
        from kramers import shortcuts
        for link in shortcuts.install():
            print("created", link)
        raise SystemExit(0)
    from kramers.gui import main
    main()
