from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """
    Entrypoint for `python -m ezpeek.cli`.
    Starts the Qt GUI and blocks until exit.
    """
    if argv is None:
        argv = sys.argv[1:]

    # Lazy import so non-GUI commands could be added later
    from ezpeek.gui.main import run_gui

    return run_gui(argv)


if __name__ == "__main__":
    raise SystemExit(main())
