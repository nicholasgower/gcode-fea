"""Native save/open dialogs for the plugin, via the desktop's own helpers.

OrcaSlicer's plugin API exposes message boxes, HTML dialogs, progress dialogs,
and panels, but no native file chooser. A plugin also must not import its own
GUI toolkit. This module therefore asks the desktop's standard dialog tool in a
separate process:

- Linux: ``zenity`` (GNOME/GTK) or ``kdialog`` (KDE),
- macOS: ``osascript`` with ``choose file name`` / ``choose folder``,
- Windows: PowerShell with ``System.Windows.Forms`` dialogs.

If no helper is available, the caller gets ``backend="none"`` and can fall back
to its configured path. Spawning the helper is audited by OrcaSlicer once.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DialogResult:
    path: Path | None
    cancelled: bool
    backend: str
    message: str = ""

    @property
    def available(self) -> bool:
        return self.backend not in ("none", "error")


def choose_save_file(
    default_path: str | Path,
    title: str = "Save file",
    filter_name: str = "STEP files",
    patterns: tuple[str, ...] = ("*.step", "*.stp"),
    timeout: float | None = None,
) -> DialogResult:
    """Show a native Save As dialog and return the chosen path."""

    target = Path(default_path)
    suffix = Path(patterns[0]).suffix if patterns else ""
    backend = _backend()
    if backend == "none":
        return DialogResult(None, False, "none", "no native dialog helper found")

    if backend == "zenity":
        command = [
            "zenity",
            "--file-selection",
            "--save",
            "--confirm-overwrite",
            "--title",
            title,
            "--filename",
            str(target),
        ]
        if patterns:
            command.extend(
                ["--file-filter", f"{filter_name} | {' '.join(patterns)}"]
            )
        return _run(command, backend, suffix, timeout)

    if backend == "kdialog":
        command = [
            "kdialog",
            "--getsavefilename",
            str(target),
            f"{filter_name} ({' '.join(patterns)})",
            "--title",
            title,
        ]
        return _run(command, backend, suffix, timeout)

    if backend == "macos":
        script = (
            "POSIX path of (choose file name with prompt "
            f'"{_applescript(title)}" default name "{_applescript(target.name)}")'
        )
        return _run(["osascript", "-e", script], backend, suffix, timeout)

    if backend == "windows":
        script = _powershell_save_script(target, title, filter_name, patterns)
        return _run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            backend,
            suffix,
            timeout,
        )

    return DialogResult(None, False, "error", f"unknown backend: {backend}")


def choose_directory(
    default_path: str | Path,
    title: str = "Choose a folder",
    timeout: float | None = None,
) -> DialogResult:
    """Show a native folder chooser and return the chosen directory."""

    target = Path(default_path)
    if target.suffix:
        target = target.parent
    backend = _backend()
    if backend == "none":
        return DialogResult(None, False, "none", "no native dialog helper found")

    if backend == "zenity":
        command = [
            "zenity",
            "--file-selection",
            "--directory",
            "--title",
            title,
            "--filename",
            str(target) + "/",
        ]
        return _run(command, backend, "", timeout)

    if backend == "kdialog":
        command = [
            "kdialog",
            "--getexistingdirectory",
            str(target),
            "--title",
            title,
        ]
        return _run(command, backend, "", timeout)

    if backend == "macos":
        script = (
            "POSIX path of (choose folder with prompt "
            f'"{_applescript(title)}" default location POSIX file "{_applescript(str(target))}")'
        )
        return _run(["osascript", "-e", script], backend, "", timeout)

    if backend == "windows":
        script = _powershell_folder_script(target, title)
        return _run(
            ["powershell", "-NoProfile", "-STA", "-Command", script],
            backend,
            "",
            timeout,
        )

    return DialogResult(None, False, "error", f"unknown backend: {backend}")


def _backend() -> str:
    if sys.platform.startswith("linux"):
        if shutil.which("zenity"):
            return "zenity"
        if shutil.which("kdialog"):
            return "kdialog"
        return "none"
    if sys.platform == "darwin":
        return "macos" if shutil.which("osascript") else "none"
    if sys.platform.startswith("win"):
        return "windows" if shutil.which("powershell") else "none"
    return "none"


def _run(
    command: list[str],
    backend: str,
    suffix: str,
    timeout: float | None,
) -> DialogResult:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return DialogResult(None, False, "none", f"{command[0]} not found")
    except subprocess.TimeoutExpired:
        return DialogResult(None, True, backend, "dialog timed out")
    except OSError as exc:
        return DialogResult(None, False, "error", str(exc))

    if completed.returncode != 0:
        # zenity/kdialog/osascript use exit 1 for Cancel; anything else is an
        # error but still means no path was chosen.
        return DialogResult(
            None,
            completed.returncode == 1,
            backend,
            completed.stderr.strip() if completed.returncode != 1 else "cancelled",
        )
    text = completed.stdout.strip()
    if not text:
        return DialogResult(None, True, backend, "no path returned")
    path = Path(text)
    if suffix and not path.suffix:
        path = path.with_suffix(suffix)
    return DialogResult(path, False, backend)


def _applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _powershell_save_script(
    target: Path,
    title: str,
    filter_name: str,
    patterns: tuple[str, ...],
) -> str:
    wildcards = ";".join(patterns)
    filter_text = f"{filter_name} ({wildcards})|{wildcards}|All files (*.*)|*.*"
    return (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.SaveFileDialog;"
        f"$d.Title = '{_powershell(title)}';"
        f"$d.InitialDirectory = '{_powershell(str(target.parent))}';"
        f"$d.FileName = '{_powershell(target.name)}';"
        f"$d.Filter = '{_powershell(filter_text)}';"
        "$d.OverwritePrompt = $true;"
        "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ Write-Output $d.FileName }"
    )


def _powershell_folder_script(target: Path, title: str) -> str:
    return (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
        f"$d.Description = '{_powershell(title)}';"
        f"$d.SelectedPath = '{_powershell(str(target))}';"
        "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) "
        "{ Write-Output $d.SelectedPath }"
    )


def _powershell(value: str) -> str:
    return value.replace("'", "''")
