#!/usr/bin/env python3
"""Copy a plugin into the pinned OrcaSlicer 2.5 plugin directory."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

DEFAULT_CONFIG_ROOT = Path.home() / ".config/OrcaSlicer"
ENTRY_SUFFIXES = {".py", ".whl"}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MANIFEST_RE = re.compile(r"Found\s+(\d+)\s+plugin manifests?")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def validate_name(name: str) -> None:
    if not NAME_RE.fullmatch(name) or name in {".", ".."}:
        raise ValueError(
            "plugin name must contain only letters, digits, '.', '_' or '-' "
            "and must not be '.' or '..'"
        )


def entry_files(src: Path) -> list[Path]:
    return sorted(
        path
        for path in src.iterdir()
        if path.is_file() and path.suffix.lower() in ENTRY_SUFFIXES
    )


def debug_logs(config_root: Path) -> list[Path]:
    log_dir = config_root / "log"
    if not log_dir.is_dir():
        return []
    return sorted(
        (path for path in log_dir.glob("debug_*") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def last_manifest_count(log: Path) -> int | None:
    last: int | None = None
    try:
        with log.open(errors="replace") as handle:
            for line in handle:
                match = MANIFEST_RE.search(line)
                if match:
                    last = int(match.group(1))
    except OSError:
        return None
    return last


def report_discovery(config_root: Path, name: str, copied: bool) -> None:
    logs = debug_logs(config_root)
    print(
        f"next: restart OrcaSlicer (or reopen the Plugins dialog) so it scans "
        f"{config_root / 'orca_plugins' / name}"
    )
    if not logs:
        print("next: no debug log found; after launch, look for 'Found N plugin manifests'")
        return
    for log in logs:
        count = last_manifest_count(log)
        if count is not None:
            suffix = " (may predate this copy)" if copied else ""
            print(f"last discovery: {count} plugin manifest(s) in {log.name}{suffix}")
            return
    print(f"next: no discovery line yet in {logs[0].name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a plugin directory into "
            "<config-root>/orca_plugins/<name>/ and report discovery state. "
            "The helper never imports or runs plugin code."
        )
    )
    parser.add_argument("--src", type=Path, required=True, help="plugin source directory")
    parser.add_argument("--name", required=True, help="destination plugin folder name")
    parser.add_argument(
        "--config-root",
        type=Path,
        default=DEFAULT_CONFIG_ROOT,
        help=f"OrcaSlicer config root (default: {DEFAULT_CONFIG_ROOT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the planned copy and write nothing",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="overwrite an existing destination plugin directory",
    )
    args = parser.parse_args(argv)

    src = args.src.expanduser()
    config_root = args.config_root.expanduser()
    try:
        validate_name(args.name)
    except ValueError as exc:
        return _error(str(exc))

    if not src.is_dir():
        return _error(f"missing plugin source directory: {src}")
    entries = entry_files(src)
    if not entries:
        return _error(f"{src} has no .py or .whl entry file")
    if len(entries) > 1:
        names = ", ".join(path.name for path in entries)
        return _error(f"{src} has multiple entry files ({names}); expected exactly one")

    plugin_dir = config_root / "orca_plugins"
    if not plugin_dir.is_dir():
        return _error(
            f"missing {plugin_dir}; launch OrcaSlicer once to create the plugin directory"
        )
    dest = plugin_dir / args.name
    exists = dest.exists()

    if exists and not args.yes:
        return _error(
            f"destination exists: {dest}; pass --yes to overwrite it"
        )

    if args.dry_run:
        action = "overwrite" if exists else "copy"
        print(f"would {action}: {src} -> {dest}")
        print(f"entry: {entries[0].name}")
        report_discovery(config_root, args.name, copied=False)
        return 0

    if exists:
        if dest.is_symlink():
            return _error(f"refusing to replace symlink: {dest}")
        if dest.parent != plugin_dir or dest.name != args.name:
            return _error(f"refusing to replace unexpected path: {dest}")
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print(f"copied: {src} -> {dest}")
    report_discovery(config_root, args.name, copied=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
