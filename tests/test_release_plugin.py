from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "helper_scripts"))

import release_plugin  # noqa: E402

CHANGELOG = REPO_ROOT / "CHANGELOG.md"


class ReleasePluginTests(unittest.TestCase):
    def test_versions_match(self) -> None:
        version = release_plugin.validate_versions()
        self.assertEqual(version, release_plugin.project_version())
        self.assertEqual(version, release_plugin.plugin_version())

    def test_explicit_version_must_match(self) -> None:
        with self.assertRaises(ValueError):
            release_plugin.validate_versions("9.9.9")

    def test_changelog_section_for_current_version(self) -> None:
        version = release_plugin.project_version()
        body = release_plugin.changelog_section(
            CHANGELOG.read_text(), version
        )
        self.assertIn("Initial release.", body)
        self.assertIn("### Added", body)
        self.assertNotIn("## 0.0.0", body)

    def test_changelog_section_stops_at_next_version(self) -> None:
        text = (
            "# Changelog\n\n"
            "## 2.0.0 - 2026-12-01\n\nSecond release.\n\n"
            "## 1.0.0 - 2026-11-01\n\nFirst release.\n"
        )
        self.assertEqual(
            release_plugin.changelog_section(text, "2.0.0"),
            "Second release.",
        )

    def test_missing_changelog_section(self) -> None:
        with self.assertRaises(ValueError) as context:
            release_plugin.changelog_section("# Changelog\n", "1.2.3")
        self.assertIn("no section", str(context.exception))

    def test_release_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notes = Path(tmp) / "notes.md"
            notes.write_text("notes")
            command = release_plugin.release_command(
                "v1.2.3",
                Path(tmp) / "plugin.whl",
                "owner/repo",
                "GCode-FEA v1.2.3",
                notes,
                "abc123",
                draft=True,
                prerelease=True,
            )
        self.assertEqual(command[:4], ["gh", "release", "create", "v1.2.3"])
        self.assertIn("--repo", command)
        self.assertIn("owner/repo", command)
        self.assertIn("--target", command)
        self.assertIn("--draft", command)
        self.assertIn("--prerelease", command)

    def test_release_command_omits_target_for_existing_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notes = Path(tmp) / "notes.md"
            notes.write_text("notes")
            command = release_plugin.release_command(
                "v1.2.3",
                Path(tmp) / "plugin.whl",
                "owner/repo",
                "GCode-FEA v1.2.3",
                notes,
                None,
                draft=False,
                prerelease=False,
            )
        self.assertNotIn("--target", command)
        self.assertNotIn("--draft", command)
        self.assertNotIn("--prerelease", command)

    def test_dry_run_prints_plan(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = release_plugin.main(
                [
                    "--dry-run",
                    "--allow-dirty",
                    "--skip-tests",
                    "--repo",
                    "owner/repo",
                ]
            )
        self.assertEqual(code, 0)
        output = stdout.getvalue()
        self.assertIn("gh release create", output)
        self.assertIn("owner/repo", output)
        self.assertIn("release notes:", output)

    def test_dry_run_uses_notes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            notes = Path(tmp) / "notes.md"
            notes.write_text("Custom release notes")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = release_plugin.main(
                    [
                        "--dry-run",
                        "--allow-dirty",
                        "--skip-tests",
                        "--repo",
                        "owner/repo",
                        "--notes-file",
                        str(notes),
                    ]
                )
        self.assertEqual(code, 0)
        self.assertIn("Custom release notes", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
