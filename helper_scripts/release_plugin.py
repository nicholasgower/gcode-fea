#!/usr/bin/env python3
"""Create a GitHub release for a new GCode-FEA plugin version.

The script is a release gate, not a build system:

1. Read the version from ``pyproject.toml`` and require the plugin's PEP 723
   version to match.
2. Extract that version's section from ``CHANGELOG.md``.
3. Require a clean working tree (unless ``--allow-dirty``).
4. Run the test suite (unless ``--skip-tests``).
5. Build the self-contained plugin wheel.
6. Create the GitHub release, attach the wheel, and use the changelog section
   as the release notes.

The tag defaults to ``v<version>``. If the tag does not exist on the remote,
GitHub creates it at the current commit. If it already exists, it must point at
the current commit; the release is then created for the existing tag.

Use ``--dry-run`` to validate the version, changelog, and repository state and
print the commands without running tests, building, tagging, or creating the
release.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
PYPROJECT = REPO_ROOT / "pyproject.toml"
PLUGIN = REPO_ROOT / "plugins" / "gcode_fea" / "gcode_fea_plugin.py"
DEFAULT_WHEEL_DIR = REPO_ROOT / "dist"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_plugin_wheel import build_wheel  # noqa: E402

PROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
PLUGIN_VERSION_RE = re.compile(r'^#\s*version\s*=\s*"([^"]+)"', re.MULTILINE)


def project_version(path: Path = PYPROJECT) -> str:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"no project version in {path}")
    return version.strip()


def plugin_version(path: Path = PLUGIN) -> str:
    text = path.read_text()
    match = PLUGIN_VERSION_RE.search(text)
    if not match:
        raise ValueError(f"no [tool.orcaslicer.plugin] version in {path}")
    return match.group(1).strip()


def validate_versions(version: str | None = None) -> str:
    """Return the release version, requiring metadata to agree."""

    declared = project_version()
    plugin = plugin_version()
    if declared != plugin:
        raise ValueError(
            f"version mismatch: pyproject.toml is {declared}, plugin is {plugin}; "
            "bump both before releasing"
        )
    if version is not None and version != declared:
        raise ValueError(
            f"--version {version} does not match the repository version "
            f"{declared}; bump pyproject.toml and the plugin metadata first"
        )
    return declared


def changelog_section(text: str, version: str) -> str:
    """Return the body of the ``## <version> - <date>`` section."""

    heading = re.compile(
        rf"^##\s+\[?v?{re.escape(version)}\]?(?:\s+[-–—]\s+.*)?$",
        re.MULTILINE,
    )
    match = heading.search(text)
    if match is None:
        raise ValueError(
            f"CHANGELOG.md has no section for {version}; add "
            f"`## {version} - YYYY-MM-DD` first"
        )
    start = match.end()
    next_heading = re.search(r"^##\s+", text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(text)
    body = text[start:end].strip()
    if not body:
        raise ValueError(f"CHANGELOG.md section for {version} is empty")
    return body


def git_output(*args: str, cwd: Path = REPO_ROOT) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def current_sha() -> str:
    return git_output("rev-parse", "HEAD")


def worktree_is_clean() -> bool:
    return git_output("status", "--porcelain") == ""


def remote_tag_sha(tag: str) -> str | None:
    """Return the commit a remote tag points at, peeled when annotated."""

    output = git_output(
        "ls-remote",
        "origin",
        f"refs/tags/{tag}",
        f"refs/tags/{tag}^{{}}",
    )
    peeled: str | None = None
    direct: str | None = None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref.endswith("^{}"):
            peeled = sha
        else:
            direct = sha
    return peeled or direct


def release_exists(tag: str, repo: str) -> bool:
    completed = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def repo_slug(explicit: str | None) -> str:
    if explicit:
        return explicit
    completed = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip()
    try:
        remote = git_output("remote", "get-url", "origin")
    except RuntimeError as exc:
        raise RuntimeError(
            "could not determine the GitHub repository; pass --repo OWNER/REPO"
        ) from exc
    match = re.search(r"github\.com[:/](?P<slug>[^/]+/[^/]+?)(?:\.git)?$", remote)
    if match is None:
        raise RuntimeError(
            f"origin is not a GitHub URL ({remote}); pass --repo OWNER/REPO"
        )
    return match.group("slug")


def ensure_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"required command not found: {name}")


def ensure_gh_auth() -> None:
    completed = subprocess.run(
        ["gh", "auth", "status"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            "gh is not authenticated; run `gh auth login`"
            + (f"\n{detail}" if detail else "")
        )


def run_tests() -> None:
    completed = subprocess.run(
        [
            "uv",
            "run",
            "--with",
            "manifold3d",
            "--with",
            "gmsh",
            "--with",
            "shapely",
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("tests failed; no release was created")


def release_notes(
    version: str,
    notes_file: Path | None,
) -> str:
    if notes_file is not None:
        return notes_file.expanduser().read_text().strip()
    return changelog_section(CHANGELOG.read_text(), version)


def release_command(
    tag: str,
    wheel: Path,
    repo: str,
    title: str,
    notes_path: Path,
    target: str | None,
    draft: bool,
    prerelease: bool,
) -> list[str]:
    command = [
        "gh",
        "release",
        "create",
        tag,
        str(wheel),
        "--repo",
        repo,
        "--title",
        title,
        "--notes-file",
        str(notes_path),
    ]
    if target is not None:
        command.extend(["--target", target])
    if draft:
        command.append("--draft")
    if prerelease:
        command.append("--prerelease")
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        help="release version (default: pyproject.toml; must match the plugin)",
    )
    parser.add_argument("--tag", help="release tag (default: v<version>)")
    parser.add_argument(
        "--repo",
        help="GitHub repository as OWNER/REPO (default: gh repo view)",
    )
    parser.add_argument(
        "--title",
        help="release title (default: GCode-FEA v<version>)",
    )
    parser.add_argument(
        "--wheel-dir",
        type=Path,
        default=DEFAULT_WHEEL_DIR,
        help=f"directory for the built wheel (default: {DEFAULT_WHEEL_DIR})",
    )
    parser.add_argument(
        "--notes-file",
        type=Path,
        help="use this file as release notes instead of the CHANGELOG section",
    )
    parser.add_argument("--draft", action="store_true", help="create a draft release")
    parser.add_argument(
        "--prerelease", action="store_true", help="mark the release as a prerelease"
    )
    parser.add_argument(
        "--skip-tests", action="store_true", help="do not run the test suite"
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow a dirty working tree (not recommended)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the release plan without changing anything",
    )
    args = parser.parse_args(argv)

    try:
        version = validate_versions(args.version)
        tag = args.tag or f"v{version}"
        title = args.title or f"GCode-FEA v{version}"
        wheel_dir = args.wheel_dir.expanduser()
        if not wheel_dir.is_absolute():
            wheel_dir = REPO_ROOT / wheel_dir
        notes_file = args.notes_file.expanduser() if args.notes_file else None
        if notes_file is not None and not notes_file.is_absolute():
            notes_file = REPO_ROOT / notes_file
        notes = release_notes(version, notes_file)
        if not args.allow_dirty and not worktree_is_clean():
            raise RuntimeError(
                "working tree is not clean; commit or stash changes, or pass "
                "--allow-dirty"
            )
        sha = current_sha()
        repo = repo_slug(args.repo)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    wheel = wheel_dir / f"gcode_fea_orca-{version}-py3-none-any.whl"

    if args.dry_run:
        print(f"version: {version}")
        print(f"tag: {tag}")
        print(f"repo: {repo}")
        print(f"target: {sha}")
        print(f"wheel: {wheel}")
        print("commands:")
        if not args.skip_tests:
            print(
                "  uv run --with manifold3d --with gmsh --with shapely "
                "python -m unittest discover -s tests -p \"test_*.py\""
            )
        print(f"  python3 helper_scripts/build_plugin_wheel.py --out {wheel_dir}")
        notes_label = (
            str(notes_file)
            if notes_file is not None
            else f"<changelog section for {version}>"
        )
        print(
            "  gh release create "
            f"{tag} {wheel} --repo {repo} --title {title!r} "
            f"--notes-file {notes_label}"
            + (" --draft" if args.draft else "")
            + (" --prerelease" if args.prerelease else "")
            + f" --target {sha}"
        )
        print("\nrelease notes:\n")
        print(notes)
        return 0

    try:
        ensure_tool("git")
        ensure_tool("gh")
        ensure_tool("uv")
        ensure_gh_auth()

        remote_sha = remote_tag_sha(tag)
        if remote_sha is not None and remote_sha != sha:
            raise RuntimeError(
                f"tag {tag} already exists on origin at {remote_sha}, not {sha}; "
                "choose another tag or delete the old one"
            )
        if release_exists(tag, repo):
            raise RuntimeError(f"GitHub release {tag} already exists in {repo}")
        target = None if remote_sha == sha else sha

        if args.skip_tests:
            print("skipping tests")
        else:
            print("running tests")
            run_tests()

        wheel_dir.mkdir(parents=True, exist_ok=True)
        print(f"building wheel in {wheel_dir}")
        built = build_wheel(out_dir=wheel_dir)
        if built.resolve() != wheel.resolve():
            raise RuntimeError(
                f"wheel builder produced {built.name}, expected {wheel.name}"
            )
        if not built.is_file() or built.stat().st_size == 0:
            raise RuntimeError(f"wheel is missing or empty: {built}")

        with tempfile.TemporaryDirectory(prefix="gcode-fea-release-") as tmp:
            notes_path = Path(tmp) / "release-notes.md"
            notes_path.write_text(notes + "\n")
            command = release_command(
                tag,
                built,
                repo,
                title,
                notes_path,
                target,
                args.draft,
                args.prerelease,
            )
            print("creating GitHub release")
            completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
            if completed.returncode != 0:
                raise RuntimeError(
                    "gh release create failed; the tag may have been created. "
                    "Fix the error and rerun, or finish the release manually."
                )

        try:
            subprocess.run(
                ["git", "fetch", "--tags", "origin"],
                cwd=REPO_ROOT,
                check=False,
            )
        except OSError:
            pass
        print(f"released {tag} to {repo}")
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
