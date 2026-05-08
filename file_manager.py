"""
file_manager.py — Lumina-LY project-file scanner.

Sweeps a directory tree for ``.py`` files using two strategies:

  * ``os.walk``     — the battle-hardened classic
  * ``Path.rglob``  — the pathlib clean-sweep

Usage
-----
::

    python file_manager.py [directory]
    # defaults to CWD when omitted

Both results are printed side-by-side so you can verify
they agree (they always do, but seeing is believing).
"""

import os
from pathlib import Path
from typing import Final


def list_py_files_os(root_dir: str) -> list[str]:
    """Walk through *root_dir* with ``os.walk`` and return every ``.py`` path."""
    py_files: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for f in filenames:
            if f.endswith(".py"):
                py_files.append(os.path.join(dirpath, f))
    return py_files


def list_py_files_pathlib(root_dir: str) -> list[Path]:
    """Use ``Path.rglob("*.py")`` to find every Python file under *root_dir*.

    Returns sorted :class:`pathlib.Path` objects (preserves them as
    ``Path`` so callers can chain path operations naturally).
    """
    return sorted(Path(root_dir).rglob("*.py"))


def list_py_filenames_os(root_dir: str) -> list[str]:
    """Return **only filenames** (no directory prefix) via ``os.walk``."""
    names: list[str] = []
    for _dirpath, _dirnames, filenames in os.walk(root_dir):
        for f in filenames:
            if f.endswith(".py"):
                names.append(f)
    return names


def list_py_filenames_pathlib(root_dir: str) -> list[str]:
    """Return **only filenames** via ``Path.rglob``."""
    return [p.name for p in Path(root_dir).rglob("*.py")]


def main() -> None:
    """CLI entry-point: print discovered ``.py`` files grouped by method.

    Accepts an optional directory argument from ``sys.argv``.
    Reports a count summary at the end.
    """
    import sys

    DEFAULT_TARGET: Final[str] = "."
    target: str = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TARGET
    target_path: Path = Path(target).resolve()

    if not target_path.is_dir():
        print(f"Error: {target} is not a valid directory.")
        sys.exit(1)

    print(f"Scanning: {target_path}\n")

    # ── os.walk ─────────────────────────────────────────────────
    print("── os.walk ──────────────────────────────")
    os_results: list[str] = list_py_files_os(str(target_path))
    for path in os_results:
        print(f"  {path}")

    # ── pathlib.rglob ──────────────────────────────────────────
    print("\n── pathlib.rglob ────────────────────────")
    pl_results: list[Path] = list_py_files_pathlib(str(target_path))
    for path in pl_results:
        print(f"  {path}")

    os_count: int = len(os_results)
    pl_count: int = len(pl_results)
    print(f"\nFound {os_count} .py file(s) via os, {pl_count} via pathlib.")


if __name__ == "__main__":
    main()
