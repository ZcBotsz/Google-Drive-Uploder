#!/usr/bin/env python3
"""Verify that every knob in config.py is documented in .env.example.

A variable that exists in the code but not in `.env.example` is invisible to
whoever deploys this — and one that is in `.env.example` but not in the code
makes the docs lie. This script fails on either mismatch, so it can gate CI.

    python3 scripts/check_env.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Names accepted in config.py as aliases but not documented on purpose
# (legacy spellings kept for compatibility with older deployments).
ALIASES = {"API_ID", "API_HASH", "TELEGRAM_API_ID", "TELEGRAM_API_HASH",
           "BRAND_NAME", "TELEGRAM_BOT_TOKEN"}


def env_names_in(path: pathlib.Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    pat = r'_\w+\("([A-Z][A-Z0-9_]+)"'      # _s("X") and _s("X", default)
    return set(re.findall(pat, text))


def main() -> int:
    code = env_names_in(ROOT / "driveup" / "config.py") - ALIASES
    example_file = ROOT / ".env.example"
    example = {m.group(1) for m in re.finditer(r"^([A-Z][A-Z0-9_]+)=",
                                               example_file.read_text(), re.M)}
    missing = sorted(code - example)
    extra = sorted(example - code - ALIASES)
    print(f"config.py : {len(code)} variables")
    print(f".env.example : {len(example)} variables")
    for name in missing:
        print(f"  MISSING from .env.example : {name}")
    for name in extra:
        print(f"  UNUSED in .env.example    : {name}")
    if missing or extra:
        return 1
    print("OK — every variable matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
