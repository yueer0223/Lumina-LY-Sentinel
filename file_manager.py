"""
file_manager.py — Traverse a directory and list all .py filenames.

Demonstrates two approaches:
  - os.walk()  from the standard os library
  - Path.rglob()  from the pathlib library

Usage:
    python file_manager.py [directory]
    (defaults to the current working directory if omitted)
"""

import os
from pathlib import Path


def list_py_files_os(root_dir: str) -> list[str]:
    """Walk through root_dir with os.walk and return paths of all .py files."""
    py_files: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for f in filenames:
            if f.endswith(".py"):
                py_files.append(os.path.join(dirpath, f))
    return py_files


def list_py_files_pathlib(root_dir: str) -> list[Path]:
    """Use pathlib.Path.rglob to find every .py file under root_dir."""
    return sorted(Path(root_dir).rglob("*.py"))


def list_py_filenames_os(root_dir: str) -> list[str]:
    """Return only filenames (no full paths) via os.walk."""
    names: list[str] = []
    for _dirpath, _dirnames, filenames in os.walk(root_dir):
        for f in filenames:
            if f.endswith(".py"):
                names.append(f)
    return names


def list_py_filenames_pathlib(root_dir: str) -> list[str]:
    """Return only filenames (no full paths) via pathlib."""
    return [p.name for p in Path(root_dir).rglob("*.py")]


def main() -> None:
    """CLI entry point — print all .py files found under the given directory."""
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "."
    target_path = Path(target).resolve()

    if not target_path.is_dir():
        print(f"Error: {target} is not a valid directory.")
        sys.exit(1)

    print(f"Scanning: {target_path}\n")

    # --- os.walk approach ---
    print("── os.walk ──────────────────────────────")
    for path in list_py_files_os(str(target_path)):
        print(f"  {path}")

    # --- pathlib approach ---
    print("\n── pathlib.rglob ────────────────────────")
    for path in list_py_files_pathlib(str(target_path)):
        print(f"  {path}")

    # Summary
    os_count = len(list_py_files_os(str(target_path)))
    pl_count = len(list_py_files_pathlib(str(target_path)))
    print(f"\nFound {os_count} .py file(s) via os, {pl_count} via pathlib.")


if __name__ == "__main__":
    main()
