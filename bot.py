#!/usr/bin/env python3
"""Compatibility entry point for Render services configured as `python bot.py`.

The maintained application lives in the `driveup` package. Prefer `python main.py`
or `python -m driveup`; this shim keeps older Render Start Commands working.
"""
from driveup.main import main

if __name__ == "__main__":
    raise SystemExit(main())
