#!/usr/bin/env python3
"""Compatibility entrypoint for callers of the former PR Cycle CLI."""

from forge_loop import _main


if __name__ == "__main__":
    raise SystemExit(_main())
