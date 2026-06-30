"""Support for `python -m ezpeek`.

This allows running the application directly as a module:
    python -m ezpeek
    ezpeek-mod/bin/python -m ezpeek
"""

from __future__ import annotations

import sys

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
