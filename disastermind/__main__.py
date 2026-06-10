"""``python -m disastermind`` entry point (PRD Group A, Step 10).

Delegates to :func:`disastermind.cli.main` so the package is runnable as a
module: ``python -m disastermind {run,simulate,verify-audit} ...``.
"""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
