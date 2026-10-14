"""python -m kramers  [--run [--dry-run] [--jobs a,b]]"""
import sys

if "--run" in sys.argv[1:]:
    from .runner import main as run_main
    raise SystemExit(run_main(sys.argv[1:]))

from .gui import main

main()
