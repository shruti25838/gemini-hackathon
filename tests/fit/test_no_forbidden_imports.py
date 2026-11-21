"""fit/ must stay runnable inside a sandbox: no web, database or network imports.

This is checked by reading the source rather than by importing, so it catches a
forbidden import even inside a function body or a lazy import.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIT_DIR = REPO_ROOT / "fit"

pytestmark = pytest.mark.fit

FORBIDDEN_ROOTS = {
    "fastapi",
    "starlette",
    "uvicorn",
    "sqlite3",
    "aiosqlite",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "http",
    "google",
    "websockets",
    "backend",
    "ai",
    "dotenv",
    "pydantic_settings",
}

ALLOWED_FIRST_PARTY = {"contracts", "fit"}


def python_files() -> list[Path]:
    return sorted(FIT_DIR.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import stays inside fit/
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_fit_package_exists() -> None:
    assert FIT_DIR.is_dir()
    assert python_files(), "fit/ has no Python files"


@pytest.mark.parametrize("path", python_files(), ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path) -> None:
    offenders = imported_roots(path) & FORBIDDEN_ROOTS
    assert not offenders, (
        f"{path.relative_to(REPO_ROOT)} imports {sorted(offenders)}. "
        "fit/ must run inside the Argon and Carbon sandboxes, so it cannot "
        "depend on web, database or network packages."
    )


@pytest.mark.parametrize("path", python_files(), ids=lambda p: p.name)
def test_first_party_imports_are_contracts_only(path: Path) -> None:
    """The only first-party package fit/ may touch is contracts/."""
    import sys

    stdlib = set(sys.stdlib_module_names)
    roots = imported_roots(path)
    first_party = {
        root
        for root in roots
        if root not in stdlib and (REPO_ROOT / root).exists()
    }
    assert first_party <= ALLOWED_FIRST_PARTY, (
        f"{path.relative_to(REPO_ROOT)} imports {sorted(first_party - ALLOWED_FIRST_PARTY)}"
    )


def test_solver_version_is_pinned() -> None:
    """Every FitReport carries this, so it must be a real, stable string."""
    import fit

    assert fit.SOLVER_VERSION.startswith("fit/")
    assert fit.SOLVER_VERSION != "fit/"
