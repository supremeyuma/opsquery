"""Minimal command line entry point."""

from __future__ import annotations

import argparse

from opsquery import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opsquery")
    parser.add_argument("--version", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
