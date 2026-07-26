"""Enable ``python -m statemediafm ...`` (and the zipapp entry point)."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
