#!/usr/bin/env python3
"""Build the self-contained GCode-FEA OrcaSlicer plugin wheel.

The wheel bundles:

- ``gcode_fea_plugin`` - the adapter (the single-file plugin source),
- ``gcode_fea`` - the core, so the adapter needs no external install,
- ``METADATA``/``WHEEL``/``RECORD`` and the license/credits files.

Only NumPy is an external dependency; OrcaSlicer's bundled ``uv`` installs it
from the PEP 723 metadata (or the wheel's ``Requires-Dist``).

Usage:
    python3 helper_scripts/build_plugin_wheel.py --out dist
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLUGIN = REPO_ROOT / "plugins" / "gcode_fea" / "gcode_fea_plugin.py"
DEFAULT_CORE = REPO_ROOT / "src" / "gcode_fea"
DIST_NAME = "gcode_fea_orca"
PACKAGE_NAME = "gcode_fea_plugin"


def build_wheel(
    plugin_path: Path = DEFAULT_PLUGIN,
    core_dir: Path = DEFAULT_CORE,
    out_dir: Path | None = None,
    version: str | None = None,
) -> Path:
    if not plugin_path.exists():
        raise FileNotFoundError(plugin_path)
    if not core_dir.is_dir():
        raise FileNotFoundError(core_dir)
    version = version or _project_version()
    out = Path(out_dir) if out_dir else REPO_ROOT / "dist"
    out.mkdir(parents=True, exist_ok=True)
    wheel_path = out / f"{DIST_NAME}-{version}-py3-none-any.whl"
    info = f"{DIST_NAME}-{version}.dist-info"

    files: dict[str, bytes] = {
        f"{PACKAGE_NAME}/__init__.py": plugin_path.read_bytes(),
    }
    for source in sorted(core_dir.rglob("*.py")):
        relative = source.relative_to(core_dir.parent)
        files[str(relative)] = source.read_bytes()

    metadata = "\n".join(
        [
            "Metadata-Version: 2.3",
            f"Name: {DIST_NAME}",
            f"Version: {version}",
            "Summary: GCode-FEA OrcaSlicer adapter and core",
            "Author: GCode-FEA",
            "License: MIT",
            "Requires-Python: >=3.12",
            "Requires-Dist: numpy>=1.26",
            "Import-Name: gcode_fea_plugin",
            "",
        ]
    )
    wheel = "\n".join(
        [
            "Wheel-Version: 1.0",
            "Generator: gcode-fea build_plugin_wheel",
            "Root-Is-Purelib: true",
            "Tag: py3-none-any",
            "",
        ]
    )
    files[f"{info}/METADATA"] = metadata.encode("utf-8")
    files[f"{info}/WHEEL"] = wheel.encode("utf-8")
    files[f"{info}/top_level.txt"] = b"gcode_fea_plugin\n"
    for extra in ("LICENSE", "CREDITS.md"):
        source = REPO_ROOT / extra
        if source.exists():
            files[f"{info}/licenses/{extra}"] = source.read_bytes()

    record_lines = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(data).digest()
        ).rstrip(b"=").decode("ascii")
        record_lines.append(f"{name},sha256={digest},{len(data)}")
    record_lines.append(f"{info}/RECORD,,")
    files[f"{info}/RECORD"] = ("\n".join(record_lines) + "\n").encode("utf-8")

    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return wheel_path


def _project_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    return str(data.get("project", {}).get("version", "0.0.0"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--plugin", type=Path, default=DEFAULT_PLUGIN)
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--version", default=None)
    args = parser.parse_args(argv)
    wheel = build_wheel(
        plugin_path=args.plugin,
        core_dir=args.core,
        out_dir=args.out,
        version=args.version,
    )
    print(f"wheel: {wheel} ({wheel.stat().st_size} bytes)")
    print(
        "install: copy the .whl into "
        "~/.config/OrcaSlicer/orca_plugins/<plugin>/ and enable it"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
