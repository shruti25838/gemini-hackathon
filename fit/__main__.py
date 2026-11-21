"""Entry point for `python -m fit`."""

from __future__ import annotations

import sys

from fit.cli import main

if __name__ == "__main__":
    sys.exit(main())
