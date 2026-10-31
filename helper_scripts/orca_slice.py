#!/usr/bin/env python3
"""Run the pinned OrcaSlicer 2.5 AppImage headlessly with the project fixtures."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_APPIMAGE = Path(
    "/home/nicholas/Applications/"
    "OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.5.0-beta.AppImage"
)
DEFAULT_MODEL = REPO_ROOT / "tests/fixtures/models/cube.stl"
DEFAULT_SETTINGS = REPO_ROOT / "tests/fixtures/profiles/process_headless.json"
DEFAULT_OUT = REPO_ROOT / "out"


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def load_settings(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read settings {path}: {exc}") from exc
    if data.get("type") != "process":
        raise ValueError(f"{path} is not a process preset")
    if data.get("inherits"):
        raise ValueError(
            f"{path} uses inherits; use a self-contained process fixture "
            "(2.4.2 system bundles are not loaded by 2.5)"
        )
    if "G92 E0" not in str(data.get("layer_change_gcode", "")):
        raise ValueError(f"{path} must set layer_change_gcode to G92 E0")
    if not isinstance(data.get("compatible_printers"), list):
        raise ValueError(f"{path} must set compatible_printers")
    return data


def build_argv(
    appimage: Path,
    model: Path,
    settings: Path,
    out: Path,
) -> list[str]:
    return [
        str(appimage),
        "--appimage-extract-and-run",
        "--slice",
        "0",
        "--outputdir",
        str(out),
        "--logfile",
        str(out / "orca.log"),
        "--load-settings",
        str(settings),
        str(model),
    ]


def _single_gcode(out: Path) -> Path | None:
    gcodes = sorted(out.glob("*.gcode"), key=lambda path: path.stat().st_mtime)
    if not gcodes:
        return None
    return gcodes[-1]


def _rename_single_gcode(out: Path, model: Path) -> Path | None:
    gcode = _single_gcode(out)
    if gcode is None:
        return None
    target = out / f"{model.stem}.gcode"
    if gcode == target:
        return target
    if target.exists():
        target.unlink()
    gcode.rename(target)
    return target


def verify(out: Path, model: Path) -> int:
    gcode = out / f"{model.stem}.gcode"
    if not gcode.is_file():
        gcode = _single_gcode(out)
    if gcode is None:
        print("verify: FAIL no *.gcode in output directory", file=sys.stderr)
        return 1

    text = gcode.read_text(errors="replace")
    if not text.strip():
        print(f"verify: FAIL empty G-code: {gcode}", file=sys.stderr)
        return 1

    layer_match = re.search(r"total layer number:\s*(\d+)", text)
    if layer_match is None:
        print(f"verify: FAIL no layer count in {gcode.name}", file=sys.stderr)
        return 1
    layers = int(layer_match.group(1))
    g92_count = sum(1 for line in text.splitlines() if line.strip() == "G92 E0")
    layer_change_count = sum(
        1 for line in text.splitlines() if line.strip() == ";LAYER_CHANGE"
    )

    failures: list[str] = []
    if g92_count != layers:
        failures.append(f"G92 E0 count {g92_count} != layer count {layers}")
    if layer_change_count != layers:
        failures.append(
            f"LAYER_CHANGE count {layer_change_count} != layer count {layers}"
        )

    result_path = out / "result.json"
    if not result_path.is_file():
        failures.append("missing result.json")
    else:
        try:
            result = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"invalid result.json: {exc}")
        else:
            if result.get("return_code") != 0:
                failures.append(f"result.json return_code={result.get('return_code')}")

    if failures:
        for failure in failures:
            print(f"verify: FAIL {failure}", file=sys.stderr)
        return 1

    print(
        f"verify: PASS {gcode.name} ({layers} layers, {g92_count} G92 E0, "
        f"{layer_change_count} layer changes)"
    )
    print(f"verify: artifacts {gcode} {out / 'orca.log'} {result_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the pinned OrcaSlicer 2.5 AppImage headlessly. "
            "The helper always uses --appimage-extract-and-run and removes "
            "APPIMAGE_EXTRACT_AND_RUN from the child environment."
        )
    )
    parser.add_argument("--appimage", type=Path, default=DEFAULT_APPIMAGE)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact command and write nothing",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="after slicing, check the G-code layer count and result.json",
    )
    args = parser.parse_args(argv)

    if args.dry_run and args.verify:
        return _error("--dry-run and --verify are mutually exclusive")

    appimage = args.appimage.expanduser()
    model = args.model.expanduser()
    settings = args.settings.expanduser()
    out = args.out.expanduser()

    if not appimage.is_file():
        return _error(f"missing AppImage: {appimage}")
    if not os.access(appimage, os.X_OK):
        return _error(f"AppImage is not executable: {appimage}")
    if not model.is_file():
        return _error(f"missing model: {model}")
    if not settings.is_file():
        return _error(f"missing settings: {settings}")
    try:
        load_settings(settings)
    except ValueError as exc:
        return _error(str(exc))

    command = build_argv(appimage, model, settings, out)
    if args.dry_run:
        print(shlex.join(command))
        if os.environ.get("APPIMAGE_EXTRACT_AND_RUN"):
            print(
                "# note: inherited APPIMAGE_EXTRACT_AND_RUN will be removed "
                "from the child environment",
                file=sys.stderr,
            )
        return 0

    out.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    if env.pop("APPIMAGE_EXTRACT_AND_RUN", None) is not None:
        print(
            "warning: removing inherited APPIMAGE_EXTRACT_AND_RUN; "
            "using --appimage-extract-and-run",
            file=sys.stderr,
        )

    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        return completed.returncode

    gcode = _rename_single_gcode(out, model)
    if gcode is None:
        return _error(f"slice finished but no G-code was produced in {out}")
    print(f"slice: {gcode} ({gcode.stat().st_size} bytes)")
    print(f"slice: log {out / 'orca.log'}")
    print(f"slice: result {out / 'result.json'}")

    if args.verify:
        return verify(out, model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
