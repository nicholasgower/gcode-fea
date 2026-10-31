#!/usr/bin/env python3
"""Read-only environment doctor for the pinned OrcaSlicer 2.5 setup."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_APPIMAGE = Path(
    "/home/nicholas/Applications/"
    "OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage"
)
DEFAULT_CONFIG_ROOT = Path.home() / ".config/OrcaSlicer"
EXPECTED_VERSION = "2.5.0-beta"
EXPECTED_BUILD = "345efec6"
REPO_ROOT = Path(__file__).resolve().parents[1]

VERSION_RE = re.compile(
    r"Current OrcaSlicer Version\s+(\S+)\s+build\s+(\S+)"
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _pass(name: str, detail: str) -> Check:
    return Check(name, "PASS", detail)


def _warn(name: str, detail: str) -> Check:
    return Check(name, "WARN", detail)


def _fail(name: str, detail: str) -> Check:
    return Check(name, "FAIL", detail)


def check_appimage(path: Path) -> Check:
    if not path.is_file():
        return _fail("appimage", f"missing: {path}")
    if not os.access(path, os.X_OK):
        return _fail("appimage", f"not executable: {path}")
    return _pass("appimage", str(path))


def check_config_root(path: Path) -> Check:
    if not path.is_dir():
        return _fail("config_root", f"missing: {path}")
    return _pass("config_root", str(path))


def check_plugin_dir(config_root: Path) -> Check:
    path = config_root / "orca_plugins"
    if not path.is_dir():
        return _fail("plugin_dir", f"missing: {path}")
    return _pass("plugin_dir", str(path))


def _stl_dimensions(path: Path) -> tuple[float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    zs: list[float] = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.strip().split()
        if len(parts) == 4 and parts[0] == "vertex":
            try:
                x, y, z = (float(value) for value in parts[1:])
            except ValueError:
                continue
            xs.append(x)
            ys.append(y)
            zs.append(z)
    if not xs:
        return None
    return (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))


def check_fixtures(repo_root: Path) -> Check:
    settings = repo_root / "tests/fixtures/profiles/process_headless.json"
    model = repo_root / "tests/fixtures/models/cube.stl"
    if not settings.is_file():
        return _fail("fixtures", f"missing: {settings}")
    if not model.is_file():
        return _fail("fixtures", f"missing: {model}")
    try:
        data = json.loads(settings.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return _fail("fixtures", f"invalid settings JSON: {exc}")
    if data.get("type") != "process":
        return _fail("fixtures", "process_headless.json is not a process preset")
    if data.get("inherits"):
        return _fail("fixtures", "process_headless.json must not use inherits")
    if "G92 E0" not in str(data.get("layer_change_gcode", "")):
        return _fail("fixtures", "process_headless.json must set layer_change_gcode to G92 E0")
    if not isinstance(data.get("compatible_printers"), list):
        return _fail("fixtures", "process_headless.json must set compatible_printers")
    dimensions = _stl_dimensions(model)
    if dimensions is None:
        return _fail("fixtures", f"no STL vertices found in {model}")
    x, y, z = dimensions
    return _pass(
        "fixtures",
        f"process_headless.json valid; cube.stl {x:.1f} x {y:.1f} x {z:.1f} mm",
    )


def log_candidates(config_root: Path, prefix: str) -> list[Path]:
    log_dir = config_root / "log"
    if not log_dir.is_dir():
        return []
    return sorted(
        (path for path in log_dir.glob(prefix) if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def newest_log(config_root: Path, prefix: str) -> Path | None:
    candidates = log_candidates(config_root, prefix)
    return candidates[0] if candidates else None


def _first_lines(path: Path, limit: int = 200) -> Iterable[str]:
    try:
        with path.open(errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= limit:
                    break
                yield line
    except OSError:
        return


def check_launch_log(config_root: Path) -> Check:
    logs = log_candidates(config_root, "debug_*")
    if not logs:
        return _warn("launch_log", "no debug log found; launch OrcaSlicer once")
    for log in logs:
        version = build = None
        for line in _first_lines(log):
            match = VERSION_RE.search(line)
            if match:
                version, build = match.group(1), match.group(2)
                break
        if version is None or build is None:
            continue
        detail = f"{version} build {build} ({log.name})"
        if version != EXPECTED_VERSION or build != EXPECTED_BUILD:
            return _fail(
                "launch_log",
                f"{detail}; expected {EXPECTED_VERSION} build {EXPECTED_BUILD}",
            )
        return _pass("launch_log", detail)
    return _warn("launch_log", f"no version line in {len(logs)} debug log(s)")


def check_python_log(config_root: Path) -> Check:
    log = newest_log(config_root, "python_*")
    if log is None:
        return _warn("python_log", "no python_*.log found yet")
    return _pass("python_log", log.name)


def check_mcp_token() -> Check:
    if os.environ.get("ORCA_API_TOKEN", "").strip():
        return _pass("mcp_token", "ORCA_API_TOKEN is set")
    return _warn("mcp_token", "ORCA_API_TOKEN is not set (MCP tools will refuse to run)")


def run_checks(appimage: Path, config_root: Path, repo_root: Path) -> list[Check]:
    return [
        check_appimage(appimage),
        check_config_root(config_root),
        check_plugin_dir(config_root),
        check_fixtures(repo_root),
        check_launch_log(config_root),
        check_python_log(config_root),
        check_mcp_token(),
    ]


def _print_human(checks: list[Check]) -> None:
    for check in checks:
        print(f"{check.status:4}  {check.name:12}  {check.detail}")
    failures = sum(check.status == "FAIL" for check in checks)
    warnings = sum(check.status == "WARN" for check in checks)
    print(f"\n{len(checks)} checks: {failures} failure(s), {warnings} warning(s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only doctor for the pinned OrcaSlicer 2.5 environment."
    )
    parser.add_argument(
        "--appimage",
        type=Path,
        default=DEFAULT_APPIMAGE,
        help=f"path to the OrcaSlicer AppImage (default: {DEFAULT_APPIMAGE})",
    )
    parser.add_argument(
        "--config-root",
        type=Path,
        default=DEFAULT_CONFIG_ROOT,
        help=f"OrcaSlicer config root (default: {DEFAULT_CONFIG_ROOT})",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository root used to locate fixtures",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    checks = run_checks(
        args.appimage.expanduser(),
        args.config_root.expanduser(),
        args.repo_root.expanduser(),
    )
    ok = not any(check.status == "FAIL" for check in checks)
    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "checks": [asdict(check) for check in checks],
                },
                indent=2,
            )
        )
    else:
        _print_human(checks)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
