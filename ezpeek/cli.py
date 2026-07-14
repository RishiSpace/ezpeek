from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    """
    Entrypoint for `ezpeek` / `python -m ezpeek`.

    Commands:
      (default)     Start the Qt GUI
      self-test     Headless LAN path check (SRT + control on localhost)
      --test-pattern GUI host uses synthetic testsrc (no screen capture)
    """
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(prog="ezpeek", description="LAN remote desktop (Phase 1)")
    parser.add_argument(
        "command",
        nargs="?",
        default="gui",
        choices=["gui", "self-test"],
        help="gui (default) or self-test",
    )
    parser.add_argument(
        "--test-pattern",
        action="store_true",
        help="Host with a synthetic test pattern instead of screen capture",
    )
    args = parser.parse_args(argv)

    if args.command == "self-test":
        from ezpeek.core.lan_self_test import run_self_test

        return run_self_test()

    from ezpeek.gui.main import run_gui

    return run_gui(test_pattern=args.test_pattern)


if __name__ == "__main__":
    raise SystemExit(main())
