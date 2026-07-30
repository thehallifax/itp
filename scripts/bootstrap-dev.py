#!/usr/bin/env python3
"""Create an idempotent contributor environment with ITP's dev dependencies."""
from __future__ import annotations

import sys
from pathlib import Path

from bootstrap import BootstrapError, ensure_environment


def main():
    root = Path(__file__).resolve().parents[1]
    try:
        python, _ = ensure_environment(root, development=True)
    except BootstrapError as exc:
        print(f"ITP developer bootstrap error: {exc}.", file=sys.stderr)
        return 1
    print("ITP developer environment is ready.")
    if sys.platform == "win32":
        print(r"Activate it with: .\.venv\Scripts\Activate.ps1")
    else:
        print("Activate it with: source .venv/bin/activate")
    print(f"Python: {python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
