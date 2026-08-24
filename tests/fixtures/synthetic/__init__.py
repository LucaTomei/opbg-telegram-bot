"""Synthetic, sanitised portal fixtures.

Nothing here comes from a real capture: names, identifiers, tax codes and
tokens are invented and structurally equivalent to what the portal returns.
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).parent


def load(name: str) -> str:
    """Read a fixture file as text."""
    return (FIXTURES / name).read_text(encoding="utf-8")
