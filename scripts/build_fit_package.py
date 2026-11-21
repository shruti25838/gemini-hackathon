"""Build a complete, standalone wheel for the fit engine.

    python scripts/build_fit_package.py            -> dist/ethyneon_fit-*.whl
    python scripts/build_fit_package.py --verify    also installs it into a
                                                    throwaway venv and runs a
                                                    golden case through it

Why this exists. `fit/` imports the shared `contracts` models, which live in a
sibling directory. A build backend will not reach above its own project
directory, so `pip install ./fit` produces a wheel containing `fit` alone.
That is fine inside this repository, where `contracts` is already importable,
and useless in a bare sandbox.

So the two packages are staged into one temporary directory with a generated
pyproject, and the wheel is built from there. The metadata is read from
fit/pyproject.toml, which stays the single source of truth for the version
and the dependencies.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIT_DIR = REPO_ROOT / "fit"
CONTRACTS_DIR = REPO_ROOT / "contracts"
DIST_DIR = REPO_ROOT / "dist"

STAGED_PYPROJECT = """\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ethyneon-fit"
version = "{version}"
description = "Room-fit geometry for Ethyneon. Pure Python, no web, database or network."
requires-python = ">=3.10"
dependencies = ["pydantic>=2.9,<3"]

[project.scripts]
ethyneon-fit = "fit.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["fit", "contracts"]
"""


def read_version() -> str:
    """Take the version from fit/pyproject.toml rather than repeating it."""
    text = (FIT_DIR / "pyproject.toml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("could not read the version from fit/pyproject.toml")


def stage(target: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "pyproject.toml", "dist")
    shutil.copytree(FIT_DIR, target / "fit", ignore=ignore)
    shutil.copytree(CONTRACTS_DIR, target / "contracts", ignore=ignore)
    (target / "pyproject.toml").write_text(
        STAGED_PYPROJECT.format(version=read_version()), encoding="utf-8"
    )
    readme = FIT_DIR / "README.md"
    if readme.is_file():
        shutil.copy2(readme, target / "README.md")


def build(staging: Path) -> Path:
    DIST_DIR.mkdir(exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(DIST_DIR), str(staging)],
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(DIST_DIR.glob("ethyneon_fit-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise SystemExit("the build produced no wheel")
    return wheels[-1]


def verify(wheel: Path) -> int:
    """Install into a clean venv and run a golden case through the CLI.

    This is the only claim worth making about a package: that someone who
    has nothing but the wheel can actually run it.
    """
    import json

    case = REPO_ROOT / "fixtures" / "fit_cases" / "does_not_fit_door_swing.json"
    payload = json.loads(case.read_text(encoding="utf-8"))["input"]

    with tempfile.TemporaryDirectory() as temp:
        temp_path = Path(temp)
        venv_dir = temp_path / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

        python = venv_dir / "Scripts" / "python.exe"
        if not python.exists():
            python = venv_dir / "bin" / "python"

        print("  installing the wheel into a clean venv...")
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", str(wheel)],
            check=True,
            capture_output=True,
            text=True,
        )

        # Nothing from this repository is importable in there.
        installed = subprocess.run(
            [str(python), "-c", "import fit, contracts; print(fit.SOLVER_VERSION)"],
            capture_output=True,
            text=True,
            cwd=temp_path,
        )
        if installed.returncode != 0:
            print("  FAILED to import from the installed wheel:")
            print(installed.stderr)
            return 1
        print(f"  imports cleanly, solver {installed.stdout.strip()}")

        case_file = temp_path / "case.json"
        case_file.write_text(json.dumps(payload), encoding="utf-8")
        run = subprocess.run(
            [str(python), "-m", "fit", "check", "--input", str(case_file)],
            capture_output=True,
            text=True,
            cwd=temp_path,
        )
        if run.returncode != 0:
            print(f"  FAILED to run the CLI (exit {run.returncode}):")
            print(run.stderr)
            return 1

        document = json.loads(run.stdout)
        result = document["fit_report"]["result"]
        print(f"  ran a golden case from a bare venv: {result}")
        if result != "does_not_fit":
            print("  WRONG result from the installed wheel")
            return 1

        # And the console script, which is the other advertised entry point.
        script = venv_dir / "Scripts" / "ethyneon-fit.exe"
        if not script.exists():
            script = venv_dir / "bin" / "ethyneon-fit"
        if script.exists():
            entry = subprocess.run(
                [str(script), "version"], capture_output=True, text=True, cwd=temp_path
            )
            print(f"  console script: {entry.stdout.strip() or entry.stderr.strip()}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Install the wheel into a throwaway venv and run a golden case.",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as temp:
        staging = Path(temp) / "pkg"
        staging.mkdir()
        stage(staging)
        wheel = build(staging)

    print(f"built {wheel.relative_to(REPO_ROOT)}")
    print("  contains: fit, contracts")
    print("  requires: pydantic")

    if args.verify:
        print("verifying in a clean environment:")
        return verify(wheel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
