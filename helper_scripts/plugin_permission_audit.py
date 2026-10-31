#!/usr/bin/env python3
"""Static audit of the plugin's audited-write and process declarations.

Checks that the "Export STEP (viewable)" capability does not create or write the
user-chosen directory directly (the copy helper does that outside the audited
Python call), and that the process commands declared at load are exactly the
commands the helpers build.

Read-only. Exit 1 on violations.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = REPO_ROOT / "plugins" / "gcode_fea" / "gcode_fea_plugin.py"


def audit_source(text: str, source: str = "<string>") -> list[str]:
    violations: list[str] = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != "ExportSTEPViewable":
            continue
        execute = next(
            (
                item
                for item in node.body
                if isinstance(item, ast.FunctionDef) and item.name == "execute"
            ),
            None,
        )
        if execute is None:
            violations.append(f"{source}:{node.lineno}: missing execute()")
            continue
        for child in ast.walk(execute):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in ("mkdir", "write_text", "open", "copy", "copyfile"):
                continue
            target = ast.unparse(func.value)
            if "output_dir" in target or "target.parent" in target:
                violations.append(
                    f"{source}:{child.lineno}: direct {func.attr} on user path "
                    f"'{target}'; stage in plugin storage and let the copy helper "
                    "create the destination"
                )
    return violations


def audit_plugin(path: Path = PLUGIN) -> list[str]:
    return audit_source(path.read_text(), str(path))


def audit_commands() -> list[str]:
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from gcode_fea import save_dialog, transfer

    violations: list[str] = []
    commands = save_dialog.process_commands()
    if commands:
        backend = save_dialog._backend()
        expected = [
            save_dialog._save_command(
                Path("beads.step"),
                save_dialog.SAVE_TITLE,
                save_dialog.FILTER_NAME,
                save_dialog.PATTERNS,
                backend,
            ),
            save_dialog._directory_command(
                Path("beads.step"), save_dialog.DIRECTORY_TITLE, backend
            ),
        ]
        if commands != expected:
            violations.append(
                "save_dialog.process_commands() does not match the commands "
                "choose_save_file/choose_directory build"
            )

    copy_commands = transfer.process_commands()
    expected_copy = [transfer.copy_file_command(), transfer.copy_directory_command()]
    if copy_commands != expected_copy:
        violations.append(
            "transfer.process_commands() does not match copy_file/copy_directory"
        )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", type=Path, default=PLUGIN)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    try:
        violations = audit_plugin(args.plugin) + audit_commands()
    except (OSError, SyntaxError, ImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = {
        "plugin": str(args.plugin),
        "violations": violations,
        "ok": not violations,
    }
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    if violations:
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1
    print("plugin permission audit: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
