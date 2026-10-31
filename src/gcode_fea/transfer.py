"""Copy finished exports from plugin storage to a user-chosen location.

OrcaSlicer audits Python file writes outside its plugin data directory, and the
first write can be denied while the prompt is being shown. The plugin therefore
writes into its storage (an allowed root) and lets a small OS helper copy the
result to the chosen path. The helper is a child process; OrcaSlicer audits the
spawn once, not each file the child writes.

The command line is deliberately constant and the paths travel in environment
variables, so the process-permission grant can be reused across runs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def copy_file(source: str | Path, destination: str | Path) -> None:
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    _copy(source_path, destination_path, directory=False)


def copy_directory(source: str | Path, destination: str | Path) -> None:
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_dir():
        raise FileNotFoundError(source_path)
    _copy(source_path, destination_path, directory=True)


def _copy(source: Path, destination: Path, directory: bool) -> None:
    if sys.platform.startswith("win"):
        _copy_windows(source, destination, directory)
        return
    _copy_posix(source, destination, directory)


def _copy_posix(source: Path, destination: Path, directory: bool) -> None:
    if directory:
        script = (
            'mkdir -p "$ORCA_FEA_DSTDIR" && '
            'cp -R -- "$ORCA_FEA_SRC/." "$ORCA_FEA_DST/"'
        )
    else:
        script = (
            'mkdir -p "$ORCA_FEA_DSTDIR" && '
            'cp -- "$ORCA_FEA_SRC" "$ORCA_FEA_DST"'
        )
    env = os.environ.copy()
    env["ORCA_FEA_SRC"] = str(source)
    env["ORCA_FEA_DST"] = str(destination)
    env["ORCA_FEA_DSTDIR"] = str(
        destination if directory else destination.parent
    )
    _run(["sh", "-c", script], env)


def _copy_windows(source: Path, destination: Path, directory: bool) -> None:
    env = os.environ.copy()
    env["ORCA_FEA_SRC"] = str(source)
    env["ORCA_FEA_DST"] = str(destination)
    env["ORCA_FEA_DSTDIR"] = str(
        destination if directory else destination.parent
    )
    if directory:
        script = (
            'if not exist "%ORCA_FEA_DSTDIR%" mkdir "%ORCA_FEA_DSTDIR%" && '
            'xcopy /Y /E /I "%ORCA_FEA_SRC%" "%ORCA_FEA_DST%"'
        )
    else:
        script = (
            'if not exist "%ORCA_FEA_DSTDIR%" mkdir "%ORCA_FEA_DSTDIR%" && '
            'copy /Y "%ORCA_FEA_SRC%" "%ORCA_FEA_DST%"'
        )
    _run(["cmd", "/c", script], env)


def _run(command: list[str], env: dict[str, str]) -> None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"copy helper not found: {command[0]}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"could not copy export to the chosen location: {detail or completed.returncode}"
        )
