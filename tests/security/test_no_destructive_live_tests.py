"""No automated test may mutate the real portal."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"

# Built at runtime so this file does not itself contain the forbidden literals.
MUTATING_CALLS = (
    "confirm" + "_booking(",
    "cancel" + "_appointment(",
    "select" + "_slot(",
)


def read_text(path: Path) -> str:
    """Read a source or fixture file, skipping binary artefacts."""
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def live_test_sources() -> list[Path]:
    return [
        path
        for path in TESTS.rglob("*.py")
        if "live" in path.name or "pytest.mark.live" in read_text(path)
    ]


def test_live_test_modules_never_mutate_the_portal() -> None:
    offenders: list[str] = []
    for path in live_test_sources():
        source = read_text(path)
        for call in MUTATING_CALLS:
            if f"client.{call}" in source:
                offenders.append(f"{path.relative_to(ROOT)} calls {call}")
    assert offenders == []


def test_live_tests_are_opt_in() -> None:
    for path in live_test_sources():
        source = read_text(path)
        assert "OPBG_LIVE_TESTS" in source, path
        assert "pytest.mark.live" in source, path


def test_no_test_targets_the_real_portal_host() -> None:
    needle = "prenotazioni." + "ospedalebambinogesu.it"
    offenders = [
        str(path.relative_to(ROOT))
        for path in TESTS.rglob("*")
        if path.is_file() and path.suffix in {".py", ".json", ".html"} and needle in read_text(path)
    ]
    assert offenders == []


def test_fixtures_contain_no_real_looking_tax_code() -> None:
    """Synthetic tax codes must not match the checksum-bearing real format."""
    pattern = re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b")
    found: set[str] = set()
    for path in (TESTS / "fixtures").rglob("*"):
        if path.is_file():
            found.update(pattern.findall(read_text(path)))
    # The synthetic codes are built from repeated letters, which no real Italian
    # tax code uses for a surname/name pair.
    for code in found:
        assert code[:6] in {"AAABBB", "CCCDDD", "EEEFFF"}, code
