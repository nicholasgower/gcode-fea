from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "helper_scripts"))

import plugin_permission_audit  # noqa: E402

DIRECT_WRITE_SOURCE = '''
class ExportSTEPViewable:
    def execute(self):
        output_dir = target.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir
'''

STAGED_SOURCE = '''
class ExportSTEPViewable:
    def execute(self):
        staging = _staging_dir(storage, "step")
        core["copy_file"](stage_file, target)
        return target
'''


class PluginPermissionAuditTests(unittest.TestCase):
    def test_repository_plugin_is_clean(self) -> None:
        self.assertEqual(plugin_permission_audit.audit_plugin(), [])

    def test_process_commands_match_helpers(self) -> None:
        self.assertEqual(plugin_permission_audit.audit_commands(), [])

    def test_direct_user_path_write_is_flagged(self) -> None:
        violations = plugin_permission_audit.audit_source(
            DIRECT_WRITE_SOURCE, "synthetic.py"
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("direct mkdir", violations[0])
        self.assertIn("output_dir", violations[0])

    def test_staged_copy_is_clean(self) -> None:
        self.assertEqual(
            plugin_permission_audit.audit_source(STAGED_SOURCE, "synthetic.py"),
            [],
        )

    def test_cli_exit_code(self) -> None:
        self.assertEqual(plugin_permission_audit.main([]), 0)


if __name__ == "__main__":
    unittest.main()
